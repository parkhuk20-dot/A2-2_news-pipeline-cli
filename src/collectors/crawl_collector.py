"""방법2 — 기사 페이지 크롤링 (본문 '확보' 담당).

RSS 로 얻은 링크를 직접 방문해 BeautifulSoup 으로 본문을 파싱한다.
셀렉터는 config.json 의 sources.<키>.article 에 외부화해, 사이트 구조가 바뀌어도
코드 수정 없이 대응할 수 있게 했다.

장점: RSS 가 주지 않는 본문 전문을 얻을 수 있다.
단점: HTML 구조 변경에 취약하고, 사이트 정책·부하를 고려해야 한다.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..logger import get_logger
from .http_client import HttpClient

log = get_logger("crawl")

DEFAULT_TITLE_SELECTORS = ["meta[property='og:title']", "h1"]
DEFAULT_BODY_SELECTORS = ["article", "#article", ".article-body"]
DEFAULT_REMOVE_SELECTORS = ["script", "style", "figure", "aside", "nav"]


def _select_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element is None:
            continue
        if element.name == "meta":
            content = (element.get("content") or "").strip()
            if content:
                return content
            continue
        text = element.get_text(" ", strip=True)
        if text:
            return text
    return None


def extract_article(html: str, article_cfg: dict) -> tuple[str | None, str | None]:
    """HTML 에서 (제목, 본문) 을 뽑는다. 못 찾으면 None."""
    soup = BeautifulSoup(html, "lxml")

    for selector in article_cfg.get("remove_selectors", DEFAULT_REMOVE_SELECTORS):
        for element in soup.select(selector):
            element.decompose()

    title = _select_text(soup, article_cfg.get("title_selectors", DEFAULT_TITLE_SELECTORS))
    body = _select_text(soup, article_cfg.get("body_selectors", DEFAULT_BODY_SELECTORS))
    return title, body


def crawl_body(client: HttpClient, url: str, article_cfg: dict) -> tuple[str | None, str | None]:
    """기사 1건의 본문을 가져온다. 실패는 예외 대신 (None, None) 으로 돌려준다.

    한 기사가 실패해도 파이프라인 전체는 계속 진행되어야 하기 때문이다.
    """
    try:
        html = client.get_text(url, label="본문 크롤링")
    except PermissionError as e:
        log.warning("%s", e)
        return None, None
    except Exception as e:  # RetryError 포함 — 재시도까지 모두 실패한 경우
        log.warning("본문 크롤링 실패: %s (%s)", url, e)
        return None, None

    try:
        return extract_article(html, article_cfg)
    except Exception as e:
        log.warning("본문 파싱 실패: %s (%s)", url, e)
        return None, None
