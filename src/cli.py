"""argparse 기반 서브커맨드 CLI.

각 서브커맨드는 독립적으로 실행 가능하며, 이전 단계가 SQLite 에 남긴 결과를 입력으로 받는다.
실제 동작은 단계별 모듈(collectors/cleaner/ai/report/exporter)에 위임하고,
여기서는 옵션 정의와 라우팅만 담당한다.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from .config import Config, ConfigError, load_config
from .logger import get_logger, setup_logging

log = get_logger("cli")

DESCRIPTION = "뉴스 수집 → 정제 → AI 요약·분석 → 시각화·리포트 파이프라인"


def _common_parser() -> argparse.ArgumentParser:
    """모든 서브커맨드가 공유하는 옵션 (도움말 표시용).

    같은 옵션이 상위 파서와 서브파서에 함께 있으면, 서브파서가 나중에 파싱되면서
    기본값으로 앞선 값을 덮어쓴다. 그래서 `main.py --config X fetch` 처럼 서브커맨드
    '앞'에 준 값이 조용히 무시된다. 실제 값은 아래 pre_parse_globals() 로 따로 읽는다.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="설정 파일 경로 (기본: config.json)")
    common.add_argument("--verbose", action="store_true", help="DEBUG 레벨까지 출력")
    return common


