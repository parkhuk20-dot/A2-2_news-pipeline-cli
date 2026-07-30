"""텍스트 임베딩 — 이벤트 클러스터링용 벡터 생성.

- 실제 모드: OpenAI text-embedding-3-small (1536차원, 배치 호출)
- mock/오프라인 모드: 문자 2-gram + 단어 토큰을 해싱한 벡터.
  API 없이도 '표면적으로 비슷한 제목'이 가깝게 나와 클러스터링을 시연할 수 있다.

계산 비용을 아끼려고 결과는 DB(embeddings 테이블)에 캐시한다.
"""

from __future__ import annotations

import hashlib
import os
import re

import numpy as np

from ..config import Config
from ..logger import get_logger
from ..retry import retry_call

log = get_logger("embed")

MOCK_MODEL = "mock-hash-256"
MOCK_DIM = 256
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


def _mock_vector(text: str) -> list[float]:
    """해싱 기반 오프라인 임베딩 (단어 토큰 + 문자 2-gram)."""
    vec = np.zeros(MOCK_DIM, dtype=float)
    tokens = _TOKEN.findall((text or "").lower())
    grams = list(tokens)
    for tok in tokens:
        grams.extend(tok[i : i + 2] for i in range(len(tok) - 1))
    for g in grams:
        idx = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16) % MOCK_DIM
        vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm else vec.tolist()


class Embedder:
    """임베딩 생성기. 모델명(model)으로 캐시를 구분한다."""

    def __init__(self, cfg: Config, mock: bool = False):
        self.mock = bool(mock) or cfg.ai.get("provider") == "mock"
        self.max_retries = cfg.http.get("max_retries", 3)
        self.backoff_base = cfg.http.get("backoff_base", 1.6)
        self._client = None

        if self.mock:
            self.model = MOCK_MODEL
            log.warning("mock 임베딩 사용 (해싱 벡터) — 실제 의미가 아닌 표면적 유사도")
            return

        self.model = cfg.ai.get("embedding_model", "text-embedding-3-small")
        api_key = cfg.api_key
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY 가 없습니다. --mock 으로 오프라인 임베딩을 쓰거나 키를 설정하세요."
            )
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            timeout=cfg.ai.get("request_timeout", 60),
            max_retries=0,  # 재시도는 retry_call 이 담당
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """여러 텍스트를 임베딩. mock 이면 해싱 벡터."""
        if self.mock:
            return [_mock_vector(t) for t in texts]

        out: list[list[float]] = []
        # OpenAI 임베딩은 배치 호출 가능 — 100개씩 끊어 보낸다
        for i in range(0, len(texts), 100):
            batch = [t[:6000] for t in texts[i : i + 100]]

            def _call():
                resp = self._client.embeddings.create(model=self.model, input=batch)
                return [d.embedding for d in resp.data]

            vectors = retry_call(
                _call,
                max_retries=self.max_retries,
                backoff_base=self.backoff_base,
                exceptions=(Exception,),
                label=f"임베딩({i}~{i+len(batch)})",
            )
            out.extend(vectors)
        return out
