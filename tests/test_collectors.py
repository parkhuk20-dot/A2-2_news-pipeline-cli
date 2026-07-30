"""RSS 파싱·증분 필터 단위 테스트 — 샘플 XML 로 네트워크 없이 검증."""

from src.collectors.rss_collector import apply_query, filter_new, parse_feed

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>테스트피드</title>
  <item>
    <title>삼성전자 신제품 공개</title>
    <link>https://ex.com/a</link>
    <guid>guid-a</guid>
    <pubDate>Wed, 22 Jul 2026 18:00:00 +0900</pubDate>
  </item>
  <item>
    <title>AI 반도체 뉴스</title>
    <link>https://ex.com/b</link>
    <guid>guid-b</guid>
    <pubDate>Wed, 22 Jul 2026 17:00:00 +0900</pubDate>
  </item>
</channel></rss>"""


class TestParseFeed:
    def test_parses_items(self):
        recs = parse_feed(SAMPLE_RSS, source="yonhap", category="IT", feed_url="u")
        assert len(recs) == 2
        assert recs[0]["title"] == "삼성전자 신제품 공개"
        assert recs[0]["source"] == "yonhap"
        assert recs[0]["category"] == "IT"
        assert recs[0]["method"] == "rss"
        assert recs[0]["published_at"].startswith("2026-07-22")

    def test_guid_extracted(self):
        recs = parse_feed(SAMPLE_RSS, source="s", category="c", feed_url="u")
        assert recs[0]["guid"] == "guid-a"

    def test_empty_feed(self):
        assert parse_feed("<rss><channel></channel></rss>", source="s", category="c", feed_url="u") == []


class TestFilterNew:
    def _recs(self):
        return parse_feed(SAMPLE_RSS, source="s", category="c", feed_url="u")

    def test_skips_seen_guids(self):
        out = filter_new(self._recs(), seen_guids={"guid-a"}, last_pubdate=None, incremental=True)
        assert [r["guid"] for r in out] == ["guid-b"]

    def test_skips_older_than_last_pubdate(self):
        recs = self._recs()
        cutoff = recs[0]["published_at"]  # 최신 기사 시각
        out = filter_new(recs, seen_guids=set(), last_pubdate=cutoff, incremental=True)
        # cutoff 이하는 제외 → 둘 다 빠짐
        assert out == []

    def test_incremental_off_returns_all(self):
        out = filter_new(self._recs(), seen_guids={"guid-a"}, last_pubdate="2027-01-01", incremental=False)
        assert len(out) == 2


class TestApplyQuery:
    def test_filters_by_title_keyword(self):
        recs = parse_feed(SAMPLE_RSS, source="s", category="c", feed_url="u")
        out = apply_query(recs, "반도체")
        assert len(out) == 1
        assert out[0]["url"] == "https://ex.com/b"

    def test_none_query_returns_all(self):
        recs = parse_feed(SAMPLE_RSS, source="s", category="c", feed_url="u")
        assert len(apply_query(recs, None)) == 2
