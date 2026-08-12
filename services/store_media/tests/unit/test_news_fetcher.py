import json
from unittest.mock import patch

from app.infrastructure.news_fetcher import OfficialNewsFetcher


def test_parse_chengdu_association_html_keeps_articles_only() -> None:
    content = """
    <nav><a href="/Infor/type/typeid/22.html">通知公告</a></nav>
    <a href="/Infor/index/id/8630.html">@居民朋友，这份住房需求问卷，期待您的参与</a>
    <a href="/about.html">关于我们</a>
    <a href="/Infor/index/id/8629.html"><span>关于坚决抵制非法金融活动的倡议书</span></a>
    """
    fetcher = OfficialNewsFetcher("https://www.cdfangxie.com/Infor/type/typeid/22.html", limit=10)

    items = fetcher._parse_content(content, "https://www.cdfangxie.com/Infor/type/typeid/22.html")

    assert [item.title for item in items] == [
        "@居民朋友，这份住房需求问卷，期待您的参与",
        "关于坚决抵制非法金融活动的倡议书",
    ]
    assert all(item.url.startswith("https://www.cdfangxie.com/Infor/index/id/") for item in items)
    assert fetcher.source_label == "成都房协 · 通知公告"


def test_parse_xinhua_json_payload_still_works() -> None:
    content = json.dumps(
        {
            "datasource": [
                {"showTitle": "成都住房市场观察", "publishUrl": "/house/demo.html", "publishTime": "2026-08-11"},
            ]
        }
    )
    fetcher = OfficialNewsFetcher("http://www.news.cn/house/feed.json", limit=10)

    items = fetcher._parse_content(content, "http://www.news.cn/house/feed.json")

    assert items[0].title == "成都住房市场观察"
    assert items[0].url == "https://www.news.cn/house/demo.html"
    assert fetcher.source_label == "新华网 · 房产频道"


def test_fetch_uses_next_source_when_first_source_fails() -> None:
    fetcher = OfficialNewsFetcher(
        "https://www.cdfangxie.com/Infor/type/typeid/22.html,http://www.news.cn/house/feed.json",
        limit=10,
    )
    payload = json.dumps({"datasource": [{"showTitle": "新华网备用资讯", "publishUrl": "/house/demo.html"}]})

    class Response:
        def __init__(self, body: str):
            self.headers = self
            self.body = body.encode()

        def get_content_charset(self):
            return "utf-8"

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_urlopen(request, timeout):
        if "cdfangxie" in request.full_url:
            raise OSError("temporary source outage")
        return Response(payload)

    with patch("app.infrastructure.news_fetcher.urlopen", side_effect=fake_urlopen):
        items = fetcher.latest()

    assert items[0].title == "新华网备用资讯"
    assert fetcher.source_label == "新华网 · 房产频道"
