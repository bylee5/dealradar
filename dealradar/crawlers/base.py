# -*- coding: utf-8 -*-
"""크롤러 공통: HTTP, 가격/쇼핑몰/할인율 파싱, 날짜 처리."""
import logging
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request

import config

log = logging.getLogger(__name__)

# 일부 사이트(Cloudflare)는 requests/urllib3 의 TLS 지문을 차단하므로 표준 urllib 을 사용한다.
_meta_charset = re.compile(br'<meta[^>]+charset=["\']?([\w-]+)', re.I)


def _decode(raw, content_type, encoding=None):
    if encoding:
        return raw.decode(encoding, "replace")
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    enc = m.group(1) if m else None
    if not enc:
        m = _meta_charset.search(raw[:4096])
        enc = m.group(1).decode("ascii") if m else None
    for e in [enc, "utf-8", "cp949"]:
        if not e:
            continue
        try:
            return raw.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def http_request(url, data=None, headers=None, timeout=None):
    """(status, content_type, bytes) 반환. HTTP 오류는 urllib.error.HTTPError 로 올라온다."""
    h = {"User-Agent": config.USER_AGENT}
    if headers:
        h.update(headers)
    for _ in range(5):
        req = urllib.request.Request(url, data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return resp.status, resp.headers.get("Content-Type", ""), raw
        except urllib.error.HTTPError as e:
            # 구버전 urllib 은 307/308 을 따라가지 않는다
            loc = e.headers.get("Location") if e.code in (307, 308) else None
            if not loc:
                raise
            url = urllib.parse.urljoin(url, loc)
    raise urllib.error.URLError("too many redirects: %s" % url)


def fetch(url, encoding=None):
    """URL 본문 텍스트를 반환."""
    _, ctype, raw = http_request(url)
    return _decode(raw, ctype, encoding)


def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    return http_request(url, data=body, headers={"Content-Type": "application/json"})


class Deal(object):
    """크롤러가 반환하는 딜 1건."""

    __slots__ = (
        "source", "source_id", "title", "url", "shop", "price", "price_text",
        "shipping", "source_category", "thumbnail", "author", "comments",
        "recommends", "views", "posted_at", "sold_out",
    )

    def __init__(self, source, source_id, title, url, **kw):
        self.source = source
        self.source_id = str(source_id)
        self.title = clean_text(title)
        self.url = url
        self.shop = kw.get("shop")
        self.price = kw.get("price")
        self.price_text = kw.get("price_text")
        self.shipping = kw.get("shipping")
        self.source_category = kw.get("source_category")
        self.thumbnail = kw.get("thumbnail")
        self.author = kw.get("author")
        self.comments = kw.get("comments") or 0
        self.recommends = kw.get("recommends") or 0
        self.views = kw.get("views") or 0
        self.posted_at = kw.get("posted_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.sold_out = 1 if kw.get("sold_out") else 0

        # 제목에서 보충 가능한 정보 채우기
        if not self.shop:
            self.shop = parse_shop(self.title)
        if self.price is None:
            p, ptxt, ship = parse_price_from_title(self.title)
            self.price = p
            self.price_text = self.price_text or ptxt
            self.shipping = self.shipping or ship

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "<Deal %s:%s %s %s>" % (self.source, self.source_id, self.price, self.title[:30])


# ------------------------------------------------------------------ parsers
_ws = re.compile(r"\s+")


def clean_text(s):
    return _ws.sub(" ", (s or "").replace("\xa0", " ")).strip()


def to_int(s, default=0):
    if s is None:
        return default
    s = str(s).strip().lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|천|만)?", s)
    if not m:
        return default
    val = float(m.group(1))
    unit = m.group(2)
    if unit in ("k", "천"):
        val *= 1000
    elif unit == "만":
        val *= 10000
    return int(val)


_shop_re = re.compile(r"^\s*[\[【(]\s*([^\]】)]{1,20}?)\s*[\]】)]")


def parse_shop(title):
    m = _shop_re.match(title or "")
    return m.group(1).strip() if m else None


_won_re = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*원")
_man_re = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*원")
_paren_re = re.compile(r"\(([^()]*)\)")
_num_re = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})")


