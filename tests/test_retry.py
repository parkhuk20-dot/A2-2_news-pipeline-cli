"""재시도 유틸 단위 테스트 — sleep 을 패치해 빠르게 검증."""

import pytest

from src import retry
from src.retry import RetryError, retry_call


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda *_: None)


def test_success_first_try():
    calls = []
    assert retry_call(lambda: calls.append(1) or "ok", max_retries=3) == "ok"
    assert len(calls) == 1


def test_succeeds_after_retries():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("일시 오류")
        return "ok"

    assert retry_call(flaky, max_retries=3) == "ok"
    assert state["n"] == 3


def test_all_attempts_fail_raises_retry_error():
    state = {"n": 0}

    def always_fail():
        state["n"] += 1
        raise ValueError("계속 실패")

    with pytest.raises(RetryError):
        retry_call(always_fail, max_retries=3)
    assert state["n"] == 3


def test_only_listed_exceptions_are_retried():
    def raises_key():
        raise KeyError("안 잡힘")

    with pytest.raises(KeyError):
        retry_call(raises_key, max_retries=3, exceptions=(ValueError,))
