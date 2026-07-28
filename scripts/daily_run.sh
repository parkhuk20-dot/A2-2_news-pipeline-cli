#!/bin/bash
#
# 일일 자동 수집 래퍼 (launchd / cron 공용)
#
# 이 스크립트는 어디서 호출되든 프로젝트 루트로 이동한 뒤 전체 파이프라인을 돌린다.
# API 키는 프로젝트 루트의 .env 에서 자동으로 읽히므로(load_dotenv) 여기서 따로 설정하지 않는다.
#
# 수동 테스트:  ./scripts/daily_run.sh
#
set -u

# 스크립트 위치 기준으로 프로젝트 루트 계산 (심링크·다른 cwd 에서 호출돼도 안전)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT" || { echo "프로젝트 루트로 이동 실패: $PROJECT_ROOT"; exit 1; }

PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "[$(date '+%F %T')] 가상환경 파이썬을 찾을 수 없음: $PYTHON"
    exit 1
fi

echo "==================================================================="
echo "[$(date '+%F %T')] 일일 수집 시작 (PROJECT_ROOT=$PROJECT_ROOT)"
echo "==================================================================="

# 네트워크(DNS) 준비 대기
# 맥이 절전에서 막 깨어나 예약 시각(오전 8시)에 실행되면, DNS 가 아직 안 올라와
# 전 피드가 "Failed to resolve" 로 실패하는 일이 있었다(2026-07-27, 07-28).
# 뉴스 서버 이름이 실제로 풀릴 때까지 최대 약 5분간 기다렸다가 진행한다.
NET_HOST="www.yna.co.kr"
for attempt in $(seq 1 30); do
    if curl -s -m 5 -o /dev/null "https://$NET_HOST/robots.txt"; then
        echo "[$(date '+%F %T')] 네트워크 준비됨 ($NET_HOST, ${attempt}회째)"
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "[$(date '+%F %T')] 네트워크가 준비되지 않아 중단 (다음 예약 실행에서 재시도)"
        exit 1
    fi
    sleep 10
done

# 소스별 20건 수집 → 정제 → 미요약분 요약 → 인사이트 → 리포트
# summarize-limit 은 하루 수집량(소스별 20 × 2 = 최대 40건)보다 넉넉히 잡아,
# 하루라도 수집이 많거나 자동 실행이 걸러도 요약이 밀리지 않게 한다.
"$PYTHON" main.py run --source all --limit 20 --summarize-limit 60
STATUS=$?

echo "[$(date '+%F %T')] 종료 (exit=$STATUS)"
echo ""
exit $STATUS
