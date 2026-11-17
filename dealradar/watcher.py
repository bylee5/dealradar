# -*- coding: utf-8 -*-
"""관심 상품 감시.

- 키워드 감시: 새 핫딜 제목이 조건(키워드/제외어/카테고리/최대가격)에 맞으면 알림
    · 공백으로 구분한 단어는 모두 포함(AND)
    · 쉼표로 구분하면 그중 한 묶음만 맞아도 됨(OR)
      예) "하기스 네이처메이드, 하기스 맥스드라이"
- URL 감시: 상품 페이지 가격을 주기적으로 확인해 목표가 도달/급락 시 알림
"""
import logging
from datetime import datetime, timedelta

from dealradar import db, notifier
from dealradar.price_tracker import extract_price

log = logging.getLogger(__name__)


# ------------------------------------------------------------ keyword watch
def parse_groups(text):
    """'a b, c' -> [['a','b'], ['c']]"""
    groups = []
    for part in (text or "").split(","):
        words = [w.strip().lower() for w in part.split() if w.strip()]
        if words:
            groups.append(words)
    return groups


def parse_words(text):
    return [w.strip().lower() for w in (text or "").replace(",", " ").split() if w.strip()]


def _contains(title_l, title_ns, word):
    return word in title_l or word.replace(" ", "") in title_ns


def matches(watch, deal, include_sold_out=False):
    """watch, deal: dict/Row. 조건에 맞으면 True."""
    if deal["sold_out"] and not include_sold_out:
        return False
    title_l = (deal["title"] or "").lower()
    title_ns = title_l.replace(" ", "")
    groups = parse_groups(watch["keywords"])
    if not groups:
        return False
    if not any(all(_contains(title_l, title_ns, w) for w in g) for g in groups):
        return False
    if any(_contains(title_l, title_ns, w) for w in parse_words(watch["exclude"])):
        return False
    if watch["category"] and watch["category"] != deal["category"]:
        return False
    if watch["max_price"] and deal["price"] and deal["price"] > watch["max_price"]:
        return False
    return True


def _alert_for(watch, deal, push, settings):
    from dealradar.crawlers import SOURCE_NAMES
    msg = "'%s' 조건 일치 · %s" % (watch["name"], SOURCE_NAMES.get(deal["source"], deal["source"]))
    if deal["is_surge"] and deal["surge_reason"]:
        msg += " · 급할인(%s)" % deal["surge_reason"]
    return notifier.create_alert(
        watch["id"], deal["title"], msg, url=deal["url"], price=deal["price"],
        deal_id=deal["id"], push=push, settings=settings)


def match_deals(deal_ids, push=True, settings=None):
    """새로 들어온 딜들을 모든 활성 키워드 감시와 대조."""
    if not deal_ids:
        return 0
    with db.connect() as conn:
        watches = conn.execute(
            "SELECT * FROM watches WHERE kind='keyword' AND active=1").fetchall()
        if not watches:
            return 0
        marks = ",".join("?" * len(deal_ids))
        deals = conn.execute("SELECT * FROM deals WHERE id IN (%s)" % marks, deal_ids).fetchall()
    n = 0
    for w in watches:
        for d in deals:
            if matches(w, d) and _alert_for(w, d, push, settings):
                n += 1
    return n


def match_existing(watch_id, days=7):
    """감시를 새로 등록했을 때 최근 딜 중 맞는 것을 앱 내 알림으로만 표시."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as conn:
        w = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
        deals = conn.execute("SELECT * FROM deals WHERE posted_at >= ?", (cutoff,)).fetchall()
    if w is None:
        return 0
    return sum(1 for d in deals if matches(w, d) and _alert_for(w, d, False, None))


def matched_deals(watch, limit=50):
    """키워드 감시에 맞는 저장된 딜 목록(최신순, 종료된 딜 포함)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM deals ORDER BY posted_at DESC LIMIT 5000").fetchall()
    out = []
    for d in rows:
        if matches(watch, d, include_sold_out=True):
            out.append(d)
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------- URL watch
def check_url_watch(watch_id, settings=None):
    """URL 감시 1건 확인. (가격 또는 None, 오류메시지 또는 None)"""
    with db.connect() as conn:
        w = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    if w is None or w["kind"] != "url":
        return None, "감시 항목이 없습니다"
    now = db.now_str()
    try:
        price, page_title = extract_price(w["url"], (w["price_selector"] or "").strip() or None)
    except Exception as e:
        err = "%s" % e
        with db.connect() as conn:
            conn.execute("UPDATE watches SET last_checked=?, last_error=? WHERE id=?",
                         (now, err[:300], watch_id))
        return None, err

    prev = w["current_price"]
    lowest = w["lowest_price"]
    with db.connect() as conn:
        conn.execute("INSERT INTO price_history(watch_id, price, checked_at) VALUES (?, ?, ?)",
                     (watch_id, price, now))
        conn.execute(
            "UPDATE watches SET current_price=?, lowest_price=?, last_checked=?, last_error=NULL, "
            "name=CASE WHEN name='' OR name IS NULL THEN ? ELSE name END WHERE id=?",
            (price, min(price, lowest) if lowest else price, now, page_title[:100], watch_id))

    reasons = []
    target = w["target_price"]
    if target and price <= target and (prev is None or prev > target):
        reasons.append("목표가 %s원 도달" % "{:,}".format(target))
    if w["drop_pct"] and prev and price <= prev * (1 - w["drop_pct"] / 100.0):
        reasons.append("직전 대비 -%d%%" % round((prev - price) * 100.0 / prev))
    if lowest and price < lowest * 0.97 and not reasons:
        reasons.append("추적 최저가 갱신 (이전 %s원)" % "{:,}".format(lowest))
    if reasons:
        notifier.create_alert(
            watch_id, "[가격알림] %s" % w["name"], " · ".join(reasons),
            url=w["url"], price=price, settings=settings)
    return price, None


def run_url_checks(interval_min, force=False):
    cutoff = (datetime.now() - timedelta(minutes=interval_min)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as conn:
        if force:
            rows = conn.execute("SELECT id FROM watches WHERE kind='url' AND active=1").fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM watches WHERE kind='url' AND active=1 "
                "AND (last_checked IS NULL OR last_checked < ?)", (cutoff,)).fetchall()
    settings = db.get_settings()
    for r in rows:
        price, err = check_url_watch(r["id"], settings)
        log.info("url watch %s: %s %s", r["id"], price, err or "")
    return len(rows)
