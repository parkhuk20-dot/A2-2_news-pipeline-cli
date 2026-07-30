"""정제 로직 단위 테스트 — 순수 함수라 네트워크·API 없이 검증 가능."""

import pytest

from src.cleaner import (
    clean_record,
    normalize_date,
    normalize_text,
    normalize_url,
    title_hash,
)


class TestNormalizeText:
    def test_removes_html_tags_and_entities(self):
        assert normalize_text("<p>안녕&amp;하세요</p>") == "안녕&하세요"

    def test_collapses_whitespace(self):
        assert normalize_text("여러   공백\t\t정리") == "여러 공백 정리"

    def test_strips_reporter_boilerplate_in_body(self):
        text = "장지현 기자 구독 구독중 이전 다음 이미지 확대 본문 시작"
        assert "구독" not in normalize_text(text)
        assert "본문 시작" in normalize_text(text)

    def test_title_keeps_boilerplate_words(self):
        # 제목에는 보일러플레이트 제거를 적용하지 않는다 (말머리 보존)
        title = "[연합뉴스 이 시각 헤드라인] - 18:00"
        assert normalize_text(title, strip_boilerplate=False) == title

    def test_empty_input(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""


class TestNormalizeUrl:
    def test_removes_tracking_params(self):
        url = "https://ex.com/a?utm_source=rss&id=5&fbclid=xyz"
        assert normalize_url(url) == "https://ex.com/a?id=5"

    def test_lowercases_host_and_strips_fragment(self):
        assert normalize_url("https://WWW.Ex.COM/Path#top") == "https://www.ex.com/Path"

    def test_trailing_slash_normalized(self):
        assert normalize_url("https://ex.com/a/") == "https://ex.com/a"


class TestNormalizeDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-07-22T09:13:49+00:00", "2026-07-22"),
            ("Wed, 22 Jul 2026 18:13:49 +0900", "2026-07-22"),
            ("2026.07.22", "2026-07-22"),
            ("2026/07/22", "2026-07-22"),
            ("2026년 07월 22일 발행 2026-07-22 송고", "2026-07-22"),
        ],
    )
    def test_various_formats(self, raw, expected):
        assert normalize_date(raw) == expected

    def test_falls_back_to_second_arg(self):
        assert normalize_date(None, "2026-07-22T00:00:00+09:00") == "2026-07-22"

    def test_unparseable_returns_none(self):
        assert normalize_date("날짜없음", None) is None


class TestTitleHash:
    def test_same_after_removing_symbols_and_spaces(self):
        assert title_hash("삼성전자, 신제품 공개") == title_hash("삼성전자 신제품 공개!")

    def test_different_titles_differ(self):
        assert title_hash("삼성전자 실적") != title_hash("LG전자 실적")


class TestCleanRecord:
    def _row(self, **over):
        base = {
            "id": 1, "url": "https://ex.com/a", "title": "제목입니다",
            "body": "본문 " * 60, "category": "IT", "source": "yonhap",
            "published_at": "2026-07-22T09:00:00+00:00", "collected_at": "2026-07-22T10:00:00+09:00",
        }
        base.update(over)
        return base

    def test_valid_record(self):
        rec, reason = clean_record(self._row(), min_body=100)
        assert reason == "ok"
        assert rec["published_at"] == "2026-07-22"
        assert rec["category"] == "IT"
        assert rec["title_hash"]

    def test_missing_url_rejected(self):
        rec, reason = clean_record(self._row(url=""), min_body=100)
        assert rec is None and "URL" in reason

    def test_short_body_rejected(self):
        rec, reason = clean_record(self._row(body="짧음"), min_body=100)
        assert rec is None and "길이" in reason

    def test_missing_category_defaults(self):
        rec, _ = clean_record(self._row(category=None), min_body=100)
        assert rec["category"] == "미분류"
