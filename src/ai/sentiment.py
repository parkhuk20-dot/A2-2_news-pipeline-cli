"""[보너스] 감성 분석.

별도 API 호출을 하지 않고 요약과 같은 호출에서 함께 받아온다(비용·지연 절감).
여기서는 라벨 정규화와 mock 규칙, 집계용 한글 라벨만 담당한다.
"""

from __future__ import annotations

LABELS = ("positive", "negative", "neutral")

KOREAN = {
    "positive": "긍정",
    "negative": "부정",
    "neutral": "중립",
    "unknown": "미분류",
}

# 모델이 한국어로 답하거나 표기를 흔들어도 하나로 모아준다
ALIASES = {
    "긍정": "positive",
    "긍정적": "positive",
    "pos": "positive",
    "부정": "negative",
    "부정적": "negative",
    "neg": "negative",
    "중립": "neutral",
    "중립적": "neutral",
    "neu": "neutral",
    "mixed": "neutral",
}

# mock 모드에서 쓰는 아주 단순한 사전 (실제 판정이 아니라 흐름 검증용)
_POSITIVE_HINTS = ("성장", "호조", "상승", "흑자", "확대", "수출", "채용", "개선", "신기록", "돌파")
_NEGATIVE_HINTS = ("하락", "적자", "감소", "우려", "논란", "사고", "위기", "부진", "해킹", "파산")


def normalize_label(value: str | None) -> str:
    """모델이 준 감성 값을 positive/negative/neutral 중 하나로 맞춘다."""
    if not value:
        return "neutral"
    text = str(value).strip().lower()
    if text in LABELS:
        return text
    return ALIASES.get(text, "neutral")


def mock_label(text: str) -> str:
    """mock 모드용 감성 추정 — 키워드 사전 기반."""
    pos = sum(hint in text for hint in _POSITIVE_HINTS)
    neg = sum(hint in text for hint in _NEGATIVE_HINTS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def to_korean(label: str | None) -> str:
    return KOREAN.get(label or "unknown", "미분류")
