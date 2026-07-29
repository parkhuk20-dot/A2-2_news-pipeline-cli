"""trends 커맨드 — 키워드 시계열 + 일일 브리핑.

날짜별로 제목 키워드 빈도를 집계해:
  - 최근 N일간 키워드가 어떻게 뜨고 지는지 (시계열 차트)
  - 오늘 '새로 등장한' 키워드 (이전 기간엔 없다가 오늘 나타난 것)
  - 오늘 '급상승한' 키워드 (이전 평균 대비 크게 늘어난 것)
를 보여준다.

AI 호출 없이 순수 집계라 --mock 없이도 항상 동작하고 비용도 없다.
(키워드 추출 규칙은 analyze 모듈과 공유해 리포트·인사이트와 일관성을 유지한다.)
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .ai.analyze import STOPWORDS, WORD
from .config import Config
from .db import Database
from .logger import get_logger

log = get_logger("trends")


def _keywords(title: str) -> list[str]:
    return [t for t in WORD.findall(title or "") if t not in STOPWORDS and len(t) >= 2]


def collect_daily_keywords(
    db: Database, days: int
) -> tuple[list[str], dict[str, Counter]]:
    """최근 `days` 일의 (날짜 리스트, 날짜→키워드Counter) 를 만든다."""
    all_days = [d for d, _ in db.daily_counts()]
    window = all_days[-days:] if days > 0 else all_days
    if not window:
        return [], {}

    per_day: dict[str, Counter] = {d: Counter() for d in window}
    rows = db.query_articles(date_from=window[0], date_to=window[-1])
    for row in rows:
        d = row["published_at"]
        if d in per_day:
            per_day[d].update(_keywords(row["title"]))
    return window, per_day


def _briefing(window: list[str], per_day: dict[str, Counter], top_n: int) -> dict:
    """오늘(마지막 날) 기준 신규·급상승 키워드를 뽑는다."""
    today = window[-1]
    prior = window[:-1]
    today_counts = per_day[today]

    prior_total: Counter = Counter()
    for d in prior:
        prior_total.update(per_day[d])

    new_kw = [
        (kw, c) for kw, c in today_counts.most_common()
        if c >= 2 and prior_total[kw] == 0
    ][:top_n]

    rising = []
    prior_days = max(len(prior), 1)
    for kw, c in today_counts.most_common():
        if c < 2 or prior_total[kw] == 0:
            continue
        prior_avg = prior_total[kw] / prior_days
        if c >= prior_avg * 1.5 and c - prior_avg >= 1:
            rising.append((kw, c, round(prior_avg, 1)))
    rising = rising[:top_n]

    return {"today": today, "new": new_kw, "rising": rising, "top_today": today_counts.most_common(top_n)}


def chart_keyword_timeline(
    window: list[str], per_day: dict[str, Counter], top_k: int, out_dir: Path
) -> Path | None:
    """상위 키워드들의 날짜별 언급 추이를 다중 선그래프로."""
    from .visualize import apply_korean_font
    import matplotlib.pyplot as plt

    if len(window) < 2:
        log.info("시계열 차트는 2일 이상 데이터가 필요합니다 (현재 %d일) — 건너뜁니다", len(window))
        return None

    total: Counter = Counter()
    for d in window:
        total.update(per_day[d])
    top_keywords = [kw for kw, _ in total.most_common(top_k)]
    if not top_keywords:
        return None

    apply_korean_font()
    fig, ax = plt.subplots(figsize=(10, 5))
    for kw in top_keywords:
        series = [per_day[d][kw] for d in window]
        ax.plot(window, series, marker="o", linewidth=1.8, label=kw)

    ax.set_title(f"키워드 시계열 (상위 {len(top_keywords)}개)", fontsize=14, pad=12)
    ax.set_xlabel("발행일")
    ax.set_ylabel("언급 수")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    if len(window) > 8:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "keyword_timeline.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log.info("차트 저장: %s", path)
    return path


def run_trends(args: argparse.Namespace, cfg: Config) -> int:
    top_n = args.top_n or cfg.report.get("top_n", 5)

    with Database(cfg.path_for("db")) as db:
        window, per_day = collect_daily_keywords(db, args.days)
        if not window:
            log.warning("집계할 데이터가 없습니다. fetch → clean 을 먼저 실행하세요.")
            return 0

        brief = _briefing(window, per_day, top_n)

        chart = None
        if not args.no_chart:
            chart = chart_keyword_timeline(window, per_day, max(top_n, 6), cfg.path_for("charts"))

    print()
    print("=" * 56)
    print(f" 키워드 트렌드 브리핑  (기간: {window[0]} ~ {window[-1]}, {len(window)}일)")
    print("=" * 56)

    print(f"\n[오늘({brief['today']}) 많이 나온 키워드]")
    if brief["top_today"]:
        print("  " + ", ".join(f"{kw}({c})" for kw, c in brief["top_today"]))
    else:
        print("  (오늘자 기사가 없습니다)")

    print("\n[🆕 새로 등장한 키워드]  (이전 기간엔 없다가 오늘 나타남)")
    if brief["new"]:
        for kw, c in brief["new"]:
            print(f"  · {kw} — 오늘 {c}회")
    else:
        print("  (없음)")

    print("\n[📈 급상승 키워드]  (이전 일평균 대비 급증)")
    if brief["rising"]:
        for kw, c, avg in brief["rising"]:
            print(f"  · {kw} — 오늘 {c}회 (이전 일평균 {avg}회)")
    else:
        print("  (없음)")

    if chart:
        print(f"\n[시계열 차트] {chart}")
    print()
    return 0
