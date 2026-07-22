"""로깅 설정 모듈.

콘솔에는 `[INFO] 메시지` 형태로 간결하게, 파일에는 시각·모듈명까지 남긴다.
INFO / WARNING / ERROR 3레벨을 모두 사용한다.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOGGER_NAME = "news"

CONSOLE_FORMAT = "[%(levelname)s] %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(log_file: Path | None = None, verbose: bool = False) -> logging.Logger:
    """루트 파이프라인 로거를 구성해 돌려준다. 중복 호출해도 핸들러가 쌓이지 않는다."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    # 재호출 시 기존 핸들러 정리 (테스트·run 커맨드에서 여러 번 불릴 수 있음)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        logger.addHandler(file_handler)

    # 외부 라이브러리 로그가 콘솔을 어지럽히지 않게 낮춰둔다
    for noisy in ("urllib3", "openai", "httpx", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger


def get_logger(module: str | None = None) -> logging.Logger:
    """모듈별 자식 로거. setup_logging 의 핸들러를 그대로 공유한다."""
    return logging.getLogger(LOGGER_NAME if not module else f"{LOGGER_NAME}.{module}")
