"""설정 로드 모듈.

config.json 을 읽어 기본값과 병합하고, API 키는 환경변수에서만 가져온다.
(과제 제약: API 키를 코드나 설정 파일에 직접 쓰지 않는다.)
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

# 프로젝트 루트 = src/ 의 부모
ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "http": {
        "timeout": 10,
        "delay_sec": 1.0,
        "max_retries": 3,
        "backoff_base": 1.6,
        "user_agent": "Mozilla/5.0 (compatible; NewsPipelineBot/1.0; educational project)",
        "respect_robots": True,
    },
    "fetch": {"default_limit": 20, "incremental": True, "seen_guid_cap": 500},
    "dedup": {"policy": "skip", "dedup_similar": False},
    "ai": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "request_timeout": 60,
        "temperature": 0.2,
        "summary_max_chars": 200,
        "max_articles_per_analysis": 60,
        "analysis_body_chars": 400,
    },
    "report": {"top_n": 5},
    "paths": {
        "db": "data/news.db",
        "charts": "output/charts",
        "reports": "output/reports",
        "exports": "output/exports",
        "log": "logs/pipeline.log",
    },
    "sources": {},
}

API_KEY_ENV = "OPENAI_API_KEY"
ENV_FILE = ROOT / ".env"


def load_dotenv(path: Path = ENV_FILE) -> list[str]:
    """프로젝트 루트의 .env 를 읽어 환경변수에 채운다 (이미 있는 값은 덮지 않는다).

    셸에서 export 한 값은 그 셸에서만 유효해서, cron 이나 다른 터미널에서 실행하면
    키를 못 찾는다. .env 파일에 두면 실행 위치와 무관하게 읽힌다.
    이 파일은 .gitignore 에 있어 커밋되지 않는다.
    """
    if not path.exists():
        return []

    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


class ConfigError(Exception):
    """설정 파일이 없거나 형식이 잘못된 경우."""


def _deep_merge(base: dict, override: dict) -> dict:
    """override 값으로 base 를 재귀 병합한 새 dict 를 돌려준다."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Config:
    """설정 접근용 얇은 래퍼."""

    def __init__(self, data: dict[str, Any], path: Path):
        self._data = data
        self.path = path

    # --- 섹션 접근 -----------------------------------------------------
    @property
    def http(self) -> dict:
        return self._data["http"]

    @property
    def fetch(self) -> dict:
        return self._data["fetch"]

    @property
    def dedup(self) -> dict:
        return self._data["dedup"]

    @property
    def ai(self) -> dict:
        return self._data["ai"]

    @property
    def report(self) -> dict:
        return self._data["report"]

    @property
    def sources(self) -> dict:
        return self._data["sources"]

    def get(self, dotted: str, default: Any = None) -> Any:
        """'http.timeout' 처럼 점 표기로 값을 꺼낸다."""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # --- 경로 ----------------------------------------------------------
    def path_for(self, key: str, ensure_parent: bool = True) -> Path:
        """paths 섹션의 상대 경로를 프로젝트 루트 기준 절대 경로로 바꾼다."""
        raw = self._data["paths"][key]
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        if ensure_parent:
            target = p if p.suffix == "" else p.parent
            target.mkdir(parents=True, exist_ok=True)
        return p

    # --- API 키 --------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        """API 키는 환경변수에서만 읽는다."""
        key = os.environ.get(API_KEY_ENV, "").strip()
        return key or None

    # --- 소스 선택 -----------------------------------------------------
    def source_names(self) -> list[str]:
        return list(self._data["sources"].keys())

    def resolve_sources(self, spec: str | None) -> list[str]:
        """--source 옵션 문자열을 실제 소스 키 목록으로 변환한다.

        'all' → 전체, 'random' → 무작위 1개, 'a,b' → 지정한 것들.
        """
        import random

        available = self.source_names()
        if not available:
            raise ConfigError("config.json 의 sources 가 비어 있습니다.")
        if not spec or spec == "all":
            return available
        if spec == "random":
            return [random.choice(available)]
        wanted = [s.strip() for s in spec.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in available]
        if unknown:
            raise ConfigError(
                f"등록되지 않은 소스: {', '.join(unknown)} (사용 가능: {', '.join(available)})"
            )
        return wanted


def load_config(path: str | Path | None = None) -> Config:
    """설정 파일을 읽어 Config 객체를 만든다."""
    load_dotenv()  # API 키는 환경변수 → 없으면 .env 순으로 찾는다
    cfg_path = Path(path) if path else ROOT / "config.json"
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path

    if not cfg_path.exists():
        raise ConfigError(
            f"설정 파일이 없습니다: {cfg_path}\n"
            "config.example.json 을 config.json 으로 복사한 뒤 사용하세요."
        )
    try:
        with cfg_path.open(encoding="utf-8") as f:
            user_data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 파일 JSON 형식 오류: {cfg_path} ({e})") from e

    return Config(_deep_merge(DEFAULTS, user_data), cfg_path)
