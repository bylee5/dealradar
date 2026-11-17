# -*- coding: utf-8 -*-
"""RSS를 제공하는 사이트: 뽐뿌, 루리웹."""
import re
import xml.etree.ElementTree as ET

from dealradar.crawlers.base import Deal, fetch, parse_rfc822, to_int


def _items(xml_text):
    root = ET.fromstring(xml_text.encode("utf-8"))
    for item in root.iter("item"):
        yield {child.tag: (child.text or "") for child in item}


class Ppomppu(object):
    key = "ppomppu"
    name = "뽐뿌"
    home = "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu"
    # 뽐뿌게시판(국내) + 해외뽐뿌
    feeds = [
        ("ppomppu", "https://www.ppomppu.co.kr/rss.php?id=ppomppu"),
        ("ppomppu4", "https://www.ppomppu.co.kr/rss.php?id=ppomppu4"),
    ]

    def crawl(self):
        deals = []
        for board, url in self.feeds:
            for it in _items(fetch(url)):
                link = it.get("link", "").replace("http://", "https://")
                m = re.search(r"no=(\d+)", link)
                if not m:
                    continue
                # <hits> [0|51|0|0] : 두 번째 값이 조회수
                hits = re.findall(r"\d+", it.get("hits", ""))
                views = int(hits[1]) if len(hits) > 1 else 0
                title = it.get("title", "")
                deals.append(Deal(
                    self.key, "%s-%s" % (board, m.group(1)), title, link,
                    author=it.get("author"),
                    posted_at=parse_rfc822(it.get("pubDate", "")),
                    views=views,
                    source_category="해외" if board == "ppomppu4" else None,
                    sold_out="품절" in title or "종료" in title,
                ))
        return deals


class Ruliweb(object):
    key = "ruliweb"
    name = "루리웹"
    home = "https://bbs.ruliweb.com/market/board/1020"
    feed = "https://bbs.ruliweb.com/market/board/1020/rss"

    def crawl(self):
        deals = []
        for it in _items(fetch(self.feed)):
            link = it.get("link", "")
            m = re.search(r"/read/(\d+)", link)
            if not m:
                continue
            thumb = re.search(r'src="([^"]+)"', it.get("description", ""))
            title = it.get("title", "")
            deals.append(Deal(
                self.key, m.group(1), title, link,
                author=it.get("author"),
                posted_at=parse_rfc822(it.get("pubDate", "")),
                source_category=it.get("category"),
                thumbnail=thumb.group(1) if thumb else None,
                sold_out="종료" in title or "품절" in title,
            ))
        return deals
