"""방법1 — RSS 수집기 (기사 '발견' 담당).

언론사 RSS 는 대개 본문 전문을 주지 않는다. 여기서는 제목·링크·발행시각·카테고리만
확보하고, 본문은 crawl_collector 가 채운다.

장점: 언론사가 공식 제공하는 구조화된 목록이라 파싱이 안정적이고 부하도 적다.
단점: 본문이 없고, 제공하는 항목·기간이 언론사 정책에 묶인다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import feedparser

from ..logger import get_logger

log = get_logger("rss")


def _entry_datetime(entry: Any) -> str | None:
    """feedparser 가 파싱한 시간 구조체를 ISO8601 문자열로."""
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _entry_guid(entry: Any) -> str:
    """guid 가 없는 피드(한국경제 등)는 링크를 식별자로 쓴다."""
    for key in ("id", "guid", "link"):
        value = getattr(entry, key, None)
        if value:
            return str(value)
    return str(getattr(entry, "title", ""))


def parse_feed(
    xml_text: str,
    *,
    source: str,
    category: str,
    feed_url: str,
) -> list[dict]:
    """RSS XML 문자열을 표준 레코드 리스트로 변환한다."""
    parsed = feedparser.parse(xml_text)
    if parsed.bozo and not parsed.entries:
        log.warning("RSS 파싱 실패 (%s/%s): %s", source, category, parsed.bozo_exception)
        return []

    records: list[dict] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        if not link:
            continue
        records.append(
            {
                "source": source,
                "method": "rss",
                "guid": _entry_guid(entry),
                "url": link.strip(),
                "title": (getattr(entry, "title", "") or "").strip(),
                "body": None,
                "category": category,
                "published_at": _entry_datetime(entry),
                "raw": {
                    "feed_url": feed_url,
                    "title": getattr(entry, "title", None),
                    "link": link,
                    "author": getattr(entry, "author", None),
                    "published": getattr(entry, "published", None),
                    "summary": getattr(entry, "summary", None),
                },
            }
        )
    return records


def filter_new(
    records: list[dict],
    *,
    seen_guids: set[str],
    last_pubdate: str | None,
    incremental: bool,
) -> list[dict]:
    """이미 처리한 guid, 마지막 수집 시각 이전 기사를 걸러낸다 (증분 수집)."""
    if not incremental:
        return records

    fresh = []
    for rec in records:
        if rec["guid"] in seen_guids:
            continue
        if last_pubdate and rec["published_at"] and rec["published_at"] <= last_pubdate:
            continue
        fresh.append(rec)
    return fresh


def apply_query(records: list[dict], query: str | None) -> list[dict]:
    """--query 키워드가 제목에 있는 기사만 남긴다."""
    if not query:
        return records
    needle = query.lower()
    return [r for r in records if needle in (r["title"] or "").lower()]