def pre_parse_globals(argv: list[str]) -> argparse.Namespace:
    """--config / --verbose 는 위치와 상관없이 인식되도록 먼저 훑는다."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    pre.add_argument("--verbose", action="store_true")
    known, _ = pre.parse_known_args(argv)
    return known


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description=DESCRIPTION,
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python main.py fetch --source yonhap,hankyung --category IT --limit 20\n"
            "  python main.py clean --dedup upsert\n"
            "  python main.py summarize --unsummarized --limit 10\n"
            "  python main.py analyze --date-from 2026-07-01 --date-to 2026-07-22 --category IT\n"
            "  python main.py report --format md --top-n 5\n"
            "  python main.py export --format xlsx --status summarized\n"
            "  python main.py run --source all --limit 30 --mock\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<서브커맨드>")

    # --- fetch --------------------------------------------------------
    p = sub.add_parser("fetch", parents=[common], help="뉴스 수집 (RSS 발견 + 본문 크롤링)")
    p.add_argument("--source", default="all", help="언론사 키 (쉼표 구분 / all / random)")
    p.add_argument("--category", default=None, help="특정 카테고리 피드만 수집")
    p.add_argument("--query", default=None, help="제목에 이 키워드가 있는 기사만 수집")
    p.add_argument("--limit", type=int, default=None, help="수집 최대 건수 (소스별)")
    p.add_argument("--no-incremental", action="store_true", help="증분 수집 끄기 (전체 재수집)")
    p.add_argument("--no-crawl", action="store_true", help="본문 크롤링 없이 RSS 정보만 저장")

    # --- clean --------------------------------------------------------
    p = sub.add_parser("clean", parents=[common], help="정제 · 중복 처리 후 clean 저장")
    p.add_argument("--dedup", choices=["skip", "upsert"], default=None, help="완전 중복 정책")
    p.add_argument("--dedup-similar", action="store_true", help="유사 보도(제목 기준)도 중복 처리")
    p.add_argument("--min-body", type=int, default=100, help="본문 최소 길이 (미만이면 제외)")

    # --- summarize ----------------------------------------------------
    p = sub.add_parser("summarize", parents=[common], help="AI 요약 (+감성 분석)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="모든 기사 (이미 요약된 것 포함)")
    g.add_argument("--id", type=int, default=None, help="특정 기사 ID 하나만")
    g.add_argument("--unsummarized", action="store_true", help="아직 요약되지 않은 기사만 (기본)")
    p.add_argument("--limit", type=int, default=None, help="요약 최대 건수")
    p.add_argument("--category", default=None, help="카테고리 필터")
    p.add_argument("--no-sentiment", action="store_true", help="감성 분석 생략")
    p.add_argument("--mock", action="store_true", help="AI 호출 없이 모의 응답 사용")

    # --- analyze ------------------------------------------------------
    p = sub.add_parser("analyze", parents=[common], help="AI 인사이트 분석 (기간·카테고리 종합)")
    p.add_argument("--date-from", default=None, help="시작일 YYYY-MM-DD")
    p.add_argument("--date-to", default=None, help="종료일 YYYY-MM-DD")
    p.add_argument("--category", default=None, help="카테고리 필터")
    p.add_argument("--limit", type=int, default=None, help="분석에 넣을 최대 기사 수")
    p.add_argument("--mock", action="store_true", help="AI 호출 없이 모의 응답 사용")

    # --- report -------------------------------------------------------
    p = sub.add_parser("report", parents=[common], help="품질 지표 · TOP N · 인사이트 리포트")
    p.add_argument("--format", choices=["txt", "md"], default="md", help="저장 형식")
    p.add_argument("--top-n", type=int, default=None, help="TOP N 집계 개수")
    p.add_argument("--no-charts", action="store_true", help="차트 생성 생략")
    p.add_argument("--output", default=None, help="저장 경로 직접 지정")

    # --- export -------------------------------------------------------
    p = sub.add_parser("export", parents=[common], help="CSV / Excel 내보내기")
    p.add_argument("--format", choices=["csv", "xlsx"], default="csv", help="내보내기 형식")
    p.add_argument("--status", choices=["summarized", "unsummarized"], default=None,
                   help="요약 여부 필터")
    p.add_argument("--category", default=None, help="카테고리 필터")
    p.add_argument("--date-from", default=None, help="시작일 YYYY-MM-DD")
    p.add_argument("--date-to", default=None, help="종료일 YYYY-MM-DD")
    p.add_argument("--output", default=None, help="저장 경로 직접 지정")

    # --- run ----------------------------------------------------------
    p = sub.add_parser("run", parents=[common], help="fetch→clean→summarize→analyze→report 일괄 실행")
    p.add_argument("--source", default="all", help="언론사 키 (쉼표 구분 / all / random)")
    p.add_argument("--category", default=None, help="카테고리 필터")
    p.add_argument("--limit", type=int, default=None, help="소스별 수집 건수")
    p.add_argument("--summarize-limit", type=int, default=10, help="요약 최대 건수")
    p.add_argument("--dedup", choices=["skip", "upsert"], default=None, help="완전 중복 정책")
    p.add_argument("--format", choices=["txt", "md"], default="md", help="리포트 형식")
    p.add_argument("--mock", action="store_true", help="AI 호출 없이 모의 응답 사용")
    p.add_argument("--skip-fetch", action="store_true", help="수집 단계 건너뛰기")

    # --- list (보너스) -------------------------------------------------
    p = sub.add_parser("list", parents=[common], help="[보너스] 뉴스 목록 조회")
    p.add_argument("--category", default=None, help="카테고리 필터")
    p.add_argument("--source", default=None, help="언론사 필터")
    p.add_argument("--date", default=None, help="특정 날짜 YYYY-MM-DD")
    p.add_argument("--date-from", default=None, help="시작일 YYYY-MM-DD")
    p.add_argument("--date-to", default=None, help="종료일 YYYY-MM-DD")
    p.add_argument("--keyword", default=None, help="제목·본문 키워드 검색")
    p.add_argument("--status", choices=["summarized", "unsummarized"], default=None)
    p.add_argument("--page", type=int, default=1, help="페이지 번호 (1부터)")
    p.add_argument("--page-size", type=int, default=10, help="페이지당 건수")

    # --- show (보너스) -------------------------------------------------
    p = sub.add_parser("show", parents=[common], help="[보너스] 뉴스 상세 조회")
    p.add_argument("--id", type=int, required=True, help="기사 ID")
    p.add_argument("--full", action="store_true", help="본문 전문 출력")

    return parser


# ----------------------------------------------------------------------
# 핸들러 — 각 단계 모듈에 위임
# ----------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace, cfg: Config) -> int:
    from .collectors.pipeline_fetch import run_fetch

    return run_fetch(args, cfg)


def cmd_clean(args: argparse.Namespace, cfg: Config) -> int:
    from .cleaner import run_clean

    return run_clean(args, cfg)


def cmd_summarize(args: argparse.Namespace, cfg: Config) -> int:
    from .ai.summarize import run_summarize

    return run_summarize(args, cfg)


def cmd_analyze(args: argparse.Namespace, cfg: Config) -> int:
    from .ai.analyze import run_analyze

    return run_analyze(args, cfg)


def cmd_report(args: argparse.Namespace, cfg: Config) -> int:
    from .report import run_report

    return run_report(args, cfg)


def cmd_export(args: argparse.Namespace, cfg: Config) -> int:
    from .exporter import run_export

    return run_export(args, cfg)


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    from .pipeline import run_all

    return run_all(args, cfg)


def cmd_list(args: argparse.Namespace, cfg: Config) -> int:
    from .viewer import run_list

    return run_list(args, cfg)


def cmd_show(args: argparse.Namespace, cfg: Config) -> int:
    from .viewer import run_show

    return run_show(args, cfg)


HANDLERS: dict[str, Callable[[argparse.Namespace, Config], int]] = {
    "fetch": cmd_fetch,
    "clean": cmd_clean,
    "summarize": cmd_summarize,
    "analyze": cmd_analyze,
    "report": cmd_report,
    "export": cmd_export,
    "run": cmd_run,
    "list": cmd_list,
    "show": cmd_show,
}


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # 서브커맨드 앞/뒤 어디에 써도 동작하도록 공통 옵션을 다시 확정한다
    globals_ = pre_parse_globals(raw_argv)
    args.config = globals_.config or args.config
    args.verbose = globals_.verbose or args.verbose

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    setup_logging(cfg.path_for("log"), verbose=args.verbose)

    try:
        return HANDLERS[args.command](args, cfg)
    except ConfigError as e:
        log.error("설정 오류: %s", e)
        return 2
    except KeyboardInterrupt:
        log.warning("사용자가 중단했습니다.")
        return 130
    except Exception as e:  # 마지막 방어선 — 스택 대신 한 줄 오류로 마무리
        log.error("예상치 못한 오류: %s", e, exc_info=args.verbose)
        return 1
