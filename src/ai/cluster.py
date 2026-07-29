"""cluster 커맨드 — 유사 기사를 '이벤트'로 묶고 언론사별 논조를 비교한다.

기존 유사 보도 판정(title_hash)은 제목 글자가 거의 같아야 잡히는 표면적 매칭이라,
"같은 사건을 다른 표현으로 쓴 기사"는 놓친다. 여기서는 임베딩(의미 벡터)의
코사인 유사도로 묶어, 표현이 달라도 같은 사건이면 하나의 이벤트로 모은다.

이벤트가 여러 언론사에 걸치면 → 같은 사안을 언론사들이 어떤 논조(감성)로 다뤘는지 비교한다.

알고리즘: 임계값 기반 그리디 응집 클러스터링 (numpy). 외부 ML 라이브러리 불필요하고
동작이 설명 가능하다. 수백 건 규모에 충분히 빠르다.
"""

from __future__ import annotations

import argparse

import numpy as np

from ..config import Config
from ..db import Database
from ..logger import get_logger
from .embeddings import Embedder
from .sentiment import to_korean

log = get_logger("cluster")


def _text_for(row) -> str:
    """임베딩 입력: 제목 + (요약 또는 본문 앞부분)."""
    tail = row["summary"] or (row["body"] or "")[:300]
    return f"{row['title']} {tail}".strip()


def _greedy_cluster(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """코사인 유사도 임계값 기반 그리디 클러스터링. 인덱스 그룹 리스트를 돌려준다."""
    clusters: list[dict] = []  # {"idxs": [...], "centroid": vec}
    for i in range(len(vectors)):
        v = vectors[i]
        best_c, best_sim = None, threshold
        for c in clusters:
            sim = float(np.dot(v, c["centroid"]))  # 정규화돼 있으므로 내적 = 코사인
            if sim >= best_sim:
                best_sim, best_c = sim, c
        if best_c is None:
            clusters.append({"idxs": [i], "centroid": v.copy()})
        else:
            best_c["idxs"].append(i)
            members = best_c["centroid"] * (len(best_c["idxs"]) - 1) + v
            centroid = members / np.linalg.norm(members)
            best_c["centroid"] = centroid
    return [c["idxs"] for c in clusters]


def _representative(idxs: list[int], vectors: np.ndarray, rows: list) -> int:
    """센트로이드에 가장 가까운 기사 = 이벤트 대표."""
    centroid = vectors[idxs].mean(axis=0)
    n = np.linalg.norm(centroid)
    if n:
        centroid = centroid / n
    best_i, best_sim = idxs[0], -1.0
    for i in idxs:
        sim = float(np.dot(vectors[i], centroid))
        if sim > best_sim:
            best_sim, best_i = sim, i
    return best_i


def run_cluster(args: argparse.Namespace, cfg: Config) -> int:
    limit = args.limit or cfg.ai.get("max_articles_per_analysis", 60) * 5

    try:
        embedder = Embedder(cfg, mock=args.mock)
    except RuntimeError as e:
        log.error("%s", e)
        return 2

    with Database(cfg.path_for("db")) as db:
        rows = db.query_articles(
            category=args.category,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=limit,
        )
        if len(rows) < 2:
            log.warning("클러스터링할 기사가 부족합니다 (%d건).", len(rows))
            return 0

        log.info("클러스터링 대상: %d건 (모델=%s, 임계값=%.2f)", len(rows), embedder.model, args.threshold)

        # --- 임베딩 (캐시 우선) ---------------------------------------
        ids = [r["id"] for r in rows]
        cached = db.get_embeddings(ids, embedder.model)
        missing = [(idx, r) for idx, r in enumerate(rows) if r["id"] not in cached]
        if missing:
            log.info("임베딩 신규 생성: %d건 (캐시 %d건 재사용)", len(missing), len(cached))
            new_vecs = embedder.embed([_text_for(r) for _, r in missing])
            db.save_embeddings(
                ((r["id"], vec) for (_, r), vec in zip(missing, new_vecs)), embedder.model
            )
            for (_, r), vec in zip(missing, new_vecs):
                cached[r["id"]] = vec
        else:
            log.info("임베딩 전부 캐시 재사용 (%d건)", len(cached))

        vectors = np.array([cached[r["id"]] for r in rows], dtype=float)
        # 안전하게 정규화 (내적=코사인 전제)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.where(norms == 0, 1, norms)

        # --- 클러스터링 ----------------------------------------------
        groups = _greedy_cluster(vectors, args.threshold)

        # 언론사 2곳 이상이 다룬 이벤트만
        events = []
        for idxs in groups:
            sources = {rows[i]["source"] for i in idxs}
            if len(sources) >= args.min_sources:
                events.append(idxs)
        events.sort(key=len, reverse=True)

    total_multi = len(events)
    print()
    print("=" * 60)
    print(f" 이벤트 클러스터 — {len(rows)}건에서 {len(groups)}개 묶음, "
          f"{args.min_sources}개+ 언론사 교차 이벤트 {total_multi}개")
    print("=" * 60)

    if not events:
        print(f"\n{args.min_sources}개 이상 언론사가 함께 다룬 이벤트가 없습니다.")
        print("(--threshold 를 낮추거나 --min-sources 1 로 넓혀보세요)\n")
        return 0

    for rank, idxs in enumerate(events[: args.top_n], start=1):
        members = [rows[i] for i in idxs]
        rep = rows[_representative(idxs, vectors, rows)]
        dates = sorted(m["published_at"] for m in members if m["published_at"])
        span = dates[0] if not dates else (dates[0] if dates[0] == dates[-1] else f"{dates[0]}~{dates[-1]}")

        # 언론사별 논조 집계
        by_source: dict[str, list[str]] = {}
        for m in members:
            by_source.setdefault(m["source"], []).append(m["sentiment"] or "unknown")

        print(f"\n[이벤트 {rank}] 기사 {len(members)}건 · 언론사 {len(by_source)}곳 · {span}")
        print(f"  대표: {rep['title']}")
        print("  논조 비교:")
        for src, sents in by_source.items():
            from collections import Counter
            dist = Counter(to_korean(s) for s in sents)
            dist_str = ", ".join(f"{k} {v}" for k, v in dist.items())
            print(f"    - {src:9s}: {dist_str}")

        # 논조 갈림 여부 한 줄 요약
        overall = [to_korean(s) for m in members for s in [m["sentiment"] or "unknown"]]
        from collections import Counter
        oc = Counter(overall)
        if len(oc) > 1 and oc.most_common(1)[0][1] < len(overall):
            top2 = oc.most_common(2)
            print(f"  → 논조 갈림: {top2[0][0]} {top2[0][1]}건 vs {top2[1][0]} {top2[1][1]}건")
        else:
            print(f"  → 논조 일치: 대체로 '{oc.most_common(1)[0][0]}'")

    print()
    log.info("클러스터 분석 완료")
    return 0
