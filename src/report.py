"""리포트 생성.

포함 내용
  - 품질 지표 4종: 수집 성공률 · 요약 커버리지 · 중복/탈락 건수 · 평균 압축률
  - TOP N 집계 2종: 카테고리 TOP N · 제목 키워드 빈도 TOP N
  - 최신 AI 인사이트
  - 생성된 차트 목록

콘솔에 출력하고 동시에 파일(MD/TXT)로 저장한다.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from .ai.analyze import STOPWORDS, WORD
from .ai.sentiment import to_korean
from .config import Config
from .db import Database
from .logger import get_logger
from .visualize import generate_all

log = get_logger("report")


def top_keywords(db: Database, top_n: int) -> list[tuple[str, int]]:
    """제목에서 뽑은 키워드 빈도 TOP N (AI 호출 없이 집계)."""
    counter: Counter[str] = Counter()
    for row in db.query_articles():
        for token in WORD.findall(row["title"] or ""):
            if token not in STOPWORDS:
                counter[token] += 1
    return counter.most_common(top_n)


def build_report(db: Database, *, top_n: int, charts: list[Path], as_markdown: bool) -> str:
    metrics = db.quality_metrics()
    categories = db.category_counts()[:top_n]
    keywords = top_keywords(db, top_n)
    sources = db.source_counts()
    sentiments = db.sentiment_counts()
    insight = db.latest_insight()

    h1 = "# " if as_markdown else ""
    h2 = "## " if as_markdown else ""
    bullet = "- "
    lines: list[str] = []

    lines.append(f"{h1}뉴스 파이프라인 리포트")
    lines.append("")
    lines.append(f"생성 시각: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # --- 품질 지표 ----------------------------------------------------
    lines.append(f"{h2}1. 품질 지표")
    lines.append("")
    lines.append(f"{bullet}수집 성공률: {metrics['crawl_success_rate']:.1f}% "
                 f"({metrics['raw_ok']}/{metrics['raw_total']}건 본문 확보)")
    lines.append(f"{bullet}요약 커버리지: {metrics['summary_coverage']:.1f}% "
                 f"({metrics['summarized']}/{max(metrics['clean_total'] - metrics['duplicates'], 0)}건)")
    lines.append(f"{bullet}중복·탈락 제거: {metrics['dedup_removed']}건 "
                 f"(raw {metrics['raw_total']}건 → clean {metrics['clean_total']}건, "
                 f"유사 보도 표시 {metrics['duplicates']}건)")
    lines.append(f"{bullet}평균 압축률: {metrics['avg_compression']:.1f}% (요약 길이 / 원문 길이)")
    lines.append("")

    # --- TOP N --------------------------------------------------------
    lines.append(f"{h2}2. TOP {top_n} 집계")
    lines.append("")
    lines.append(f"{h2 and '### ' or ''}카테고리 TOP {top_n}")
    for rank, (name, count) in enumerate(categories, start=1):
        lines.append(f"{bullet}{rank}위 {name}: {count}건")
    lines.append("")
    lines.append(f"{h2 and '### ' or ''}키워드 TOP {top_n} (제목 기준)")
    if keywords:
        for rank, (word, count) in enumerate(keywords, start=1):
            lines.append(f"{bullet}{rank}위 {word}: {count}회")
    else:
        lines.append(f"{bullet}(집계할 기사가 없습니다)")
    lines.append("")

    # --- 분포 ---------------------------------------------------------
    lines.append(f"{h2}3. 분포")
    lines.append("")
    lines.append(f"{bullet}언론사별: " + (", ".join(f"{s} {n}건" for s, n in sources) or "없음"))
    lines.append(f"{bullet}감성별: " + (
        ", ".join(f"{to_korean(sent)} {n}건" for _, sent, n in sentiments) or "없음 (요약 미실행)"
    ))
    lines.append("")

    # --- AI 인사이트 ---------------------------------------------------
    lines.append(f"{h2}4. AI 인사이트")
    lines.append("")
    if insight:
        scope = (
            f"기간 {insight['date_from'] or '처음'} ~ {insight['date_to'] or '현재'}, "
            f"카테고리 {insight['category'] or '전체'}, 분석 기사 {insight['n_articles']}건, "
            f"모델 {insight['model']}"
        )
        lines.append(f"{bullet}분석 범위: {scope}")
        lines.append("")
        for title, key in (
            ("주요 트렌드", "trends"),
            ("핵심 키워드", "keywords"),
            ("공통점/차이점", "common_diff"),
            ("시사점", "implications"),
        ):
            items = insight.get(key) or []
            if not items:
                continue
            lines.append(f"{h2 and '### ' or ''}{title}")
            if key == "keywords":
                lines.append(", ".join(items))
            else:
                for item in items:
                    lines.append(f"{bullet}{item}")
            lines.append("")
    else:
        lines.append(f"{bullet}저장된 인사이트가 없습니다. `python main.py analyze` 를 먼저 실행하세요.")
        lines.append("")

    # --- 차트 ---------------------------------------------------------
    lines.append(f"{h2}5. 차트")
    lines.append("")
    if charts:
        for path in charts:
            if as_markdown:
                lines.append(f"{bullet}![{path.stem}]({path.as_posix()})")
            else:
                lines.append(f"{bullet}{path}")
    else:
        lines.append(f"{bullet}(생성된 차트 없음)")
    lines.append("")

    return "\n".join(lines)


def run_report(args: argparse.Namespace, cfg: Config) -> int:
    top_n = args.top_n or cfg.report.get("top_n", 5)

    with Database(cfg.path_for("db")) as db:
        if db.quality_metrics()["clean_total"] == 0:
            log.warning("clean 데이터가 없습니다. fetch → clean 을 먼저 실행하세요.")
            return 0

        charts: list[Path] = []
        if not args.no_charts:
            charts = generate_all(db, cfg.path_for("charts"))

        content = build_report(db, top_n=top_n, charts=charts, as_markdown=(args.format == "md"))

    print()
    print(content)

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = cfg.path_for("reports") / f"report_{stamp}.{args.format}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    log.info("리포트 저장: %s", out_path)
    return 0
