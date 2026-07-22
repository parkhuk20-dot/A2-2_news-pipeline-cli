"""AI 인사이트 분석 단계.

조건(기간·카테고리)에 맞는 기사를 **한 번의 호출로 묶어** 분석한다.
기사마다 호출하면 비용·레이트리밋 부담이 크고, "종합 분석"이라는 요구에도 맞지 않는다.

분석 항목 4종: 주요 트렌드 / 핵심 키워드 / 공통점·차이점 / 시사점
결과는 insights 테이블에 저장해 리포트에서 재사용한다.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter

from ..config import Config
from ..db import Database
from ..logger import get_logger
from .client import AIClient, AIError

log = get_logger("analyze")

SYSTEM_PROMPT = (
    "너는 한국어 뉴스 데이터를 읽고 흐름을 짚어내는 미디어 애널리스트다. "
    "주어진 기사 목록에서만 근거를 찾고, 없는 사실을 지어내지 않으며, "
    "반드시 지정된 JSON 형식으로만 답한다."
)

USER_TEMPLATE = """아래는 {period} 기간, 카테고리 '{category}' 의 뉴스 {count}건이다.

{articles}

이 뉴스들을 종합해 다음 네 가지를 분석해줘.
- trends: 주요 트렌드 3~5개 (각각 한 문장)
- keywords: 핵심 키워드 5~8개 (명사 위주 단어만)
- common_diff: 기사들의 공통점과 차이점 2~4개
- implications: 시사점 2~3개 (각각 한두 문장)

아래 JSON 형식으로만 답할 것:
{{"trends": ["..."], "keywords": ["..."], "common_diff": ["..."], "implications": ["..."]}}"""

# mock 키워드 추출 시 제외할 흔한 말
STOPWORDS = {
    "기자", "뉴스", "연합뉴스", "한국경제", "지난", "올해", "관련", "대한", "이번", "가장",
    "위해", "통해", "대해", "따라", "밝혔다", "말했다", "라며", "하는", "있다", "했다",
    "시각", "헤드라인", "종합", "속보",
}
WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")


def _mock_insight(rows: list, category: str) -> dict:
    """API 없이 흐름을 검증하기 위한 모의 인사이트 — 제목 빈도 기반."""
    words = Counter()
    for row in rows:
        for token in WORD.findall(row["title"] or ""):
            if token not in STOPWORDS:
                words[token] += 1
    keywords = [word for word, _ in words.most_common(8)]

    sources = Counter(row["source"] for row in rows)
    categories = Counter(row["category"] for row in rows)

    return {
        "trends": [
            f"[모의분석] '{keywords[0] if keywords else category}' 관련 보도가 가장 많이 나타남",
            f"[모의분석] 카테고리 분포: " + ", ".join(f"{k} {v}건" for k, v in categories.most_common(3)),
        ],
        "keywords": keywords or [category],
        "common_diff": [
            f"[모의분석] 공통점: {len(rows)}건 모두 같은 조건(기간·카테고리)으로 수집됨",
            "[모의분석] 차이점: 언론사별 비중 — " + ", ".join(f"{k} {v}건" for k, v in sources.most_common()),
        ],
        "implications": ["[모의분석] 실제 시사점은 OPENAI_API_KEY 설정 후 --mock 없이 실행하면 생성됩니다."],
    }


def _format_articles(rows: list, body_chars: int) -> str:
    lines = []
    for index, row in enumerate(rows, start=1):
        content = row["summary"] or (row["body"] or "")[:body_chars]
        lines.append(
            f"{index}. ({row['published_at']} / {row['source']} / {row['category']}) "
            f"{row['title']}\n   {content}"
        )
    return "\n".join(lines)


def _as_list(value) -> list[str]:
    """모델이 문자열 하나로 답해도 리스트로 맞춰준다."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def print_insight(insight: dict) -> None:
    """콘솔 출력 (과제 예시 형식)."""
    print("\n=== AI 인사이트 분석 결과 ===")
    for title, key in (
        ("[주요 트렌드]", "trends"),
        ("[핵심 키워드]", "keywords"),
        ("[공통점/차이점]", "common_diff"),
        ("[시사점]", "implications"),
    ):
        items = insight.get(key) or []
        if not items:
            continue
        print(f"\n{title}")
        if key == "keywords":
            print(", ".join(items))
        else:
            for item in items:
                print(f"- {item}")
    print()


def run_analyze(args: argparse.Namespace, cfg: Config) -> int:
    limit = args.limit or cfg.ai.get("max_articles_per_analysis", 60)
    body_chars = cfg.ai.get("analysis_body_chars", 400)

    try:
        client = AIClient(cfg, mock=args.mock)
    except AIError as e:
        log.error("%s", e)
        return 2

    with Database(cfg.path_for("db")) as db:
        rows = db.query_articles(
            category=args.category,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=limit,
        )
        if not rows:
            log.warning("조건에 맞는 기사가 없습니다. 기간·카테고리를 확인하세요.")
            return 0

        category = args.category or "전체"
        period = f"{args.date_from or '처음'} ~ {args.date_to or '현재'}"
        log.info("분석 대상: %d건 (기간=%s, 카테고리=%s)", len(rows), period, category)
        log.info("AI 분석 요청 중...")

        prompt = USER_TEMPLATE.format(
            period=period,
            category=category,
            count=len(rows),
            articles=_format_articles(rows, body_chars),
        )

        try:
            data = client.json_chat(
                SYSTEM_PROMPT,
                prompt,
                label="인사이트 분석",
                mock_result=lambda: _mock_insight(rows, category),
                max_tokens=1500,
            )
        except AIError as e:
            log.error("분석 실패: %s", e)
            return 1

        insight = {
            "date_from": args.date_from,
            "date_to": args.date_to,
            "category": args.category,
            "n_articles": len(rows),
            "trends": _as_list(data.get("trends")),
            "keywords": _as_list(data.get("keywords")),
            "common_diff": _as_list(data.get("common_diff")),
            "implications": _as_list(data.get("implications")),
            "model": client.model,
        }

        filled = sum(1 for key in ("trends", "keywords", "common_diff", "implications") if insight[key])
        if filled < 2:
            log.error("분석 항목이 %d개뿐입니다(최소 2개 필요) — 저장하지 않습니다", filled)
            return 1

        insight_id = db.insert_insight(insight)
        log.info("분석 완료 (insight #%d 저장)", insight_id)

    print_insight(insight)
    return 0
