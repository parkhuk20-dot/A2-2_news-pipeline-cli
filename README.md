# 뉴스 데이터 파이프라인 CLI

뉴스를 **자동 수집 → 정제 → AI 요약·분석 → 시각화·리포트·내보내기** 까지 잇는 CLI 기반 데이터 파이프라인입니다.
(Codyssey A2-2 과제)

```
[fetch] RSS 목록 발견 → 기사 페이지 크롤링으로 본문 확보 → raw_articles
   ↓
[clean] 검증·정규화·날짜통일·중복처리(skip/upsert)      → clean_articles
   ↓
[summarize] AI 요약 + 감성 분석                          → summaries
   ↓
[analyze] 기간·카테고리 종합 인사이트(JSON)              → insights
   ↓
[report] 품질지표·TOP N·차트   [export] CSV/Excel        → output/
```

---

## 1. 빠른 시작

```bash
# 1) 의존성 설치 (Python 3.10 이상)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) 설정 파일 준비
cp config.example.json config.json

# 3) API 키는 환경변수로만 (코드·설정 파일에 넣지 않습니다)
export OPENAI_API_KEY='sk-...'

# 4) 전체 파이프라인 한 번에 (키가 없으면 --mock 으로 먼저 체험)
python main.py run --source all --limit 10 --mock
```

> `--mock` 은 AI API를 호출하지 않고 모의 응답으로 파이프라인 전체를 돌립니다.
> 키·비용 없이 흐름을 확인하거나, 수집·정제 로직만 검증할 때 사용하세요.

---

## 2. 서브커맨드

| 커맨드 | 설명 | 주요 옵션 |
|---|---|---|
| `fetch` | RSS로 기사 발견 + 본문 크롤링 → raw 저장 | `--source` `--category` `--query` `--limit` `--no-incremental` `--no-crawl` |
| `clean` | 검증·정규화·중복 처리 → clean 저장 | `--dedup {skip,upsert}` `--dedup-similar` `--min-body` |
| `summarize` | AI 요약 + 감성 분석 | `--all` `--id` `--unsummarized` `--limit` `--category` `--no-sentiment` `--mock` |
| `analyze` | 기간·카테고리 종합 인사이트 | `--date-from` `--date-to` `--category` `--limit` `--mock` |
| `report` | 품질지표·TOP N·인사이트·차트 리포트 | `--format {txt,md}` `--top-n` `--no-charts` `--output` |
| `export` | CSV / Excel 내보내기 | `--format {csv,xlsx}` `--status` `--category` `--date-from/to` `--output` |
| `run` | 위 단계 일괄 실행 | `--source` `--limit` `--summarize-limit` `--dedup` `--mock` `--skip-fetch` |
| `list` *(보너스)* | 뉴스 목록 조회 | `--category` `--source` `--date` `--keyword` `--status` `--page` `--page-size` |
| `show` *(보너스)* | 뉴스 상세 조회 | `--id` `--full` |
| `status` | 파이프라인 건강 상태 점검 | `--check` |
| `trends` | 키워드 시계열 + 신규·급상승 브리핑 | `--days` `--top-n` `--no-chart` |
| `cluster` | 유사 기사 이벤트 묶음 + 언론사 논조 비교 | `--date-from/to` `--category` `--threshold` `--min-sources` `--top-n` `--mock` |

공통 옵션: `--config <경로>` (기본 `config.json`), `--verbose` (DEBUG 로그)

### 사용 예시

```bash
# 언론사·카테고리 지정 수집 (--source 는 쉼표 구분 / all / random)
python main.py fetch --source yonhap,hankyung --category IT --limit 20

# 키워드가 제목에 든 기사만
python main.py fetch --source hankyung --query AI --limit 10

# 정제 (완전 중복은 갱신, 유사 보도까지 중복 처리)
python main.py clean --dedup upsert --dedup-similar

# 아직 요약 안 된 기사 10건만 요약
python main.py summarize --unsummarized --limit 10

# 특정 기사 하나만 다시 요약
python main.py summarize --id 42

# 기간·카테고리 종합 분석
python main.py analyze --date-from 2026-07-01 --date-to 2026-07-22 --category IT

# 리포트(마크다운) + 차트 생성
python main.py report --format md --top-n 5

# 요약 완료된 기사만 엑셀로
python main.py export --format xlsx --status summarized

# 조회 (보너스)
python main.py list --category IT --keyword AI --page 1 --page-size 10
python main.py show --id 42 --full
```

---

## 3. 설정 (`config.json`)

`config.example.json` 을 복사해 사용합니다. **API 키는 이 파일에 넣지 않습니다.**