def parse_price(text):
    """'16,520원', '12.9만원', '₩1,299,000', '49,259' 등에서 원화 가격을 뽑는다.
    해외 통화($, €, ¥ 등)는 None."""
    if not text:
        return None
    t = text.replace(" ", "")
    if re.search(r"[$€¥£]|usd|달러|엔|위안", t, re.I) and "원" not in t and "₩" not in t:
        return None
    m = _man_re.search(t)
    if m:
        return int(float(m.group(1)) * 10000)
    m = _won_re.search(t)
    if m:
        return int(m.group(1).replace(",", ""))
    m = _num_re.search(t)
    if m:
        v = int(m.group(1).replace(",", ""))
        return v if v >= 100 else None
    return None


_free_ship_re = re.compile(r"무료|무배|free|와우|로켓", re.I)


def parse_price_from_title(title):
    """제목 '(16,520원/무료)' 형태에서 (가격, 가격문자열, 배송) 추출."""
    if not title:
        return None, None, None
    # 1) 괄호 안 '가격/배송' 패턴 우선 (뒤쪽 괄호부터)
    for inner in reversed(_paren_re.findall(title)):
        parts = [p.strip() for p in inner.split("/")]
        price = parse_price(parts[0])
        if price is not None and (len(parts) > 1 or "원" in parts[0] or "," in parts[0]):
            ship = parts[1] if len(parts) > 1 else None
            return price, parts[0], ship
    # 2) 제목 아무 곳의 'xx,xxx원'
    m = _man_re.search(title.replace(" ", ""))
    if m:
        return int(float(m.group(1)) * 10000), m.group(0), None
    matches = _won_re.findall(title)
    if matches:
        val = matches[-1]
        return int(val.replace(",", "")), val + "원", None
    return None, None, None


_pct_re = re.compile(r"(\d{1,2})\s*%")


def parse_discount_pct(title):
    """'35% 할인', '최대 70%' 등에서 가장 큰 할인율."""
    vals = [int(v) for v in _pct_re.findall(title or "")]
    vals = [v for v in vals if 5 <= v <= 95]
    return max(vals) if vals else None


def is_free_shipping(text):
    return bool(text and _free_ship_re.search(text))


# -------------------------------------------------------------------- dates
def fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_rfc822(s):
    """RSS pubDate -> 로컬 시각 문자열."""
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return fmt_dt(dt)
    except Exception:
        return None


def parse_iso_utc(s):
    """'2026-11-13T04:52:57.000Z' -> 로컬 시각 문자열."""
    try:
        s = s.strip().replace("Z", "")
        s = s.split(".")[0]
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        # UTC -> local
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        return fmt_dt(dt)
    except Exception:
        return None


def parse_loose_time(s, now=None):
    """'13:57', '11-10', '2026.11.13', '16분 전', '3시간 전', '어제' 등."""
    now = now or datetime.now()
    s = clean_text(s)
    if not s:
        return None
    m = re.search(r"(\d+)\s*분\s*전", s)
    if m:
        return fmt_dt(now - timedelta(minutes=int(m.group(1))))
    m = re.search(r"(\d+)\s*시간\s*전", s)
    if m:
        return fmt_dt(now - timedelta(hours=int(m.group(1))))
    m = re.search(r"(\d+)\s*일\s*전", s)
    if m:
        return fmt_dt(now - timedelta(days=int(m.group(1))))
    if "방금" in s:
        return fmt_dt(now)
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mi = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        return fmt_dt(datetime(y, mo, d, hh, mi, ss))
    m = re.search(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                         second=int(m.group(3) or 0), microsecond=0)
        if dt > now + timedelta(minutes=5):
            dt -= timedelta(days=1)
        return fmt_dt(dt)
    m = re.search(r"^(\d{2})[.\-/](\d{2})[.\-/](\d{2})$", s)  # 26.11.13
    if m:
        return fmt_dt(datetime(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))))
    m = re.search(r"^(\d{1,2})[.\-/](\d{1,2})$", s)  # 11-10
    if m:
        dt = datetime(now.year, int(m.group(1)), int(m.group(2)))
        if dt > now:
            dt = dt.replace(year=now.year - 1)
        return fmt_dt(dt)
    return None


def abs_url(base, href):
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return base.rstrip("/") + href
