"""AI 요약 단계.

대상 선택: --all / --id / --unsummarized(기본).
이미 요약된 기사는 기본 스킵(캐싱), API 실패는 로깅 후 스킵해 나머지 건은 계속 진행한다.
감성(보너스)은 같은 호출에서 함께 받아 저장한다.
"""

from __future__ import annotations

import argparse
import re

from ..config import Config
from ..db import Database
from ..logger import get_logger
from .client import AIClient, AIError
from .sentiment import mock_label, normalize_label

log = get_logger("summarize")

SYSTEM_PROMPT = (
    "너는 한국어 뉴스 편집자다. 기사 본문을 사실만으로 간결하게 요약한다. "
    "추측·과장·의견을 넣지 않고, 반드시 지정된 JSON 형식으로만 답한다."
)

USER_TEMPLATE = """다음 뉴스 기사를 요약해줘.

[제목] {title}
[카테고리] {category}
[본문]
{body}

요구사항:
- summary: 핵심만 담은 한국어 요약, {max_chars}자 이내, 2~3문장
- sentiment: 기사 논조를 positive / negative / neutral 중 하나로

아래 JSON 형식으로만 답할 것:
{{"summary": "...", "sentiment": "neutral"}}"""


def _mock_summary(title: str, body: str, max_chars: int) -> dict:
    """API 없이 흐름을 검증하기 위한 모의 요약 — 앞부분 문장을 잘라 쓴다."""
    sentences = re.split(r"(?<=[.!?。])\s+", body.strip())
    text = ""
    for sentence in sentences:
        if len(text) + len(sentence) > max_chars:
            break
        text += sentence + " "
    text = (text or body)[:max_chars].strip()
    return {"summary": f"[모의요약] {text}", "sentiment": mock_label(title + body)}


def _select_targets(db: Database, args: argparse.Namespace) -> list:
    if args.id is not None:
        row = db.get_article(args.id)
        if row is None:
            log.error("ID=%s 기사를 찾을 수 없습니다", args.id)
            return []
        return [row]

    status = None if args.all else "unsummarized"
    return db.query_articles(
        category=args.category,
        status=status,
        limit=args.limit,
    )


def run_summarize(args: argparse.Namespace, cfg: Config) -> int:
    max_chars = cfg.ai.get("summary_max_chars", 200)

    try:
        client = AIClient(cfg, mock=args.mock)
    except AIError as e:
        log.error("%s", e)
        return 2

    ok = fail = skipped = 0

    with Database(cfg.path_for("db")) as db:
        targets = _select_targets(db, args)
        if not targets:
            log.info("요약할 대상이 없습니다. (clean 을 먼저 실행했는지 확인하세요)")
            return 0

        log.info("요약 대상: %d건 (모델=%s)", len(targets), client.model)

        for index, row in enumerate(targets, start=1):
            article_id = row["id"]

            # --all 이 아니면 이미 요약된 기사는 건너뛴다
            if row["summary"] and not args.all and args.id is None:
                skipped += 1
                log.debug("[%d/%d] ID=%s 이미 요약됨 — 스킵", index, len(targets), article_id)
                continue

            body = row["body"]
            prompt = USER_TEMPLATE.format(
                title=row["title"],
                category=row["category"] or "미분류",
                body=body[:4000],
                max_chars=max_chars,
            )

            try:
                data = client.json_chat(
                    SYSTEM_PROMPT,
                    prompt,
                    label=f"요약(ID={article_id})",
                    mock_result=lambda: _mock_summary(row["title"], body, max_chars),
                    max_tokens=600,
                )
            except AIError as e:
                fail += 1
                log.error("[%d/%d] ID=%s 요약 실패 — 스킵 (%s)", index, len(targets), article_id, e)
                continue

            summary = (data.get("summary") or "").strip()
            if not summary:
                fail += 1
                log.error("[%d/%d] ID=%s 요약이 비어 있음 — 스킵", index, len(targets), article_id)
                continue

            sentiment = None if args.no_sentiment else normalize_label(data.get("sentiment"))

            db.upsert_summary(
                article_id,
                summary,
                orig_len=len(body),
                summary_len=len(summary),
                sentiment=sentiment,
                model=client.model,
            )
            ok += 1
            log.info(
                "[%d/%d] ID=%s 요약 완료 (%d자 → %d자%s)",
                index, len(targets), article_id, len(body), len(summary),
                f", 감성={sentiment}" if sentiment else "",
            )

    if skipped:
        log.info("이미 요약되어 건너뛴 기사: %d건 (--all 로 강제 재요약 가능)", skipped)
    log.info("요약 완료: %d건 성공, %d건 실패", ok, fail)
    return 0
