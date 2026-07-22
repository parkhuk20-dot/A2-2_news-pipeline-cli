"""matplotlib 시각화.

필수 2종: ① 카테고리별 뉴스 수(막대) ② 일자별 수집 추이(선)
보너스 1종: ③ 카테고리별 감성 분포(누적 막대)

한글이 네모(두부)로 깨지지 않도록 OS 별 폰트를 자동 탐색해 적용하고,
음수 기호가 깨지는 문제(axes.unicode_minus)도 함께 끈다.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # GUI 없는 환경에서도 PNG 저장이 되도록
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from .db import Database  # noqa: E402
from .logger import get_logger  # noqa: E402
from .ai.sentiment import to_korean  # noqa: E402

log = get_logger("visualize")

# macOS / Windows / Linux 순으로 흔한 한글 폰트
FONT_CANDIDATES = [
    "AppleGothic",
    "Apple SD Gothic Neo",
    "NanumGothic",
    "NanumBarunGothic",
    "Malgun Gothic",
    "Noto Sans CJK KR",
    "Arial Unicode MS",
]

SENTIMENT_COLORS = {"긍정": "#4C9F70", "부정": "#D9534F", "중립": "#8C8C8C", "미분류": "#C9C9C9"}

_font_applied: str | None = None


def apply_korean_font() -> str | None:
    """사용 가능한 한글 폰트를 찾아 matplotlib 전역 설정에 적용한다."""
    global _font_applied
    if _font_applied:
        return _font_applied

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in FONT_CANDIDATES:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            _font_applied = name
            log.debug("한글 폰트 적용: %s", name)
            return name

    log.warning(
        "한글 폰트를 찾지 못했습니다. 차트의 한글이 깨질 수 있습니다.\n"
        "  macOS: 기본 AppleGothic 사용 가능 / Linux: sudo apt install fonts-nanum"
    )
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log.info("차트 저장: %s", path)
    return path


# ----------------------------------------------------------------------
def chart_category_counts(db: Database, out_dir: Path) -> Path | None:
    """① 카테고리별 뉴스 수 (막대)."""
    data = db.category_counts()
    if not data:
        log.warning("카테고리 집계 데이터가 없어 차트를 건너뜁니다")
        return None

    labels = [row[0] for row in data]
    values = [row[1] for row in data]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, values, color="#3B6EA5")
    ax.set_title("카테고리별 뉴스 수", fontsize=14, pad=12)
    ax.set_xlabel("카테고리")
    ax.set_ylabel("기사 수")
    ax.grid(axis="y", alpha=0.3)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, str(value), ha="center", va="bottom")

    return _save(fig, out_dir / "category_counts.png")


def chart_daily_trend(db: Database, out_dir: Path) -> Path | None:
    """② 일자별 수집 추이 (선)."""
    data = db.daily_counts()
    if not data:
        log.warning("일자별 집계 데이터가 없어 차트를 건너뜁니다")
        return None

    labels = [row[0] for row in data]
    values = [row[1] for row in data]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(labels, values, marker="o", color="#C25E3A", linewidth=2)
    ax.set_title("일자별 뉴스 수집 추이", fontsize=14, pad=12)
    ax.set_xlabel("발행일")
    ax.set_ylabel("기사 수")
    ax.grid(alpha=0.3)
    if len(labels) > 8:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    for x, y in zip(labels, values):
        ax.annotate(str(y), (x, y), textcoords="offset points", xytext=(0, 6), ha="center")

    return _save(fig, out_dir / "daily_trend.png")


def chart_sentiment(db: Database, out_dir: Path) -> Path | None:
    """③ [보너스] 카테고리별 감성 분포 (누적 막대)."""
    rows = db.sentiment_counts(by_category=True)
    if not rows:
        log.warning("감성 데이터가 없어 차트를 건너뜁니다 (summarize 를 먼저 실행하세요)")
        return None

    categories: list[str] = []
    table: dict[str, dict[str, int]] = {}
    for category, sentiment, count in rows:
        if category not in table:
            table[category] = {}
            categories.append(category)
        table[category][to_korean(sentiment)] = count

    labels = ["긍정", "중립", "부정", "미분류"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bottoms = [0] * len(categories)
    for label in labels:
        values = [table[c].get(label, 0) for c in categories]
        if not any(values):
            continue
        ax.bar(categories, values, bottom=bottoms, label=label, color=SENTIMENT_COLORS[label])
        bottoms = [b + v for b, v in zip(bottoms, values)]

    ax.set_title("카테고리별 뉴스 감성 분포", fontsize=14, pad=12)
    ax.set_xlabel("카테고리")
    ax.set_ylabel("기사 수")
    ax.legend(title="감성")
    ax.grid(axis="y", alpha=0.3)

    return _save(fig, out_dir / "sentiment_distribution.png")


def generate_all(db: Database, out_dir: Path) -> list[Path]:
    """리포트에서 쓰는 차트를 한 번에 생성한다."""
    apply_korean_font()
    charts = [
        chart_category_counts(db, out_dir),
        chart_daily_trend(db, out_dir),
        chart_sentiment(db, out_dir),
    ]
    return [c for c in charts if c]
