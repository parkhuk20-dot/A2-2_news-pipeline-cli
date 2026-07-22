"""OpenAI 호출 래퍼.

설계 포인트
  - **JSON 응답 강제**: response_format={"type": "json_object"} 로 받아 자연어 파싱을 없앤다.
  - **재시도**: 레이트리밋·일시 오류는 지수 백오프로 다시 시도한다.
  - **mock 모드**: API 키·비용 없이 전체 파이프라인을 검증·시연할 수 있게 한다.
  - **키 관리**: 키는 환경변수(OPENAI_API_KEY)에서만 읽는다. 코드·설정 파일에 두지 않는다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from ..config import Config
from ..logger import get_logger
from ..retry import retry_call

log = get_logger("ai")


class AIError(Exception):
    """AI 호출이 최종적으로 실패했을 때."""


class AIClient:
    def __init__(self, cfg: Config, mock: bool = False):
        ai_cfg = cfg.ai
        self.model: str = ai_cfg.get("model", "gpt-4o-mini")
        self.temperature: float = ai_cfg.get("temperature", 0.2)
        self.max_retries: int = cfg.http.get("max_retries", 3)
        self.backoff_base: float = cfg.http.get("backoff_base", 1.6)
        self.mock: bool = bool(mock) or ai_cfg.get("provider") == "mock"
        self._client: Any = None

        if self.mock:
            log.warning("mock 모드 — 실제 AI API 를 호출하지 않고 모의 응답을 사용합니다")
            self.model = f"mock:{self.model}"
            return

        api_key = cfg.api_key
        if not api_key:
            raise AIError(
                "환경변수 OPENAI_API_KEY 가 설정되어 있지 않습니다.\n"
                "  export OPENAI_API_KEY='sk-...'  로 설정하거나, --mock 옵션으로 실행하세요."
            )
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise AIError("openai 패키지가 없습니다. pip install -r requirements.txt") from e

        self._client = OpenAI(api_key=api_key, base_url=os.environ.get("OPENAI_BASE_URL") or None)

    # ------------------------------------------------------------------
    def json_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        label: str = "AI 호출",
        mock_result: Callable[[], dict] | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """JSON 객체 하나를 돌려받는다. 실패하면 AIError."""
        if self.mock:
            return mock_result() if mock_result else {}

        def _call() -> str:
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or "{}"

        try:
            raw = retry_call(
                _call,
                max_retries=self.max_retries,
                backoff_base=self.backoff_base,
                exceptions=(Exception,),
                label=label,
            )
        except Exception as e:
            raise AIError(str(e)) from e

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AIError(f"JSON 파싱 실패: {e} / 응답 앞부분: {raw[:120]}") from e

        if not isinstance(data, dict):
            raise AIError(f"JSON 객체가 아닌 응답: {type(data).__name__}")
        return data
