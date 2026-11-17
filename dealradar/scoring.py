# -*- coding: utf-8 -*-
"""'급할인' 판정과 핫 점수 계산.

신호:
  1) 제목의 할인율 (예: '70% 할인')
  2) 강한 키워드 (역대가, 대란, 가격오류 ...)
  3) 과거 같은 상품 가격 대비 하락률 (우리 DB에 쌓인 이력 기준)
  4) 여러 커뮤니티에 동시에 올라옴
  5) 추천/댓글/조회수
"""
import re

from dealradar.crawlers.base import parse_discount_pct

STRONG_KEYWORDS = [
    "역대가", "역대최저", "역대 최저", "역대급", "대란", "가격오류", "오류가", "핵딜",
    "반값", "미친가격", "미친 가격", "떨이", "창고대방출", "최저가갱신", "최저가 갱신",
]
WEAK_KEYWORDS = ["최저가", "특가", "타임딜", "한정", "선착순", "쿠폰", "1+1"]

_bracket_re = re.compile(r"[\[【(][^\]】)]*[\]】)]")
_token_re = re.compile(r"[0-9a-zA-Z가-힣]+")
_STOP = set("""
무료 무배 무료배송 배송 배송비 유배 원 개 세트 할인 특가 최저가 핫딜 정품 국내 해외 직구
구매 추천 상품 쿠폰 적용 카드 행사 이벤트 무료반품 로켓 와우 단독 한정 신상 최신 the and
""".split())


def title_tokens(title):
    """비교용 토큰 집합: 쇼핑몰/가격 괄호 제거, 소문자, 불용어/짧은 숫자 제거."""
    t = _bracket_re.sub(" ", title or "").lower()
    toks = set()
    for tok in _token_re.findall(t):
        if tok in _STOP or len(tok) < 2:
            continue
        if tok.isdigit() and len(tok) < 3:
            continue
        toks.add(tok)
    return toks


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


def keyword_hits(title):
    t = title or ""
    strong = [k for k in STRONG_KEYWORDS if k in t]
    weak = [k for k in WEAK_KEYWORDS if k in t]
    return strong, weak


def evaluate(deal, settings, ref_price=None, multi_source=1):
    """deal(dict) -> (discount_pct, hot_score, is_surge, surge_reason)

    ref_price: 과거 비슷한 상품의 대표 가격(중앙값). 없으면 None.
    multi_source: 비슷한 딜이 올라온 커뮤니티 수(자기 자신 포함).
    """
    from dealradar.db import get_setting_int

    th_disc = get_setting_int(settings, "surge_discount_pct", 40)
    th_drop = get_setting_int(settings, "surge_history_drop_pct", 25)
    th_rec = get_setting_int(settings, "surge_recommend", 20)

    title = deal.get("title") or ""
    disc = parse_discount_pct(title)
    strong, weak = keyword_hits(title)
    price = deal.get("price")
    recs = deal.get("recommends") or 0
    comments = deal.get("comments") or 0
    views = deal.get("views") or 0

    hist_drop = None
    if ref_price and price and ref_price > price:
        hist_drop = int(round((ref_price - price) * 100.0 / ref_price))

    score = 0.0
    reasons = []
    if disc:
        score += disc
        if disc >= th_disc:
            reasons.append("%d%% 할인" % disc)
    if strong:
        score += 25
        reasons.append("'%s'" % strong[0])
    if weak:
        score += 5
    if hist_drop:
        score += hist_drop * 1.2
        if hist_drop >= th_drop:
            reasons.append("이전 대비 -%d%%" % hist_drop)
    if multi_source and multi_source > 1:
        score += 10 * (multi_source - 1)
        if multi_source >= 3:
            reasons.append("%d곳 동시 등장" % multi_source)
    score += min(recs, 100) * 1.5 + min(comments, 100) * 0.3 + min(views / 500.0, 20)
    if recs >= th_rec:
        reasons.append("추천 %d" % recs)

    if deal.get("sold_out"):
        score *= 0.3

    is_surge = 1 if reasons and not deal.get("sold_out") else 0
    return disc, round(score, 1), is_surge, ", ".join(reasons) or None
