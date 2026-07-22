#!/usr/bin/env python3
"""뉴스 데이터 파이프라인 CLI 엔트리포인트.

사용법:
    python main.py <서브커맨드> [옵션]
    python main.py --help
"""

import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
