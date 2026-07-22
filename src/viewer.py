"""[보너스] 데이터 조회 CLI — list / show.

조건 필터(카테고리·언론사·날짜·키워드·요약여부)와 페이지네이션을 지원한다.
한글은 터미널에서 두 칸을 차지하므로, 표 정렬은 문자 수가 아니라 '표시 폭'으로 계산한다.
"""

from __future__ import annotations

import argparse
import unicodedata

from .ai.sentiment import to_korean
from .config import Config
from .db import Database
from .logger import get_logger

log = get_logger("viewer")


def display_width(text: str) -> int:
    """동아시아 문자(한글·한자)는 2칸으로 계산한 표시 폭."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def fit(text: str, width: int) -> str:
    """표시 폭 기준으로 자르고 남는 칸은 공백으로 채운다."""
    text = (text or "").replace("\n", " ")
    out = ""
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + w > width:
            out = out[:-1] + "…" if out else "…"
            used = display_width(out)
            break
        out += ch
        used += w
    return out + " " * max(width - used, 0)


def _filters_from(args: argparse.Namespace) -> dict:
    return {
        "category": args.category,
        "source": args.source,
        "date": args.date,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "keyword": args.keyword,
        "status": args.status,
    }


def run_list(args: argparse.Namespace, cfg: Config) -> int:
    page = max(args.page, 1)
    page_size = max(args.page_size, 1)
    filters = _filters_from(args)

    with Database(cfg.path_for("db")) as db:
        total = db.count_articles(**filters)
        if total == 0:
            log.info("조건에 맞는 기사가 없습니다.")
            return 0

        last_page = (total + page_size - 1) // page_size
        if page > last_page:
            log.warning("페이지 %d 는 비어 있습니다 (마지막 페이지: %d)", page, last_page)
            return 0

        rows = db.query_articles(**filters, limit=page_size, offset=(page - 1) * page_size)

    active = [f"{k}={v}" for k, v in filters.items() if v]
    print()
    print(f"총 {total}건" + (f" | 필터: {', '.join(active)}" if active else ""))
    print(f"페이지 {page}/{last_page} (페이지당 {page_size}건)")
    print("-" * 108)
    print(
        f"{'ID':>4}  {fit('발행일', 10)}  {fit('언론사', 10)}  {fit('카테고리', 8)}  "
        f"{fit('제목', 46)}  {fit('요약', 6)}  {fit('감성', 6)}"
    )
    print("-" * 108)
    for row in rows:
        print(
            f"{row['id']:>4}  {fit(row['published_at'] or '-', 10)}  {fit(row['source'] or '-', 10)}  "
            f"{fit(row['category'] or '-', 8)}  {fit(row['title'], 46)}  "
            f"{fit('있음' if row['summary'] else '없음', 6)}  "
            f"{fit(to_korean(row['sentiment']) if row['summary'] else '-', 6)}"
        )
    print("-" * 108)
    if last_page > 1:
        hint_parts = ["python main.py list"]
        for key, value in filters.items():
            if value:
                hint_parts.append(f"--{key.replace('_', '-')} {value}")
        hint_parts.append(f"--page-size {page_size}")
        if page < last_page:
            print(f"다음 페이지: {' '.join(hint_parts)} --page {page + 1}")
        if page > 1:
            print(f"이전 페이지: {' '.join(hint_parts)} --page {page - 1}")
    print("상세 조회: python main.py show --id <ID>")
    print()
    return 0


def run_show(args: argparse.Namespace, cfg: Config) -> int:
    with Database(cfg.path_for("db")) as db:
        row = db.get_article(args.id)

    if row is None:
        log.error("ID=%s 기사를 찾을 수 없습니다", args.id)
        return 1

    body = row["body"] or ""
    print()
    print("=" * 78)
    print(row["title"])
    print("=" * 78)
    print(f"ID        : {row['id']}")
    print(f"언론사    : {row['source']}")
    print(f"카테고리  : {row['category']}")
    print(f"발행일    : {row['published_at']}")
    print(f"URL       : {row['url']}")
    print(f"본문 길이 : {len(body)}자")
    if row["is_duplicate"]:
        print("표시      : 유사 보도(중복)로 분류됨")
    print("-" * 78)

    if row["summary"]:
        ratio = (row["summary_len"] / row["orig_len"] * 100) if row["orig_len"] else 0
        print(f"[AI 요약] (감성: {to_korean(row['sentiment'])}, 압축률 {ratio:.1f}%)")
        print(row["summary"])
    else:
        print("[AI 요약] 아직 요약되지 않았습니다.")
        print(f"  → python main.py summarize --id {row['id']}")
    print("-" * 78)

    print("[본문]")
    if args.full or len(body) <= 600:
        print(body)
    else:
        print(body[:600] + " …")
        print(f"\n(전문 {len(body)}자 — 전체를 보려면 --full 옵션을 추가하세요)")
    print()
    return 0
