# -*- coding: utf-8 -*-
"""핫딜 수집 파이프라인: 크롤링 → 분류 → 유사 상품 비교 → 점수 → 저장 → 알림."""
import logging
from datetime import datetime, timedelta

import config
from dealradar import db, notifier, watcher
from dealradar.categories import classify
from dealradar.crawlers import CRAWLERS, SOURCE_NAMES
from dealradar.scoring import evaluate, jaccard, title_tokens

log = logging.getLogger(__name__)

SIMILARITY = 0.6          # 같은 상품으로 볼 제목 유사도
HISTORY_GAP_DAYS = 3      # 이보다 오래된 딜만 '과거 가격'으로 사용
WINDOW_DAYS = 60          # 비교 대상 기간


def _dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now()


def _median(vals):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) // 2


class SimilarityIndex(object):
    """최근 딜 제목 토큰의 역색인. 새 딜과 비슷한 과거 딜을 빠르게 찾는다."""

    def __init__(self, conn):
        self.rows = []      # (id, source, price, posted_dt, tokens)
        self.inv = {}
        cutoff = (datetime.now() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        for r in conn.execute(
                "SELECT id, source, title, price, posted_at FROM deals WHERE posted_at >= ?",
                (cutoff,)):
            self.add(r["id"], r["source"], r["title"], r["price"], r["posted_at"])

    def add(self, deal_id, source, title, price, posted_at):
        toks = title_tokens(title)
        idx = len(self.rows)
        self.rows.append((deal_id, source, price, _dt(posted_at), toks))
        for t in toks:
            self.inv.setdefault(t, []).append(idx)

    def similar(self, title):
        toks = title_tokens(title)
        if len(toks) < 2:
            return []
        counts = {}
        for t in toks:
            for idx in self.inv.get(t, ()):
                counts[idx] = counts.get(idx, 0) + 1
        out = []
        for idx, shared in counts.items():
            if shared < 2:
                continue
            row = self.rows[idx]
            if jaccard(toks, row[4]) >= SIMILARITY:
                out.append(row)
        return out


def _rescore(conn, deal_id, settings):
    r = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    if r is None:
        return
    d = dict(r)
    disc, score, surge, reason = evaluate(d, settings, d.get("ref_price"), d.get("multi_source") or 1)
    conn.execute(
        "UPDATE deals SET discount_pct=?, hot_score=?, is_surge=?, surge_reason=? WHERE id=?",
        (disc, score, surge, reason, deal_id),
    )


def _store(conn, deals, settings, categories, index):
    """딜 목록을 저장하고 새로 추가된 딜 id 목록을 반환."""
    new_ids = []
    now = db.now_str()
    for d in deals:
        row = conn.execute(
            "SELECT id, price FROM deals WHERE source=? AND source_id=?",
            (d.source, d.source_id)).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE deals SET comments=?, recommends=?, views=?, sold_out=?, "
                "price=COALESCE(price, ?), title=? WHERE id=?",
                (d.comments, d.recommends, d.views, d.sold_out, d.price, d.title, row["id"]))
            _rescore(conn, row["id"], settings)
            continue

        posted = _dt(d.posted_at)
        similar = index.similar(d.title)
        past_prices = [s[2] for s in similar
                       if s[2] and s[3] < posted - timedelta(days=HISTORY_GAP_DAYS)]
        ref_price = _median(past_prices)
        recent = [s for s in similar if abs((s[3] - posted).total_seconds()) <= HISTORY_GAP_DAYS * 86400]
        other_sources = set(s[1] for s in recent if s[1] != d.source)
        multi = 1 + len(other_sources)

        data = d.as_dict()
        data["category"] = classify(d.title, categories, d.source_category)
        disc, score, surge, reason = evaluate(data, settings, ref_price, multi)
        cur = conn.execute(
            "INSERT INTO deals(source, source_id, title, url, shop, price, price_text, shipping, "
            "category, source_category, thumbnail, author, comments, recommends, views, "
            "discount_pct, hot_score, is_surge, surge_reason, ref_price, multi_source, sold_out, "
            "posted_at, crawled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.source, d.source_id, d.title, d.url, d.shop, d.price, d.price_text, d.shipping,
             data["category"], d.source_category, d.thumbnail, d.author, d.comments,
             d.recommends, d.views, disc, score, surge, reason, ref_price, multi, d.sold_out,
             d.posted_at, now))
        new_id = cur.lastrowid
        new_ids.append(new_id)
        index.add(new_id, d.source, d.title, d.price, d.posted_at)

        # 다른 커뮤니티에 이미 올라온 같은 딜들의 '동시 등장 수'도 갱신
        for s in recent:
            if s[1] != d.source:
                conn.execute(
                    "UPDATE deals SET multi_source = MAX(COALESCE(multi_source,1), ?) WHERE id=?",
                    (multi, s[0]))
                _rescore(conn, s[0], settings)
    return new_ids


