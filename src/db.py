"""SQLite 영구 저장소.

메모리(list/dict)가 아니라 파일 DB에 모든 단계의 결과를 남긴다.
raw(원본) / clean(정제) 를 분리해, 정제 규칙이 바뀌어도 원본에서 다시 만들 수 있게 한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .logger import get_logger

log = get_logger("db")

SCHEMA = """
-- 1) 원본: 수집한 그대로. 수집 시각 · 소스 · 수집 방법을 함께 남긴다.
CREATE TABLE IF NOT EXISTS raw_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,          -- 언론사 키 (yonhap 등)
    method       TEXT NOT NULL,          -- 'rss' | 'rss+crawl'
    guid         TEXT,                   -- RSS guid (없으면 링크)
    url          TEXT,
    title        TEXT,
    body         TEXT,
    category     TEXT,                   -- 출처 피드에서 그대로 가져옴
    published_at TEXT,                   -- 원본 pubDate 문자열
    crawl_status TEXT DEFAULT 'ok',      -- 'ok' | 'failed'
    raw_json     TEXT,                   -- RSS 항목 원본 (재현용)
    collected_at TEXT NOT NULL,          -- 수집 시각(ISO8601)
    is_cleaned   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_cleaned ON raw_articles(is_cleaned);
CREATE INDEX IF NOT EXISTS idx_raw_guid ON raw_articles(source, guid);

-- 2) 정제: 검증·정규화를 통과한 데이터. url UNIQUE 로 완전 중복을 막는다.
CREATE TABLE IF NOT EXISTS clean_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id       INTEGER REFERENCES raw_articles(id),
    url          TEXT UNIQUE NOT NULL,
    title_hash   TEXT,                   -- 유사 보도 판정용(정규화 제목 해시)
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    category     TEXT,
    source       TEXT,
    published_at TEXT,                   -- YYYY-MM-DD 로 통일
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    cleaned_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clean_cat ON clean_articles(category);
CREATE INDEX IF NOT EXISTS idx_clean_date ON clean_articles(published_at);
CREATE INDEX IF NOT EXISTS idx_clean_titlehash ON clean_articles(title_hash);

-- 3) AI 요약(+감성). 기사당 1건이라 article_id 가 UNIQUE.
CREATE TABLE IF NOT EXISTS summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER UNIQUE NOT NULL REFERENCES clean_articles(id),
    summary      TEXT NOT NULL,
    orig_len     INTEGER,
    summary_len  INTEGER,
    sentiment    TEXT,                   -- positive | negative | neutral
    model        TEXT,
    created_at   TEXT NOT NULL
);

-- 4) AI 인사이트: 조건(기간·카테고리)별 종합 분석 결과.
CREATE TABLE IF NOT EXISTS insights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date_from     TEXT,
    date_to       TEXT,
    category      TEXT,
    n_articles    INTEGER,
    trends        TEXT,                  -- JSON 배열 문자열
    keywords      TEXT,
    common_diff   TEXT,
    implications  TEXT,
    model         TEXT,
    created_at    TEXT NOT NULL
);

-- 5) 증분 수집 상태: 피드별로 어디까지 봤는지 기억한다.
CREATE TABLE IF NOT EXISTS fetch_state (
    source       TEXT NOT NULL,
    feed         TEXT NOT NULL,
    last_pubdate TEXT,
    seen_guids   TEXT,                   -- JSON 배열 문자열
    updated_at   TEXT,
    PRIMARY KEY (source, feed)
);

