"""정제 단계 — raw 를 검증·정규화해 clean 저장소로 옮긴다.

정제 규칙
  1) 필수 필드 검증   : url · title · body 존재, 본문 최소 길이
  2) 텍스트 정규화     : 잔여 HTML, 언론사 보일러플레이트, 공백·특수문자 정리
  3) 날짜 형식 통일    : 어떤 형식으로 들어왔든 YYYY-MM-DD 로
  4) 결측값 처리       : 카테고리·날짜 등 빈 값에 기본값 부여
  5) 중복 처리         : URL(완전 중복) + 제목 해시(유사 보도)

raw 를 지우지 않고 별도 테이블로 옮기는 이유: 정제 규칙이 바뀌어도 원본에서 다시
만들 수 있고, "수집이 잘못된 것"과 "정제가 잘못된 것"을 구분해 디버깅할 수 있다.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import unicodedata
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Config
from .db import Database
from .logger import get_logger

log = get_logger("clean")

# 본문에 섞여 들어오는 언론사 공통 잡음
BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*[\w가-힣]{2,4}\s*기자\s*(구독\s*구독중)?\s*(이전\s*다음)?\s*(이미지\s*확대)?\s*"),
    re.compile(r"\(\s*[가-힣]+=연합뉴스\s*\)\s*[\w가-힣]{2,4}\s*기자\s*=\s*"),
    re.compile(r"제보는\s*카카오톡.*$"),
    re.compile(r"<\s*저작권자.*?>\s*"),
    re.compile(r"ⓒ\s*[^\s]+\s*(무단전재|및)?.*?(금지|배포금지)\.?"),
    re.compile(r"\[[^\]]{0,20}(제공|연합뉴스|한경DB|사진)[^\]]{0,20}\]"),
    re.compile(r"(이메일|메일)\s*:?\s*[\w.\-]+@[\w.\-]+"),
    re.compile(r"[\w.\-]+@[\w.\-]+\.[a-zA-Z]{2,}"),
]

HTML_TAG = re.compile(r"<[^>]+>")
MULTI_SPACE = re.compile(r"[ \t ​]+")
MULTI_NEWLINE = re.compile(r"\n{3,}")
# 제목 해시용: 한글·영문·숫자만 남긴다
NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]+")

# URL 정규화 시 제거할 추적 파라미터
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


# ----------------------------------------------------------------------
# 정규화 유틸
# ----------------------------------------------------------------------
def normalize_text(text: str | None, *, strip_boilerplate: bool = True) -> str:
    """HTML 엔티티·태그 제거 + 유니코드 정규화 + 공백 정리.

    보일러플레이트 제거는 본문에만 적용한다. 제목에까지 적용하면
    "[연합뉴스 이 시각 헤드라인]" 같은 의미 있는 말머리까지 지워지기 때문이다.
    """
    if not text:
        return ""
    value = html.unescape(text)
    value = HTML_TAG.sub(" ", value)
    value = unicodedata.normalize("NFKC", value)
    if strip_boilerplate:
        for pattern in BOILERPLATE_PATTERNS:
            value = pattern.sub(" ", value)
    value = MULTI_SPACE.sub(" ", value)
    value = MULTI_NEWLINE.sub("\n\n", value)
    return value.strip()


def normalize_url(url: str) -> str:
    """추적 파라미터·프래그먼트를 떼어내 같은 기사를 같은 키로 만든다."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in TRACKING_PARAMS]
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, netloc, path, urlencode(query), ""))


def title_hash(title: str) -> str:
    """공백·기호를 지운 제목의 해시 — 언론사가 달라도 같은 보도면 같은 값."""
    key = NON_WORD.sub("", title or "").lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def normalize_date(value: str | None, fallback: str | None = None) -> str | None:
    """다양한 날짜 표기를 YYYY-MM-DD 로 통일한다."""
    for candidate in (value, fallback):
        if not candidate:
            continue
        text = str(candidate).strip()
        # ISO8601 (fetch 단계가 남기는 기본 형식)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
        # RFC822 등 자주 보이는 형식들
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        # 문자열 안에 YYYY-MM-DD 가 들어있는 경우
        match = re.search(r"(\d{4})[-.\/](\d{1,2})[-.\/](\d{1,2})", text)
        if match:
            y, m, d = (int(g) for g in match.groups())
            try:
                return datetime(y, m, d).date().isoformat()
            except ValueError:
                continue
    return None


# ----------------------------------------------------------------------
# 정제 본체
# ----------------------------------------------------------------------
def clean_record(row, *, min_body: int) -> tuple[dict | None, str]:
    """raw 행 하나를 정제한다. 반환: (레코드 또는 None, 사유)."""
    url = (row["url"] or "").strip()
    if not url:
        return None, "URL 없음"

    title = normalize_text(row["title"], strip_boilerplate=False)
    body = normalize_text(row["body"])

    if not title:
        return None, "제목 없음"
    if not body:
        return None, "본문 없음"
    if len(body) < min_body:
        return None, f"본문 길이 부족({len(body)}자)"

    published = normalize_date(row["published_at"], row["collected_at"])

    return (
        {
            "raw_id": row["id"],
            "url": normalize_url(url),
            "title": title,
            "body": body,
            "title_hash": title_hash(title),
            "category": row["category"] or "미분류",
            "source": row["source"] or "미상",
            "published_at": published,
        },
        "ok",
    )


def run_clean(args: argparse.Namespace, cfg: Config) -> int:
    policy = args.dedup or cfg.dedup.get("policy", "skip")
    dedup_similar = args.dedup_similar or cfg.dedup.get("dedup_similar", False)

    log.info(
        "정제 시작: 중복정책=%s, 유사보도 제거=%s, 본문 최소 %d자",
        policy,
        "on" if dedup_similar else "off",
        args.min_body,
    )

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0, "similar": 0}
    reasons: dict[str, int] = {}

    with Database(cfg.path_for("db")) as db:
        rows = db.uncleaned_raw()
        log.info("정제 대상: %d건 (raw 미처리)", len(rows))
        if not rows:
            log.info("정제할 새 데이터가 없습니다. fetch 를 먼저 실행하세요.")
            return 0

        processed_ids: list[int] = []
        for row in rows:
            record, reason = clean_record(row, min_body=args.min_body)
            processed_ids.append(row["id"])

            if record is None:
                stats["invalid"] += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                log.debug("검증 탈락 raw#%s: %s", row["id"], reason)
                continue

            # 유사 보도 판정 — 완전 중복(URL)이 아니면서 제목 해시가 겹치는 경우
            if dedup_similar and not db.url_exists(record["url"]) and db.title_hash_exists(record["title_hash"]):
                record["is_duplicate"] = 1
                stats["similar"] += 1

            result = db.insert_clean(record, policy=policy)
            stats[result] = stats.get(result, 0) + 1

        db.mark_raw_cleaned(processed_ids)

    if reasons:
        detail = ", ".join(f"{k} {v}건" for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
        log.warning("검증 탈락 %d건 — %s", stats["invalid"], detail)
    if stats["similar"]:
        log.info("유사 보도 %d건을 중복으로 표시했습니다", stats["similar"])

    log.info(
        "정제 완료: 신규 %d건, 갱신 %d건, 중복 스킵 %d건, 탈락 %d건",
        stats["inserted"], stats["updated"], stats["skipped"], stats["invalid"],
    )
    return 0