def run_crawl(sources=None):
    """수집 1회 실행. {source: (count_or_None, error_or_None)} 반환."""
    settings = db.get_settings()
    categories = db.get_categories()
    enabled = [s.strip() for s in settings.get("enabled_sources", "").split(",") if s.strip()]
    targets = sources or enabled
    report = {}
    all_new = []
    quiet_new = []

    with db.connect() as conn:
        index = SimilarityIndex(conn)

    for key in targets:
        crawler = CRAWLERS.get(key)
        if crawler is None:
            continue
        try:
            deals = crawler.crawl()
            error = None
        except Exception as e:
            log.warning("crawl %s failed: %s", key, e)
            deals, error = [], "%s: %s" % (type(e).__name__, e)
        with db.connect() as conn:
            first_time = conn.execute(
                "SELECT 1 FROM crawl_log WHERE source=? AND last_count IS NOT NULL", (key,)
            ).fetchone() is None
            new_ids = _store(conn, deals, settings, categories, index) if deals else []
            conn.execute(
                "INSERT OR REPLACE INTO crawl_log(source, last_run, last_count, last_error) "
                "VALUES (?, ?, ?, ?)",
                (key, db.now_str(), len(deals) if not error else None, error))
        # 처음 수집한 사이트의 기존 글로 알림 폭탄이 오지 않도록 외부 푸시는 생략
        (quiet_new if first_time else all_new).extend(new_ids)
        report[key] = (len(new_ids) if not error else None, error)
        log.info("crawl %s: %d deals, %d new%s", SOURCE_NAMES.get(key, key), len(deals),
                 len(new_ids), " (error: %s)" % error if error else "")

    if all_new:
        watcher.match_deals(all_new, push=True, settings=settings)
        _notify_surges(all_new, settings)
    if quiet_new:
        watcher.match_deals(quiet_new, push=False, settings=settings)
    _cleanup()
    return report


def _notify_surges(deal_ids, settings):
    cats = [c.strip() for c in settings.get("surge_notify_categories", "").split(",") if c.strip()]
    if not cats:
        return
    marks = ",".join("?" * len(deal_ids))
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM deals WHERE id IN (%s) AND is_surge=1" % marks, deal_ids).fetchall()
    for r in rows:
        if r["category"] in cats:
            notifier.create_alert(
                0, "[급할인·%s] %s" % (r["category"], r["title"]),
                "%s · %s" % (SOURCE_NAMES.get(r["source"], r["source"]), r["surge_reason"] or ""),
                url=r["url"], price=r["price"], deal_id=r["id"], settings=settings)


def _cleanup():
    cutoff = (datetime.now() - timedelta(days=config.DEAL_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as conn:
        conn.execute("DELETE FROM deals WHERE posted_at < ?", (cutoff,))


def reclassify_all():
    """카테고리 키워드를 바꾼 뒤 기존 딜 전체를 다시 분류."""
    categories = db.get_categories()
    with db.connect() as conn:
        rows = conn.execute("SELECT id, title, source_category FROM deals").fetchall()
        for r in rows:
            conn.execute("UPDATE deals SET category=? WHERE id=?",
                         (classify(r["title"], categories, r["source_category"]), r["id"]))
    return len(rows)
