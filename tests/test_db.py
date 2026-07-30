"""SQLite 저장소 단위 테스트 — tmp 경로에 실제 DB 를 만들어 검증."""

import pytest

from src.db import Database


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as d:
        yield d


def _raw(**over):
    base = {
        "source": "yonhap", "method": "rss+crawl", "guid": "g1",
        "url": "https://ex.com/1", "title": "제목1", "body": "본문1",
        "category": "IT", "published_at": "2026-07-22", "crawl_status": "ok",
    }
    base.update(over)
    return base


def _clean(**over):
    base = {
        "raw_id": None, "url": "https://ex.com/1", "title_hash": "h1",
        "title": "제목1", "body": "본문1", "category": "IT",
        "source": "yonhap", "published_at": "2026-07-22",
    }
    base.update(over)
    return base


class TestRaw:
    def test_insert_and_count(self, db):
        db.insert_raw(_raw())
        db.insert_raw(_raw(url="https://ex.com/2", crawl_status="failed"))
        counts = db.count_raw()
        assert counts["total"] == 2 and counts["ok"] == 1

    def test_uncleaned_and_mark(self, db):
        rid = db.insert_raw(_raw())
        assert len(db.uncleaned_raw()) == 1
        db.mark_raw_cleaned([rid])
        assert len(db.uncleaned_raw()) == 0


class TestDedup:
    def test_skip_policy_ignores_duplicate_url(self, db):
        assert db.insert_clean(_clean(), policy="skip") == "inserted"
        assert db.insert_clean(_clean(title="다른제목"), policy="skip") == "skipped"
        # 원래 제목이 유지됨
        rows = db.query_articles()
        assert rows[0]["title"] == "제목1"

    def test_upsert_policy_updates_duplicate_url(self, db):
        assert db.insert_clean(_clean(), policy="upsert") == "inserted"
        assert db.insert_clean(_clean(title="갱신제목"), policy="upsert") == "updated"
        rows = db.query_articles()
        assert len(rows) == 1 and rows[0]["title"] == "갱신제목"

    def test_url_and_titlehash_exist(self, db):
        db.insert_clean(_clean())
        assert db.url_exists("https://ex.com/1")
        assert db.title_hash_exists("h1")
        assert not db.url_exists("https://ex.com/none")


class TestQueryFilters:
    def _seed(self, db):
        db.insert_clean(_clean(url="u1", title_hash="a", category="IT",
                               source="yonhap", published_at="2026-07-22"))
        db.insert_clean(_clean(url="u2", title_hash="b", category="경제",
                               source="hankyung", published_at="2026-07-23", title="반도체 뉴스"))

    def test_filter_by_category(self, db):
        self._seed(db)
        assert len(db.query_articles(category="IT")) == 1

    def test_filter_by_keyword(self, db):
        self._seed(db)
        rows = db.query_articles(keyword="반도체")
        assert len(rows) == 1 and rows[0]["url"] == "u2"

    def test_filter_by_date_range(self, db):
        self._seed(db)
        assert len(db.query_articles(date_from="2026-07-23")) == 1

    def test_pagination(self, db):
        self._seed(db)
        assert len(db.query_articles(limit=1, offset=0)) == 1

    def test_unsummarized_status(self, db):
        self._seed(db)
        assert db.count_articles(status="unsummarized") == 2
        assert db.count_articles(status="summarized") == 0


class TestSummaryAndMetrics:
    def test_summary_and_coverage(self, db):
        cid = db.insert_clean(_clean()) and db.query_articles()[0]["id"]
        db.upsert_summary(cid, "요약문", orig_len=100, summary_len=20,
                          sentiment="positive", model="gpt-4o-mini")
        assert db.count_articles(status="summarized") == 1
        m = db.quality_metrics()
        assert m["summarized"] == 1
        assert m["avg_compression"] == pytest.approx(20.0)

    def test_upsert_summary_replaces(self, db):
        db.insert_clean(_clean())
        cid = db.query_articles()[0]["id"]
        db.upsert_summary(cid, "v1", orig_len=100, summary_len=10, sentiment="neutral", model="m")
        db.upsert_summary(cid, "v2", orig_len=100, summary_len=30, sentiment="positive", model="m")
        row = db.get_article(cid)
        assert row["summary"] == "v2" and row["sentiment"] == "positive"


class TestFetchState:
    def test_roundtrip(self, db):
        db.update_fetch_state("yonhap", "IT", "2026-07-22", {"g1", "g2"}, cap=500)
        last, guids = db.get_fetch_state("yonhap", "IT")
        assert last == "2026-07-22" and guids == {"g1", "g2"}

    def test_cap_trims_old_guids(self, db):
        db.update_fetch_state("s", "f", "d", [f"g{i}" for i in range(10)], cap=3)
        _, guids = db.get_fetch_state("s", "f")
        assert len(guids) == 3


class TestEmbeddingCache:
    def test_save_and_get(self, db):
        db.insert_clean(_clean())
        cid = db.query_articles()[0]["id"]
        db.save_embeddings([(cid, [0.1, 0.2, 0.3])], model="m1")
        got = db.get_embeddings([cid], "m1")
        assert got[cid] == [0.1, 0.2, 0.3]

    def test_model_scoped(self, db):
        db.insert_clean(_clean())
        cid = db.query_articles()[0]["id"]
        db.save_embeddings([(cid, [1.0])], model="m1")
        assert db.get_embeddings([cid], "m2") == {}
