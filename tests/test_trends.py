"""트렌드 브리핑 로직 단위 테스트."""

from collections import Counter

from src.trends import _briefing, _keywords


class TestKeywords:
    def test_extracts_and_drops_stopwords(self):
        kws = _keywords("삼성전자 반도체 실적 발표했다")
        assert "삼성전자" in kws and "반도체" in kws
        assert "했다" not in kws  # 불용어

    def test_drops_single_char(self):
        assert all(len(k) >= 2 for k in _keywords("A 삼성 B"))


class TestBriefing:
    def _per_day(self):
        return {
            "2026-07-21": Counter({"반도체": 1, "실적": 2}),
            "2026-07-22": Counter({"반도체": 1}),
            "2026-07-23": Counter({"반도체": 5, "폭염": 3, "실적": 1}),  # 오늘
        }

    def test_new_keywords(self):
        window = ["2026-07-21", "2026-07-22", "2026-07-23"]
        brief = _briefing(window, self._per_day(), top_n=5)
        new_kw = dict(brief["new"])
        assert "폭염" in new_kw          # 이전엔 없다가 오늘 3회
        assert "반도체" not in new_kw     # 이전에도 있었음

    def test_rising_keywords(self):
        window = ["2026-07-21", "2026-07-22", "2026-07-23"]
        brief = _briefing(window, self._per_day(), top_n=5)
        rising = {kw for kw, _, _ in brief["rising"]}
        assert "반도체" in rising        # 이전 평균 1.0 → 오늘 5

    def test_today_field(self):
        window = ["2026-07-21", "2026-07-22", "2026-07-23"]
        brief = _briefing(window, self._per_day(), top_n=5)
        assert brief["today"] == "2026-07-23"