| 섹션 | 키 | 설명 |
|---|---|---|
| `http` | `timeout` | 요청 타임아웃(초) |
| | `delay_sec` | 요청 간 최소 간격 — 과도한 요청 방지 |
| | `max_retries` / `backoff_base` | 지수 백오프 재시도 |
| | `respect_robots` | robots.txt 준수 여부 |
| `fetch` | `default_limit` / `incremental` | 기본 수집 건수 / 증분 수집 |
| `dedup` | `policy` | `skip`(기본) 또는 `upsert` |
| | `dedup_similar` | 유사 보도까지 중복 처리할지 |
| `ai` | `model` / `temperature` | 사용할 모델 |
| | `summary_max_chars` | 요약 목표 길이 |
| | `max_articles_per_analysis` | 인사이트 1회 호출에 넣을 최대 기사 수 |
| `report` | `top_n` | TOP N 기본값 |
| `paths` | `db` / `charts` / `reports` / `exports` / `log` | 산출물 경로 |
| `sources` | 언론사별 `feeds`(카테고리→RSS URL), `article`(본문 셀렉터) | 뉴스 소스 레지스트리 |

새 언론사를 추가하려면 `sources` 에 항목만 추가하면 됩니다 (코드 수정 불필요).

```json
"khan": {
  "name": "경향신문",
  "feeds": { "경제": "https://.../economy.xml" },
  "article": {
    "title_selectors": ["meta[property='og:title']", "h1"],
    "body_selectors": ["#articleBody"],
    "remove_selectors": ["script", "style", "figure"]
  }
}
```

### 환경변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | AI 기능 사용 시 | OpenAI API 키 |
| `OPENAI_BASE_URL` | 선택 | 프록시/호환 엔드포인트를 쓸 때 |

키를 전달하는 방법은 두 가지이고, **환경변수가 우선**입니다.

```bash
# 방법 1) 셸 환경변수 — 그 셸에서만 유효
export OPENAI_API_KEY='sk-...'

# 방법 2) .env 파일 — 실행 위치·터미널과 무관하게 읽힘 (cron 에 권장)
cp .env.example .env
# .env 를 열어 OPENAI_API_KEY 값을 채웁니다
```

`.env` 와 `config.json` 은 `.gitignore` 로 커밋에서 제외되어 있습니다. 키는 코드나 `config.example.json` 에 절대 넣지 마세요.

---

## 4. 모듈 구조

```
main.py                       엔트리포인트
src/
├── cli.py                    argparse 서브커맨드 정의·라우팅
├── config.py                 설정 로드 + 환경변수 병합
├── logger.py                 INFO/WARNING/ERROR 로깅 (콘솔 + 파일)
├── retry.py                  지수 백오프 재시도 (HTTP·AI 공용)
├── db.py                     SQLite 스키마·CRUD·집계
├── collectors/
│   ├── http_client.py        타임아웃·지연·robots.txt·재시도
│   ├── rss_collector.py      [방법1] RSS 로 기사 발견
│   ├── crawl_collector.py    [방법2] BeautifulSoup 본문 크롤링
│   └── pipeline_fetch.py     fetch 오케스트레이션
├── cleaner.py                정제 규칙 + 2겹 중복 처리
├── ai/
│   ├── client.py             OpenAI 래퍼 (JSON 강제·재시도·mock)
│   ├── summarize.py          요약
│   ├── analyze.py            인사이트 분석
│   └── sentiment.py          [보너스] 감성 라벨 처리
├── visualize.py              matplotlib 차트 3종 (한글 폰트)
├── report.py                 품질지표·TOP N·리포트
├── exporter.py               CSV / Excel
├── viewer.py                 [보너스] list / show
└── pipeline.py               run 오케스트레이션
```

### 데이터 저장 (SQLite, `data/news.db`)

| 테이블 | 역할 |
|---|---|
| `raw_articles` | 수집 원본 + 수집 시각·소스·수집 방법 |
| `clean_articles` | 정제 결과 (`url` UNIQUE 로 완전 중복 차단) |
| `summaries` | AI 요약·감성·길이 지표 |
| `insights` | AI 인사이트 분석 결과 |
| `fetch_state` | 피드별 증분 수집 상태 |

---

## 5. 설계 노트

### RSS 와 크롤링을 함께 쓰는 이유

| | RSS/API | 크롤링 |
|---|---|---|
| 장점 | 언론사가 공식 제공, 구조가 안정적, 서버 부하 적음 | 본문 전문 확보 가능, 제공 범위 제약 없음 |
| 단점 | 본문 전문이 없고 제공 항목·기간이 제한됨 | HTML 변경에 취약, 정책·부하 고려 필요 |

