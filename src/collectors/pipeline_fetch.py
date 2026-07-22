"""fetch 서브커맨드 오케스트레이션.

RSS 발견(방법1) → 본문 크롤링(방법2) → raw 저장 순서로 진행하고,
피드별 증분 수집 상태를 갱신한다.
"""

from __future__ import annotations

import argparse

from ..config import Config
from ..db import Database, now_iso
from ..logger import get_logger
from .crawl_collector import crawl_body
from .http_client import HttpClient
from .rss_collector import apply_query, filter_new, parse_feed

log = get_logger("fetch")


def _select_feeds(source_cfg: dict, category: str | None) -> list[tuple[str, str]]:
    """(카테고리, 피드 URL) 목록. --category 가 있으면 그 피드만."""
    feeds = source_cfg.get("feeds", {})
    if category:
        url = feeds.get(category)
        return [(category, url)] if url else []
    return list(feeds.items())


def run_fetch(args: argparse.Namespace, cfg: Config) -> int:
    sources = cfg.resolve_sources(args.source)
    limit = args.limit if args.limit is not None else cfg.fetch["default_limit"]
    incremental = cfg.fetch.get("incremental", True) and not args.no_incremental
    guid_cap = cfg.fetch.get("seen_guid_cap", 500)

    log.info(
        "뉴스 수집 시작: source=%s, limit=%s, category=%s, 증분=%s",
        ",".join(sources),
        limit,
        args.category or "전체",
        "on" if incremental else "off",
    )

    client = HttpClient(cfg.http)
    total_saved = 0
    total_failed = 0

    try:
        with Database(cfg.path_for("db")) as db:
            for source_key in sources:
                source_cfg = cfg.sources[source_key]
                source_name = source_cfg.get("name", source_key)
                article_cfg = source_cfg.get("article", {})
                feeds = _select_feeds(source_cfg, args.category)

                if not feeds:
                    log.warning("%s: '%s' 카테고리 피드가 없습니다 — 건너뜁니다", source_name, args.category)
                    continue

                saved_for_source = 0
                for category, feed_url in feeds:
                    if saved_for_source >= limit:
                        break

                    # --- 방법1: RSS 로 기사 발견 ---------------------------
                    try:
                        xml_text = client.get_text(feed_url, label=f"RSS 수집({source_name}/{category})")
                    except Exception as e:
                        log.error("RSS 수집 실패: %s/%s (%s)", source_name, category, e)
                        total_failed += 1
                        continue

                    records = parse_feed(
                        xml_text, source=source_key, category=category, feed_url=feed_url
                    )
                    last_pubdate, seen_guids = db.get_fetch_state(source_key, category)
                    records = apply_query(records, args.query)
                    records = filter_new(
                        records,
                        seen_guids=seen_guids,
                        last_pubdate=last_pubdate,
                        incremental=incremental,
                    )
                    remaining = limit - saved_for_source
                    records = records[:remaining]

                    log.info(
                        "%s/%s: 신규 %d건 발견 (피드 %s)",
                        source_name, category, len(records), feed_url,
                    )
                    if not records:
                        continue

                    # --- 방법2: 기사 페이지 크롤링으로 본문 확보 -----------
                    newest_pubdate = last_pubdate
                    for record in records:
                        if args.no_crawl:
                            record["method"] = "rss"
                            record["crawl_status"] = "skipped"
                        else:
                            title, body = crawl_body(client, record["url"], article_cfg)
                            record["method"] = "rss+crawl"
                            if body:
                                record["body"] = body
                                record["title"] = record["title"] or title
                                record["crawl_status"] = "ok"
                            else:
                                record["crawl_status"] = "failed"
                                total_failed += 1

                        record["source"] = source_key
                        record["collected_at"] = now_iso()
                        db.insert_raw(record)

                        saved_for_source += 1
                        total_saved += 1
                        seen_guids.add(record["guid"])
                        if record["published_at"] and (
                            newest_pubdate is None or record["published_at"] > newest_pubdate
                        ):
                            newest_pubdate = record["published_at"]

                    db.update_fetch_state(
                        source_key, category, newest_pubdate, seen_guids, cap=guid_cap
                    )
    finally:
        client.close()

    if total_failed:
        log.warning("본문 확보 실패 %d건 — raw 에는 crawl_status='failed' 로 남겼습니다", total_failed)
    log.info("수집 완료: %d건 성공, %d건 실패", total_saved, total_failed)
    log.info("raw 저장소에 저장 완료 (%s)", cfg.path_for("db", ensure_parent=False))
    return 0
