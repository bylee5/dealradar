# -*- coding: utf-8 -*-
"""상품 페이지 URL에서 현재 가격을 추출한다.

추출 순서:
  1) 사용자가 지정한 CSS 선택자
  2) JSON-LD (schema.org Product/Offer)
  3) 메타 태그 (product:price:amount, og:price:amount, itemprop=price)
  4) 페이지 스크립트 안의 salePrice / lowPrice 등

다나와(prod.danawa.com) 상품 페이지는 JSON-LD 최저가로 잘 동작한다.
쿠팡/G마켓/스마트스토어 등은 봇 차단으로 실패하는 경우가 많다.
"""
import json
import re

from bs4 import BeautifulSoup

from dealradar.crawlers.base import fetch, parse_price


class PriceNotFound(Exception):
    pass


def _walk_jsonld(obj):
    """JSON-LD 트리에서 offers 가격 후보를 모두 찾는다."""
    if isinstance(obj, list):
        for o in obj:
            for p in _walk_jsonld(o):
                yield p
    elif isinstance(obj, dict):
        otype = str(obj.get("@type", "")).lower()
        if "offer" in otype or "priceCurrency" in obj:
            cur = str(obj.get("priceCurrency", "KRW")).upper()
            for key in ("lowPrice", "price"):
                if key not in obj or cur not in ("KRW", "", "WON"):
                    continue
                try:
                    yield int(float(str(obj.get(key)).replace(",", "")))
                except ValueError:
                    pass
        for v in obj.values():
            if isinstance(v, (dict, list)):
                for p in _walk_jsonld(v):
                    yield p


def _from_jsonld(soup):
    prices = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string or s.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        prices.extend(p for p in _walk_jsonld(data) if p > 0)
    return min(prices) if prices else None


_META_KEYS = [
    ("property", "product:price:amount"),
    ("property", "og:price:amount"),
    ("property", "product:sale_price:amount"),
    ("itemprop", "price"),
    ("name", "price"),
]


def _from_meta(soup):
    for attr, name in _META_KEYS:
        el = soup.find(attrs={attr: name})
        if el is None:
            continue
        val = el.get("content") or el.get_text()
        p = parse_price(str(val)) if val else None
        if p is None and val:
            try:
                p = int(float(str(val).replace(",", "")))
            except ValueError:
                p = None
        if p:
            return p
    return None


def _site_rules(url, soup, html):
    # 범용: 'lowPrice' / 'salePrice' / 'finalPrice' 숫자 (스크립트 내 JSON)
    for key in ("salePrice", "finalPrice", "discountedSalePrice", "lowPrice"):
        m = re.search(r'"%s"\s*:\s*"?(\d{3,9})' % key, html)
        if m:
            return int(m.group(1))
    return None


def extract_price(url, selector=None):
    """(가격, 페이지제목) 반환. 실패하면 PriceNotFound."""
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("meta", attrs={"property": "og:title"})
    title = (title_el.get("content") if title_el else None) or (
        soup.title.get_text().strip() if soup.title else "")

    if selector:
        el = soup.select_one(selector)
        if el is None:
            raise PriceNotFound("선택자 '%s' 에 해당하는 요소가 없습니다" % selector)
        p = parse_price(el.get_text() + ("" if "원" in el.get_text() else "원"))
        if p:
            return p, title
        raise PriceNotFound("선택자 요소에서 숫자를 찾지 못했습니다: %r" % el.get_text()[:50])

    for fn in (_from_jsonld, _from_meta):
        p = fn(soup)
        if p:
            return p, title
    p = _site_rules(url, soup, html)
    if p:
        return p, title
    raise PriceNotFound(
        "페이지에서 가격을 찾지 못했습니다. (봇 차단 페이지이거나 자바스크립트로 그려지는 페이지일 수 있습니다. "
        "'가격 CSS 선택자'를 지정해 보세요.)")