이 프로젝트는 **RSS 로 "무엇이 새로 나왔는지" 발견하고, 크롤링으로 "본문"을 채우는** 역할 분담을 씁니다.

### raw / clean 을 나누는 이유

- 원본을 남겨두면 정제 규칙이 바뀌어도 **다시 만들 수 있습니다** (재현성).
- "수집이 잘못된 것"과 "정제가 잘못된 것"을 분리해 디버깅할 수 있습니다.
- `raw_articles.crawl_status` 로 수집 성공률 같은 품질 지표를 계산합니다.

### 오류 처리 정책

- HTTP: 타임아웃(기본 10초) → 지수 백오프 재시도(기본 3회) → 그래도 실패하면 **로깅 후 그 건만 스킵**.
- AI: 같은 재시도 정책. 실패한 기사는 요약 없이 남고, 다음 실행 때 `--unsummarized` 로 다시 시도됩니다.
- 한 건의 실패가 파이프라인 전체를 멈추지 않는 것이 원칙입니다.

### 크롤링 윤리

- `robots.txt` 를 확인해 금지된 경로는 수집하지 않습니다 (`http.respect_robots`).
- 요청 간 최소 간격(`http.delay_sec`)을 두어 과도한 요청을 피합니다.
- 식별 가능한 User-Agent 를 보냅니다.

---

## 6. 정기 실행 스케줄링 (보너스)

일일 자동 실행용 래퍼 스크립트와 launchd 설정을 `scripts/` 에 포함해 두었습니다.

- `scripts/daily_run.sh` — 프로젝트 루트로 이동해 `run` 을 실행하고 시각을 로그에 남기는 래퍼 (launchd·cron 공용)
- `scripts/com.codyssey.newspipeline.plist` — macOS launchd 설정 (매일 오전 8시)

API 키는 `.env` 에서 자동으로 읽히므로(§3), 스케줄러 설정에 키를 넣을 필요가 없습니다.

### ⚠️ macOS 사용자 필독 — 프로젝트를 보호된 폴더에 두지 마세요

macOS 는 `~/Desktop`, `~/Documents`, `~/Downloads` 를 TCC(개인정보 보호)로 보호합니다.
launchd·cron 이 띄우는 백그라운드 프로세스는 이 폴더 접근이 **기본 차단**되어,
프로젝트가 여기 있으면 자동 실행이 `Operation not permitted` 로 실패합니다.
(수동 실행은 터미널이 권한을 가지므로 됩니다.)

해결: 프로젝트를 보호되지 않은 경로(예: `~/news-pipeline`)에 두거나,
시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한에 `/bin/bash` 를 추가하세요.

### macOS — launchd (권장)

```bash
# plist 안의 경로가 실제 프로젝트 위치와 맞는지 확인 후
cp scripts/com.codyssey.newspipeline.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.codyssey.newspipeline.plist

# 즉시 한 번 실행해 확인
launchctl start com.codyssey.newspipeline
tail -f logs/launchd.log

# 해제
launchctl unload ~/Library/LaunchAgents/com.codyssey.newspipeline.plist
```

실행 시각을 바꾸려면 plist 의 `StartCalendarInterval` (Hour/Minute) 을 수정한 뒤 unload → load 하세요.

### macOS / Linux — cron (대안)

```bash
crontab -e
```

```cron
# 매일 오전 8시. 래퍼 스크립트가 경로 이동·로깅을 처리하므로 한 줄로 끝난다.
0 8 * * * /Users/sejin/news-pipeline/scripts/daily_run.sh >> /Users/sejin/news-pipeline/logs/cron.log 2>&1
```

- 반드시 **절대 경로**를 쓰세요. 키는 `.env` 에서 읽히므로 crontab 에 넣지 않아도 됩니다.
- macOS 는 cron 에도 전체 디스크 접근 권한(`/usr/sbin/cron`)이 필요할 수 있습니다 — 위 TCC 주의 참고.
- 증분 수집이 켜져 있어 재실행해도 같은 기사를 다시 저장하지 않습니다.

### Windows — 작업 스케줄러

```powershell
schtasks /create /tn "NewsPipeline" /tr "C:\path\to\news-pipeline\.venv\Scripts\python.exe main.py run --limit 20" /sc daily /st 08:00
```

GUI로 만들 때는 [작업 만들기] → [트리거] 매일 08:00 → [동작] 프로그램 시작에
`.venv\Scripts\python.exe`, 인수 `main.py run --limit 20`, 시작 위치에 프로젝트 폴더를 지정합니다.
Windows 에는 TCC 제한이 없어 폴더 위치 제약은 없습니다.

