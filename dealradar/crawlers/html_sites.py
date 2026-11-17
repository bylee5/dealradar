# -*- coding: utf-8 -*-
"""HTML 목록을 파싱하는 사이트: 클리앙, 퀘이사존, 펨코, 아카라이브.

사이트 구조가 바뀌면 이 파일의 선택자만 고치면 된다.
"""
import re

from bs4 import BeautifulSoup

from dealradar.crawlers.base import (
    Deal, abs_url, clean_text, fetch, parse_iso_utc, parse_loose_time,
    parse_price, to_int,
)


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _text(el):
    return clean_text(el.get_text(" ")) if el is not None else ""


class Clien(object):
    key = "clien"
    name = "클리앙"
    base = "https://www.clien.net"
    home = "https://www.clien.net/service/board/jirum"

    def crawl(self):
        soup = _soup(fetch(self.home))
        deals = []
        for row in soup.select("div.list_item.symph_row"):
            classes = row.get("class") or []
            if "notice" in classes or "blocked" in classes:
                continue
            sn = row.get("data-board-sn")
            a = row.select_one("span.list_subject > a") or row.select_one("span.list_subject a")
            if not sn or a is None:
                continue
            title = _text(a)
            reply = row.select_one(".list_reply span")
            ts = row.select_one(".list_time .timestamp")
            thumb = row.select_one(".list_thumbnail img")
            nick = row.select_one(".list_author .nickname")
            kw = row.select_one(".keyword .icon_keyword")
            deals.append(Deal(
                self.key, sn, title, abs_url(self.base, a.get("href", "").split("?")[0]),
                comments=to_int(row.get("data-comment-count") or _text(reply)),
                views=to_int(_text(row.select_one(".list_hit .hit"))),
                posted_at=parse_loose_time(_text(ts)) if ts else None,
                thumbnail=thumb.get("src") if thumb else None,
                author=_text(nick) or row.get("data-author-id"),
                source_category=_text(kw) or None,
                sold_out="sold_out" in classes,
            ))
        return deals


class Quasarzone(object):
    key = "quasarzone"
    name = "퀘이사존"
    base = "https://quasarzone.com"
    home = "https://quasarzone.com/bbs/qb_saleinfo"

    def crawl(self):
        soup = _soup(fetch(self.home))
        deals = []
        for row in soup.select("div.v2-list-row--hotdeal"):
            qid = row.get("data-qc-id")
            a = row.select_one("a.subject-link")
            if not qid or a is None:
                continue
            classes = row.get("class") or []
            badges = [_text(b) for b in row.select(".v2-list-row__line1 .v2-badge")]
            badges = [b for b in badges if b and "인기" not in b]
            price_txt = _text(row.select_one(".v2-list-row__price"))
            ship_el = row.select(".v2-list-row__ship")
            ship_txt = ""
            shop = None
            for el in ship_el:
                img = el.find("img")
                if img is not None and img.get("alt"):
                    shop = img.get("alt")
                elif _text(el):
                    ship_txt = _text(el)
            deals.append(Deal(
                self.key, qid, _text(a), abs_url(self.base, a.get("href")),
                price=parse_price(price_txt),
                price_text=price_txt or None,
                shipping=ship_txt or None,
                shop=shop,
                source_category=", ".join(badges) or None,
                thumbnail=row.get("data-preview") or None,
                recommends=to_int(_text(row.select_one(".qc-count-good"))),
                comments=to_int(_text(row.select_one(".qc-count-comment"))),
                views=to_int(_text(row.select_one(".qc-count-hit"))),
                posted_at=parse_loose_time(_text(row.select_one(".v2-list-row__time"))),
                author=_text(row.select_one(".user-nick-label")) or None,
                sold_out="is-done" in classes,
            ))
        return deals


class Fmkorea(object):
    key = "fmkorea"
    name = "에펨코리아"
    base = "https://www.fmkorea.com"
    home = "https://www.fmkorea.com/hotdeal"

    def crawl(self):
        soup = _soup(fetch(self.home))
        deals = []
        for li in soup.select("li.li"):
            a = li.select_one("h3.title a")
            if a is None:
                continue
            m = re.search(r"/(\d{5,})", a.get("href", ""))
            if not m:
                continue
            title_el = a.select_one(".ellipsis-target") or a
            title = _text(title_el)
            info = {}
            for span in li.select(".hotdeal_info span"):
                t = _text(span)
                if ":" in t:
                    k, v = t.split(":", 1)
                    info[k.strip()] = v.strip()
            shop = info.get("쇼핑몰")
            price_txt = info.get("가격")
            thumb = li.select_one("img.thumb")
            cat = li.select_one(".category a")
            author = _text(li.select_one(".author")).lstrip("/ ").strip()
            full_title = "[%s] %s" % (shop, title) if shop else title
            deals.append(Deal(
                self.key, m.group(1), full_title, self.base + "/" + m.group(1),
                shop=shop,
                price=parse_price(price_txt),
                price_text=price_txt,
                shipping=info.get("배송"),
                source_category=_text(cat) or None,
                thumbnail=abs_url(self.base, thumb.get("data-original") or thumb.get("src")) if thumb else None,
                comments=to_int(_text(li.select_one(".comment_count"))),
                posted_at=parse_loose_time(_text(li.select_one(".regdate"))),
                author=author or None,
                # 종료된 딜은 'hotdeal_var8Y' 클래스가 붙는다
                sold_out=any(c.endswith("Y") and c.startswith("hotdeal_var") for c in (a.get("class") or [])),
            ))
        return deals


class Arcalive(object):
    key = "arcalive"
    name = "아카라이브"
    base = "https://arca.live"
    home = "https://arca.live/b/hotdeal"

    def crawl(self):
        soup = _soup(fetch(self.home))
        deals = []
        for row in soup.select("div.vrow.hybrid"):
            a = row.select_one("a.title.hybrid-title")
            if a is None:
                continue
            m = re.search(r"/b/hotdeal/(\d+)", a.get("href", ""))
            if not m:
                continue
            info = a.select_one(".info")
            if info is not None:
                info.extract()
            for icon in a.select(".media-icon"):
                icon.extract()
            title = _text(a)
            store = _text(row.select_one(".deal-store")) or None
            price_txt = _text(row.select_one(".deal-price"))
            t = row.select_one(".col-time time")
            thumb = row.select_one(".vrow-preview img")
            full_title = "[%s] %s" % (store, title) if store else title
            classes = " ".join(row.get("class") or [])
            deals.append(Deal(
                self.key, m.group(1), full_title, self.base + "/b/hotdeal/" + m.group(1),
                shop=store,
                price=parse_price(price_txt),
                price_text=price_txt or None,
                shipping=_text(row.select_one(".deal-delivery")) or None,
                source_category=_text(row.select_one(".badges a.badge")) or None,
                thumbnail=abs_url(self.base, thumb.get("src")) if thumb else None,
                views=to_int(_text(row.select_one(".col-view"))),
                recommends=to_int(_text(row.select_one(".col-rate"))),
                posted_at=parse_iso_utc(t.get("datetime", "")) if t is not None else None,
                author=_text(row.select_one(".col-author")) or None,
                sold_out="deal-close" in classes or row.select_one(".deal-close") is not None,
            ))
        return deals
