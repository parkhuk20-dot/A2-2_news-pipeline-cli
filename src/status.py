"""status 커맨드 — 파이프라인 건강 상태 점검 (헬스 대시보드).

지금까지 "돌아가고 있나?"를 확인하려면 launchctl · 로그 grep · DB 쿼리를 매번 조합해야 했다.
이 커맨드는 그 셋을 한 화면으로 모아, 문제가 있으면 경고와 다음 조치를 함께 보여준다.

건강하지 않으면(오늘 수집 0건, 요약 밀림, 마지막 자동 실행 실패 등) 종료 코드 1 을 돌려주어
다른 스크립트에서 `python main.py status || 알림` 처럼 쓸 수도 있다.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from .config import Config
from .db import Database
from .logger import get_logger

log = get_logger("status")

OK = "✓"
WARN = "⚠"


def _read_last_run(cfg: Config) -> tuple[str, int] | None:
    """logs/last_run.txt (daily_run.sh 가 남김) 파싱 → (시각, 종료코드)."""
    path = cfg.path_for("log", ensure_parent=False).parent / "last_run.txt"
    if not path.exists():
        return None
    try:
        stamp, _, code = path.read_text(encoding="utf-8").strip().partition("|")
        return stamp.strip(), int(code.strip() or "0")
    except (ValueError, OSError):
        return None


def run_status(args: argparse.Namespace, cfg: Config) -> int:
    today = date.today().isoformat()
    warnings: list[str] = []

    with Database(cfg.path_for("db")) as db:
        m = db.quality_metrics()
        daily = dict(db.daily_counts())
        # 오늘 실제로 수집(fetch)이 돌았는지는 collected_at 기준으로 본다
        collected_today = db.conn.execute(
            "SELECT COUNT(*) n FROM raw_articles WHERE substr(collected_at,1,10) = ?",
            (today,),
        ).fetchone()["n"]
        failed_crawl = db.conn.execute(
            "SELECT COUNT(*) n FROM raw_articles WHERE crawl_status = 'failed'"
        ).fetchone()["n"]
        uncleaned = len(db.uncleaned_raw())
        unsummarized = db.count_articles(status="unsummarized")
        latest_insight = db.latest_insight()
        recent_days = db.daily_counts()[-5:]

    print()
    print("=" * 56)
    print(f" 뉴스 파이프라인 상태  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print("=" * 56)

    # --- 자동 실행 -----------------------------------------------------
    last_run = _read_last_run(cfg)
    print("\n[자동 실행]")
    if last_run is None:
        print(f"  {WARN} 자동 실행 기록 없음 (launchd 미등록이거나 아직 실행 전)")
        warnings.append("자동 실행 기록이 없습니다 — launchd 등록을 확인하세요")
    else:
        stamp, code = last_run
        run_day = stamp[:10]
        if code == 0:
            print(f"  {OK} 마지막 실행: {stamp} (성공)")
        else:
            print(f"  {WARN} 마지막 실행: {stamp} (실패, exit={code})")
            warnings.append(f"마지막 자동 실행이 실패했습니다 ({stamp}) — logs/launchd.log 확인")
        if run_day < today:
            print(f"  {WARN} 오늘({today}) 자동 실행 기록이 아직 없습니다")

    # --- 수집 현황 -----------------------------------------------------
    print("\n[오늘 수집]")
    if collected_today > 0:
        print(f"  {OK} 오늘 {collected_today}건 수집됨 (발행일 기준 오늘자 {daily.get(today, 0)}건)")
    else:
        print(f"  {WARN} 오늘 수집된 기사가 없습니다")
        warnings.append("오늘 수집된 기사가 없습니다 — `python main.py fetch --source all` 실행 권장")

    # --- 파이프라인 단계 -----------------------------------------------
    print("\n[파이프라인 단계]")
    print(f"  수집(raw)   : {m['raw_total']:>4}건  (본문 확보율 {m['crawl_success_rate']:.0f}%)")
    print(f"  정제(clean) : {m['clean_total']:>4}건")
    print(f"  요약        : {m['summarized']:>4}건  (커버리지 {m['summary_coverage']:.0f}%)")
    if uncleaned:
        print(f"  {WARN} 정제 대기: {uncleaned}건  → `python main.py clean`")
        warnings.append(f"정제 대기 {uncleaned}건")
    if unsummarized:
        marker = WARN if m["summary_coverage"] < 90 else OK
        print(f"  {marker} 요약 대기: {unsummarized}건  → `python main.py summarize --unsummarized`")
        if m["summary_coverage"] < 90:
            warnings.append(f"요약 커버리지 {m['summary_coverage']:.0f}% (대기 {unsummarized}건)")
    if failed_crawl:
        print(f"  · 본문 확보 실패(raw): {failed_crawl}건 (정제 단계에서 제외됨)")

    # --- 최근 수집 추이 ------------------------------------------------
    print("\n[최근 수집 추이 (발행일 기준)]")
    if recent_days:
        peak = max(n for _, n in recent_days) or 1
        for d, n in recent_days:
            bar = "█" * max(int(n / peak * 24), 1)
            print(f"  {d}  {bar} {n}")
    else:
        print("  (데이터 없음)")

    # --- 인사이트 -----------------------------------------------------
    print("\n[AI 인사이트]")
    if latest_insight:
        print(f"  {OK} 최근 분석: {latest_insight['created_at'][:16].replace('T', ' ')} "
              f"(기사 {latest_insight['n_articles']}건, 모델 {latest_insight['model']})")
    else:
        print(f"  {WARN} 저장된 인사이트 없음  → `python main.py analyze`")

    # --- 요약 ----------------------------------------------------------
    print("\n" + "-" * 56)
    if warnings:
        print(f" {WARN} 점검 필요 {len(warnings)}건:")
        for w in warnings:
            print(f"   - {w}")
    else:
        print(f" {OK} 모든 항목 정상")
    print("-" * 56 + "\n")

    # --check 옵션이면 문제가 있을 때 종료코드 1
    if getattr(args, "check", False) and warnings:
        return 1
    return 0
