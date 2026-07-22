"""수집용 공통 HTTP 클라이언트.

- 타임아웃 설정 (요구사항)
- 지수 백오프 재시도
- 요청 간 지연 (크롤링 윤리: 과도한 요청 금지)
- robots.txt 준수 확인
"""

from __future__ import annotations

import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from ..logger import get_logger
from ..retry import retry_call

log = get_logger("http")

# 재시도할 가치가 있는 오류들 (일시적)
RETRYABLE = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.HTTPError,
    requests.exceptions.ChunkedEncodingError,
)


class HttpClient:
    """세션 재사용 + 지연 + robots 확인을 담당하는 얇은 래퍼."""

    def __init__(self, http_cfg: dict):
        self.timeout = http_cfg.get("timeout", 10)
        self.delay = http_cfg.get("delay_sec", 1.0)
        self.max_retries = http_cfg.get("max_retries", 3)
        self.backoff_base = http_cfg.get("backoff_base", 1.6)
        self.respect_robots = http_cfg.get("respect_robots", True)
        self.user_agent = http_cfg.get("user_agent", "NewsPipelineBot/1.0")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Language": "ko-KR,ko;q=0.9",
            }
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request_at = 0.0

    # ------------------------------------------------------------------
    def close(self) -> None:
        self.session.close()

    def _wait_turn(self) -> None:
        """직전 요청으로부터 delay 초가 지나지 않았으면 남은 만큼 쉰다."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def can_fetch(self, url: str) -> bool:
        """robots.txt 확인. 읽을 수 없으면 보수적으로 허용하되 경고를 남긴다."""
        if not self.respect_robots:
            return True

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            robots_url = urljoin(origin, "/robots.txt")
            try:
                resp = self.session.get(robots_url, timeout=self.timeout)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    log.warning("robots.txt 응답 %s (%s) → 허용으로 간주", resp.status_code, origin)
                    parser = None
            except requests.RequestException as e:
                log.warning("robots.txt 조회 실패 (%s): %s → 허용으로 간주", origin, e)
                parser = None
            self._robots[origin] = parser

        parser = self._robots[origin]
        if parser is None:
            return True
        allowed = parser.can_fetch(self.user_agent, url)
        if not allowed:
            log.warning("robots.txt 가 수집을 금지한 URL: %s", url)
        return allowed

    # ------------------------------------------------------------------
    def get(self, url: str, *, label: str = "요청", check_robots: bool = True) -> requests.Response:
        """GET 요청 (타임아웃 + 재시도 + 지연). 실패하면 RetryError 를 올린다."""
        if check_robots and not self.can_fetch(url):
            raise PermissionError(f"robots.txt 정책상 수집 불가: {url}")

        def _do() -> requests.Response:
            self._wait_turn()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            finally:
                self._last_request_at = time.monotonic()
            resp.raise_for_status()
            return resp

        return retry_call(
            _do,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            exceptions=RETRYABLE,
            label=f"{label} ({url})",
        )

    def get_text(self, url: str, *, label: str = "요청", check_robots: bool = True) -> str:
        resp = self.get(url, label=label, check_robots=check_robots)
        # 한글 깨짐 방지: 헤더에 charset 이 없으면 apparent_encoding 사용
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text