---

## 7. 심화 분석 — 키워드 트렌드 · 이벤트 클러스터링

### `trends` — 키워드 시계열

날짜별 제목 키워드를 집계해 **뜨고 지는 흐름**을 보여줍니다. AI 호출 없는 순수 집계라 항상 동작합니다.

```bash
python main.py trends --days 7
```

- 오늘 많이 나온 키워드
- **🆕 새로 등장한 키워드** (이전 기간엔 없다가 오늘 나타남)
- **📈 급상승 키워드** (이전 일평균 대비 급증)
- 상위 키워드의 날짜별 언급 추이 다중 선그래프 (`output/charts/keyword_timeline.png`)

### `cluster` — 이벤트 클러스터링 + 언론사 논조 비교

제목 글자 일치(`title_hash`)로는 못 잡는 "같은 사건을 다르게 표현한 기사"를,
**임베딩(의미 벡터)의 코사인 유사도**로 묶습니다. 하나의 이벤트가 여러 언론사에 걸치면
**같은 사안을 언론사별로 어떤 논조(감성)로 다뤘는지** 비교합니다.

```bash
python main.py cluster --date-from 2026-07-27 --date-to 2026-07-28
python main.py cluster --threshold 0.45 --min-sources 2   # 임계값↓ = 더 넓게 묶임
python main.py cluster --mock                              # 임베딩 API 없이 오프라인(해싱 벡터)
```

출력 예시 — 같은 사건인데 경제지와 통신사의 논조가 갈리는 게 드러납니다:

```
[이벤트 1] 기사 12건 · 언론사 2곳 · 2026-07-27~2026-07-28
  대표: AI깐부 맞네...엔비디아, 네이버 3대주주로
  논조 비교:
    - hankyung : 중립 4, 긍정 7
    - yonhap   : 중립 1
  → 논조 갈림: 긍정 7건 vs 중립 5건
```

- 알고리즘: 임계값 기반 그리디 응집 클러스터링 (numpy, 외부 ML 라이브러리 불필요)
- 임베딩은 `text-embedding-3-small` (1536차원), **DB 에 캐시**해 재실행 시 재계산하지 않음
- `--mock` 은 문자 n-gram 해싱 벡터로 오프라인 시연 (표면적 유사도)

---

## 8. 상태 점검 · 모니터링

자동 실행이 조용히 실패하거나 요약이 밀리는 것을 놓치지 않도록 상태 점검 수단을 두었습니다.

```bash
python main.py status          # 한 화면 헬스 대시보드
python main.py status --check  # 문제가 있으면 종료코드 1 (스크립트 연동용)
```

`status` 가 보여주는 것:

- **자동 실행**: 마지막 실행 시각·성패 (`logs/last_run.txt` 기반), 오늘 실행 누락 여부
- **오늘 수집**: 오늘 수집된 기사 수 (실제 수집 시각 기준)
- **파이프라인 단계**: raw / clean / 요약 건수와 커버리지, 정제·요약 대기 건수
- **최근 수집 추이**: 최근 5일 막대
- **AI 인사이트**: 마지막 분석 시각
- 문제가 있으면 하단에 **경고 + 다음 조치 명령**을 모아 보여줍니다.

**실패 알림**: `scripts/daily_run.sh` 는 실행 결과를 `logs/last_run.txt` 에 기록하고,
실패 시(네트워크 미준비·파이프라인 오류) **macOS 알림**을 띄웁니다. (Linux/cron 에서는 알림만 조용히 생략)

---

## 9. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `OPENAI_API_KEY 가 설정되어 있지 않습니다` | 환경변수 설정 또는 `--mock` 사용 |
| 차트 한글이 네모(두부)로 나옴 | 한글 폰트 없음. macOS는 기본 제공, Linux는 `sudo apt install fonts-nanum` 후 재실행 |
| `RSS 수집 실패` 로그 | 피드 URL 변경 가능성 → `config.json` 의 `feeds` 확인 |
| `본문 크롤링 실패` 가 많음 | 사이트 HTML 구조 변경 → `article.body_selectors` 갱신 |
| 같은 기사가 반복 수집됨 | `--no-incremental` 를 쓰고 있진 않은지 확인 |
| 수집은 됐는데 clean 이 0건 | 본문 길이 미달일 수 있음 → `--min-body` 낮춰 확인 |

로그는 콘솔과 `logs/pipeline.log` 에 동시에 남습니다 (`--verbose` 로 DEBUG까지).
