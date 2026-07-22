"""지수 백오프 재시도 유틸 (HTTP 수집 · AI 호출 공용).

일시적인 타임아웃이나 5xx, 레이트리밋 때문에 파이프라인 전체가 죽지 않도록
정해진 횟수만큼 간격을 늘려가며 다시 시도하고, 끝내 실패하면 예외를 올려보낸다.
호출한 쪽에서 그 예외를 잡아 "로깅 후 스킵"하는 것이 이 프로젝트의 기본 정책이다.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, TypeVar

from .logger import get_logger

T = TypeVar("T")
log = get_logger("retry")


class RetryError(Exception):
    """모든 재시도가 실패했을 때 발생."""


def retry_call(
    func: Callable[[], T],
    *,
    max_retries: int = 3,
    backoff_base: float = 1.6,
    exceptions: Iterable[type[BaseException]] = (Exception,),
    label: str = "요청",
) -> T:
    """func 를 최대 max_retries 회까지 재시도한다.

    대기 시간은 backoff_base ** (시도 횟수) 초 — 예: 1.6, 2.56, 4.1초.
    """
    attempt = 0
    last_error: BaseException | None = None
    exc_tuple = tuple(exceptions)

    while attempt < max_retries:
        attempt += 1
        try:
            return func()
        except exc_tuple as e:  # noqa: PERF203 - 재시도 구조상 루프 내 try 가 필요
            last_error = e
            if attempt >= max_retries:
                break
            wait = backoff_base**attempt
            log.warning(
                "%s 실패 (%d/%d): %s → %.1f초 후 재시도",
                label,
                attempt,
                max_retries,
                e,
                wait,
            )
            time.sleep(wait)

    raise RetryError(f"{label} {max_retries}회 시도 모두 실패: {last_error}") from last_error
