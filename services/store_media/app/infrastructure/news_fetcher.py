from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    published_at: str


class _NewsLinkParser(HTMLParser):
    """Extract article links from the public Chengdu housing news pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href = ""
        self._parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href") or ""
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        title = " ".join("".join(self._parts).split())
        if title:
            self.links.append((self._href, title))
        self._href = ""
        self._parts = []


class OfficialNewsFetcher:
    """抓取并缓存成都本地官方/行业权威房产资讯，供顶部跑马灯使用。"""

    def __init__(
        self,
        feed_url: str,
        *,
        limit: int = 20,
        cache_seconds: int = 1800,
        request_timeout: float = 15.0,
        user_agent: str = "store-media-big-screen/0.1",
    ):
        self._feed_urls = tuple(
            url.strip() for url in feed_url.split(",") if url.strip()
        )
        self._active_feed_url = self._feed_urls[0] if self._feed_urls else ""
        self._limit = limit
        self._cache_seconds = cache_seconds
        self._timeout = request_timeout
        self._user_agent = user_agent
        self._items: list[NewsItem] | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @property
    def source_label(self) -> str:
        if "cdfangxie.com" in self._active_feed_url:
            return "成都房协 · 通知公告"
        if "cdzj.chengdu.gov.cn" in self._active_feed_url:
            return "成都市住建局"
        return "新华网 · 房产频道"

    def latest(self) -> list[NewsItem]:
        with self._lock:
            cached = self._items
            if cached is not None and time.monotonic() - self._fetched_at < self._cache_seconds:
                return cached
        fetched = self._fetch()
        if fetched:
            with self._lock:
                self._items = fetched
                self._fetched_at = time.monotonic()
            return fetched
        with self._lock:
            return list(self._items or [])

    def _fetch(self) -> list[NewsItem]:
        for feed_url in self._feed_urls:
            try:
                request = Request(feed_url, headers={"User-Agent": self._user_agent})
                with urlopen(request, timeout=self._timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    content = response.read().decode(charset, errors="replace")
            except Exception:
                continue

            items = self._parse_content(content, feed_url)
            if items:
                self._active_feed_url = feed_url
                return items
        return []

    def _parse_content(self, content: str, feed_url: str) -> list[NewsItem]:
        try:
            payload = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._parse_html(content, feed_url)

        items: list[NewsItem] = []
        for entry in payload.get("datasource") or []:
            title = (entry.get("showTitle") or "").strip()
            href = (entry.get("publishUrl") or "").strip()
            if not title or not href:
                continue
            url = href if href.startswith("http") else "https://www.news.cn" + href
            items.append(NewsItem(title=title, url=url, published_at=(entry.get("publishTime") or "").strip()))
            if len(items) >= self._limit:
                break
        return items

    def _parse_html(self, content: str, feed_url: str) -> list[NewsItem]:
        parser = _NewsLinkParser()
        try:
            parser.feed(content)
        except Exception:
            return []
        items: list[NewsItem] = []
        seen: set[str] = set()
        for href, title in parser.links:
            # 成都房协列表页的文章链接形如 /Infor/index/id/8630.html；
            # 跳过导航、栏目和搜索链接，避免把菜单文字放进跑马灯。
            if "/Infor/index/id/" not in href or href in seen:
                continue
            if not 4 <= len(title) <= 120:
                continue
            seen.add(href)
            items.append(
                NewsItem(
                    title=title,
                    url=urljoin(feed_url, href),
                    published_at="",
                )
            )
            if len(items) >= self._limit:
                break
        return items