-- 6) 임베딩 캐시: 이벤트 클러스터링용 벡터를 재계산 없이 재사용한다.
CREATE TABLE IF NOT EXISTS embeddings (
    article_id INTEGER UNIQUE NOT NULL REFERENCES clean_articles(id),
    model      TEXT,
    dim        INTEGER,
    vector     TEXT,                     -- JSON 실수 배열
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    """로컬 타임존이 붙은 ISO8601 현재 시각."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Database:
    """sqlite3 커넥션을 감싼 저장소. with 문으로 쓰면 자동으로 닫힌다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    # raw 단계
    # ------------------------------------------------------------------
    def insert_raw(self, record: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO raw_articles
                (source, method, guid, url, title, body, category,
                 published_at, crawl_status, raw_json, collected_at)
            VALUES (:source, :method, :guid, :url, :title, :body, :category,
                    :published_at, :crawl_status, :raw_json, :collected_at)
            """,
            {
                "source": record["source"],
                "method": record.get("method", "rss+crawl"),
                "guid": record.get("guid"),
                "url": record.get("url"),
                "title": record.get("title"),
                "body": record.get("body"),
                "category": record.get("category"),
                "published_at": record.get("published_at"),
                "crawl_status": record.get("crawl_status", "ok"),
                "raw_json": json.dumps(record.get("raw", {}), ensure_ascii=False),
                "collected_at": record.get("collected_at") or now_iso(),
            },
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def uncleaned_raw(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM raw_articles WHERE is_cleaned = 0 ORDER BY id"
        ).fetchall()

    def mark_raw_cleaned(self, raw_ids: Sequence[int]) -> None:
        self.conn.executemany(
            "UPDATE raw_articles SET is_cleaned = 1 WHERE id = ?",
            [(rid,) for rid in raw_ids],
        )
        self.conn.commit()

    def count_raw(self) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN crawl_status = 'ok' THEN 1 ELSE 0 END) AS ok
            FROM raw_articles
            """
        ).fetchone()
        return {"total": row["total"] or 0, "ok": row["ok"] or 0}

    # ------------------------------------------------------------------
    # 증분 수집 상태
    # ------------------------------------------------------------------
    def get_fetch_state(self, source: str, feed: str) -> tuple[str | None, set[str]]:
        row = self.conn.execute(
            "SELECT last_pubdate, seen_guids FROM fetch_state WHERE source = ? AND feed = ?",
            (source, feed),
        ).fetchone()
        if row is None:
            return None, set()
        guids = set(json.loads(row["seen_guids"] or "[]"))
        return row["last_pubdate"], guids

    def update_fetch_state(
        self, source: str, feed: str, last_pubdate: str | None, seen_guids: Iterable[str], cap: int = 500
    ) -> None:
        trimmed = list(seen_guids)[-cap:]
        self.conn.execute(
            """
            INSERT INTO fetch_state (source, feed, last_pubdate, seen_guids, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, feed) DO UPDATE SET
                last_pubdate = excluded.last_pubdate,
                seen_guids   = excluded.seen_guids,
                updated_at   = excluded.updated_at
            """,
            (source, feed, last_pubdate, json.dumps(trimmed, ensure_ascii=False), now_iso()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # clean 단계
    # ------------------------------------------------------------------
    def insert_clean(self, record: dict[str, Any], policy: str = "skip") -> str:
        """정제 기사 1건 저장. 반환값: 'inserted' | 'updated' | 'skipped'."""
        params = {
            "raw_id": record.get("raw_id"),
            "url": record["url"],
            "title_hash": record.get("title_hash"),
            "title": record["title"],
            "body": record["body"],
            "category": record.get("category"),
            "source": record.get("source"),
            "published_at": record.get("published_at"),
            "is_duplicate": int(record.get("is_duplicate", 0)),
            "cleaned_at": now_iso(),
        }
        columns = ", ".join(params)
        placeholders = ", ".join(f":{k}" for k in params)

        if policy == "upsert":
            sql = (
                f"INSERT INTO clean_articles ({columns}) VALUES ({placeholders}) "
                "ON CONFLICT(url) DO UPDATE SET "
                "raw_id=excluded.raw_id, title=excluded.title, body=excluded.body, "
                "title_hash=excluded.title_hash, category=excluded.category, "
                "source=excluded.source, published_at=excluded.published_at, "
                "cleaned_at=excluded.cleaned_at"
            )
        else:  # skip
            sql = f"INSERT OR IGNORE INTO clean_articles ({columns}) VALUES ({placeholders})"

        # UPSERT 후의 lastrowid 는 삽입/갱신을 구분하는 근거로 쓸 수 없어서
        # (SQLite 버전에 따라 직전 INSERT 의 rowid 가 남는다) 미리 존재 여부를 확인한다.
        existed = self.url_exists(record["url"])

        before = self.conn.total_changes
        self.conn.execute(sql, params)
        self.conn.commit()

        if self.conn.total_changes == before:
            return "skipped"
        return "updated" if existed else "inserted"

    def url_exists(self, url: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM clean_articles WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            is not None
        )

    def title_hash_exists(self, title_hash: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM clean_articles WHERE title_hash = ? AND is_duplicate = 0 LIMIT 1",
                (title_hash,),
            ).fetchone()
            is not None
        )

    def guid_exists(self, source: str, guid: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM raw_articles WHERE source = ? AND guid = ? LIMIT 1",
                (source, guid),
            ).fetchone()
            is not None
        )

    # ------------------------------------------------------------------
    # 조회 (요약 대상 선정 · list/show · 리포트 공용)
    # ------------------------------------------------------------------
    def query_articles(
        self,
        *,
        category: str | None = None,
        source: str | None = None,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        keyword: str | None = None,
        status: str | None = None,   # 'summarized' | 'unsummarized'
        article_id: int | None = None,
        include_duplicates: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        where: list[str] = []
        params: list[Any] = []

        if article_id is not None:
            where.append("c.id = ?")
            params.append(article_id)
        if category:
            where.append("c.category = ?")
            params.append(category)
        if source:
            where.append("c.source = ?")
            params.append(source)
        if date:
            where.append("c.published_at = ?")
            params.append(date)
        if date_from:
            where.append("c.published_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("c.published_at <= ?")
            params.append(date_to)
        if keyword:
            where.append("(c.title LIKE ? OR c.body LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if status == "summarized":
            where.append("s.id IS NOT NULL")
        elif status == "unsummarized":
            where.append("s.id IS NULL")
        if not include_duplicates:
            where.append("c.is_duplicate = 0")

        sql = (
            "SELECT c.*, s.summary, s.sentiment, s.orig_len, s.summary_len, "
            "       s.created_at AS summarized_at "
            "FROM clean_articles c LEFT JOIN summaries s ON s.article_id = c.id"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.published_at DESC, c.id DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def count_articles(self, **filters) -> int:
        filters.pop("limit", None)
        filters.pop("offset", None)
        return len(self.query_articles(**filters))

    def get_article(self, article_id: int) -> sqlite3.Row | None:
        rows = self.query_articles(article_id=article_id, include_duplicates=True)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # 요약
    # ------------------------------------------------------------------
    def upsert_summary(
        self,
        article_id: int,
        summary: str,
        *,
        orig_len: int,
        summary_len: int,
        sentiment: str | None,
        model: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO summaries
                (article_id, summary, orig_len, summary_len, sentiment, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                summary=excluded.summary, orig_len=excluded.orig_len,
                summary_len=excluded.summary_len, sentiment=excluded.sentiment,
                model=excluded.model, created_at=excluded.created_at
            """,
            (article_id, summary, orig_len, summary_len, sentiment, model, now_iso()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # 인사이트
    # ------------------------------------------------------------------
    def insert_insight(self, record: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO insights
                (date_from, date_to, category, n_articles,
                 trends, keywords, common_diff, implications, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("date_from"),
                record.get("date_to"),
                record.get("category"),
                record.get("n_articles", 0),
                json.dumps(record.get("trends", []), ensure_ascii=False),
                json.dumps(record.get("keywords", []), ensure_ascii=False),
                json.dumps(record.get("common_diff", []), ensure_ascii=False),
                json.dumps(record.get("implications", []), ensure_ascii=False),
                record.get("model"),
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_insight(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM insights ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        for key in ("trends", "keywords", "common_diff", "implications"):
            try:
                data[key] = json.loads(data[key] or "[]")
            except json.JSONDecodeError:
                data[key] = []
        return data

    def list_insights(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM insights ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ------------------------------------------------------------------
    # 집계 (시각화 · 리포트)
    # ------------------------------------------------------------------
    def category_counts(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            """
            SELECT COALESCE(category, '미분류') AS c, COUNT(*) AS n
            FROM clean_articles WHERE is_duplicate = 0
            GROUP BY c ORDER BY n DESC
            """
        ).fetchall()
        return [(r["c"], r["n"]) for r in rows]

    def daily_counts(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            """
            SELECT published_at AS d, COUNT(*) AS n
            FROM clean_articles
            WHERE is_duplicate = 0 AND published_at IS NOT NULL
            GROUP BY d ORDER BY d
            """
        ).fetchall()
        return [(r["d"], r["n"]) for r in rows]

    def source_counts(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            """
            SELECT COALESCE(source, '미상') AS s, COUNT(*) AS n
            FROM clean_articles WHERE is_duplicate = 0
            GROUP BY s ORDER BY n DESC
            """
        ).fetchall()
        return [(r["s"], r["n"]) for r in rows]

    def sentiment_counts(self, by_category: bool = False) -> list[tuple[str, str, int]]:
        if by_category:
            rows = self.conn.execute(
                """
                SELECT COALESCE(c.category, '미분류') AS grp,
                       COALESCE(s.sentiment, 'unknown') AS sent, COUNT(*) AS n
                FROM summaries s JOIN clean_articles c ON c.id = s.article_id
                GROUP BY grp, sent ORDER BY grp
                """
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT '전체' AS grp, COALESCE(sentiment, 'unknown') AS sent, COUNT(*) AS n
                FROM summaries GROUP BY sent
                """
            ).fetchall()
        return [(r["grp"], r["sent"], r["n"]) for r in rows]

    # ------------------------------------------------------------------
    # 임베딩 캐시 (클러스터링)
    # ------------------------------------------------------------------
    def get_embeddings(self, article_ids: Sequence[int], model: str) -> dict[int, list[float]]:
        """캐시된 임베딩을 {article_id: vector} 로. 같은 모델 것만 반환한다."""
        if not article_ids:
            return {}
        placeholders = ",".join("?" * len(article_ids))
        rows = self.conn.execute(
            f"SELECT article_id, vector FROM embeddings "
            f"WHERE model = ? AND article_id IN ({placeholders})",
            [model, *article_ids],
        ).fetchall()
        return {r["article_id"]: json.loads(r["vector"]) for r in rows}

    def save_embeddings(self, items: Iterable[tuple[int, list[float]]], model: str) -> None:
        rows = [
            (aid, model, len(vec), json.dumps(vec), now_iso())
            for aid, vec in items
        ]
        self.conn.executemany(
            """
            INSERT INTO embeddings (article_id, model, dim, vector, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                model=excluded.model, dim=excluded.dim,
                vector=excluded.vector, created_at=excluded.created_at
            """,
            rows,
        )
        self.conn.commit()

    def quality_metrics(self) -> dict[str, Any]:
        raw = self.count_raw()
        clean_total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM clean_articles"
        ).fetchone()["n"]
        dup = self.conn.execute(
            "SELECT COUNT(*) AS n FROM clean_articles WHERE is_duplicate = 1"
        ).fetchone()["n"]
        summarized = self.conn.execute("SELECT COUNT(*) AS n FROM summaries").fetchone()["n"]
        comp = self.conn.execute(
            "SELECT AVG(CAST(summary_len AS FLOAT) / NULLIF(orig_len, 0)) AS r FROM summaries"
        ).fetchone()["r"]
        active = max(clean_total - dup, 0)
        return {
            "raw_total": raw["total"],
            "raw_ok": raw["ok"],
            "crawl_success_rate": (raw["ok"] / raw["total"] * 100) if raw["total"] else 0.0,
            "clean_total": clean_total,
            "duplicates": dup,
            "dedup_removed": raw["total"] - clean_total if raw["total"] >= clean_total else 0,
            "summarized": summarized,
            "summary_coverage": (summarized / active * 100) if active else 0.0,
            "avg_compression": (comp * 100) if comp else 0.0,
        }
