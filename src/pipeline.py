"""run 서브커맨드 — 전체 파이프라인 일괄 실행.

fetch → clean → summarize → analyze → report 를 순서대로 돌린다.
각 단계는 독립 실행 가능한 함수를 그대로 재사용하고, 여기서는 단계별 옵션만 만들어 넘긴다.
한 단계가 실패해도 다음 단계로 넘어갈지는 단계 성격에 따라 정한다
(수집 실패는 계속 진행 가능, 설정 오류 같은 치명적 문제는 중단).
"""

from __future__ import annotations

import argparse
from argparse import Namespace

from .ai.analyze import run_analyze
from .ai.summarize import run_summarize
from .cleaner import run_clean
from .collectors.pipeline_fetch import run_fetch
from .config import Config
from .db import Database
from .logger import get_logger
from .report import run_report

log = get_logger("run")


def _banner(step: int, total: int, title: str) -> None:
    log.info("=" * 52)
    log.info("[%d/%d] %s", step, total, title)
    log.info("=" * 52)


def run_all(args: argparse.Namespace, cfg: Config) -> int:
    steps = 5
    step = 0
    failures: list[str] = []

    # 1) 수집 -----------------------------------------------------------
    if not args.skip_fetch:
        step += 1
        _banner(step, steps, "수집 (fetch)")
        code = run_fetch(
            Namespace(
                source=args.source,
                category=args.category,
                query=None,
                limit=args.limit,
                no_incremental=False,
                no_crawl=False,
            ),
            cfg,
        )
        if code != 0:
            failures.append("fetch")
    else:
        steps -= 1
        log.info("수집 단계는 --skip-fetch 로 건너뜁니다")

    # 2) 정제 -----------------------------------------------------------
    step += 1
    _banner(step, steps, "정제 (clean)")
    if run_clean(Namespace(dedup=args.dedup, dedup_similar=False, min_body=100), cfg) != 0:
        failures.append("clean")

    # 3) 요약 -----------------------------------------------------------
    step += 1
    _banner(step, steps, "AI 요약 (summarize)")
    code = run_summarize(
        Namespace(
            all=False,
            id=None,
            unsummarized=True,
            limit=args.summarize_limit,
            category=args.category,
            no_sentiment=False,
            mock=args.mock,
        ),
        cfg,
    )
    if code != 0:
        failures.append("summarize")

    # 4) 인사이트 --------------------------------------------------------
    step += 1
    _banner(step, steps, "AI 인사이트 분석 (analyze)")
    code = run_analyze(
        Namespace(
            date_from=None,
            date_to=None,
            category=args.category,
            limit=None,
            mock=args.mock,
        ),
        cfg,
    )
    if code != 0:
        failures.append("analyze")

    # 5) 리포트 ----------------------------------------------------------
    step += 1
    _banner(step, steps, "리포트 (report)")
    code = run_report(
        Namespace(
            format=args.format, top_n=None, no_charts=False, output=None,
            no_trends=False, no_cluster=False, mock=args.mock,
        ),
        cfg,
    )
    if code != 0:
        failures.append("report")

    # 마무리 -------------------------------------------------------------
    with Database(cfg.path_for("db")) as db:
        metrics = db.quality_metrics()
    log.info("=" * 52)
    log.info(
        "파이프라인 종료: raw %d건 / clean %d건 / 요약 %d건",
        metrics["raw_total"], metrics["clean_total"], metrics["summarized"],
    )
    if failures:
        log.warning("문제가 있었던 단계: %s", ", ".join(failures))
        return 1
    log.info("모든 단계가 정상 완료되었습니다.")
    return 0
