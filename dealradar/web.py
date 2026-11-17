# -*- coding: utf-8 -*-
"""Flask 웹 화면."""
import math
from datetime import datetime

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   url_for)

import config
from dealradar import collector, db, notifier, watcher
from dealradar.categories import OTHER
from dealradar.crawlers import ALL_CRAWLERS, SOURCE_NAMES
from dealradar.scheduler import scheduler
from dealradar.scoring import title_tokens

PAGE_SIZE = 60

SORTS = {
    "new": "posted_at DESC",
    "hot": "hot_score DESC, posted_at DESC",
    "price": "CASE WHEN price IS NULL THEN 1 ELSE 0 END, price ASC",
}


def _int_or_none(v):
    try:
        v = str(v).replace(",", "").strip()
        return int(v) if v else None
    except (TypeError, ValueError):
        return None


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # ------------------------------------------------------------ filters
    @app.template_filter("won")
    def won(v):
        return "{:,}원".format(v) if v else ""

    @app.template_filter("ago")
    def ago(s):
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return s or ""
        sec = (datetime.now() - dt).total_seconds()
        if sec < 60:
            return "방금"
        if sec < 3600:
            return "%d분 전" % (sec // 60)
        if sec < 86400:
            return "%d시간 전" % (sec // 3600)
        if sec < 86400 * 7:
            return "%d일 전" % (sec // 86400)
        return dt.strftime("%m-%d")

    @app.template_filter("source_name")
    def source_name(k):
        return SOURCE_NAMES.get(k, k)

    @app.template_global("qs")
    def qs(**over):
        """현재 목록 필터를 유지한 채 일부만 바꾼 URL. page 는 명시할 때만 유지."""
        args = {k: v for k, v in request.args.items() if k != "page"}
        args.update(over)
        return url_for(request.endpoint, **{k: v for k, v in args.items() if v not in ("", None)})

    @app.context_processor
    def inject_common():
        with db.connect() as conn:
            unread = conn.execute("SELECT COUNT(*) FROM alerts WHERE is_read=0").fetchone()[0]
        return {"unread_count": unread, "sched": scheduler, "OTHER": OTHER}

    # -------------------------------------------------------------- deals
    @app.route("/")
    def index():
        cat = request.args.get("cat", "")
        q = request.args.get("q", "").strip()
        source = request.args.get("source", "")
        surge = request.args.get("surge") == "1"
        show_sold = request.args.get("sold") == "1"
        sort = request.args.get("sort", "new")
        if sort not in SORTS:
            sort = "new"
        page = max(1, _int_or_none(request.args.get("page")) or 1)

        where, params = [], []
        if cat:
            where.append("category = ?")
            params.append(cat)
        if source:
            where.append("source = ?")
            params.append(source)
        if surge:
            where.append("is_surge = 1")
        if not show_sold:
            where.append("sold_out = 0")
        for word in q.split():
            where.append("title LIKE ?")
            params.append("%" + word + "%")
        wsql = ("WHERE " + " AND ".join(where)) if where else ""

        with db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM deals " + wsql, params).fetchone()[0]
            deals = conn.execute(
                "SELECT * FROM deals %s ORDER BY %s LIMIT ? OFFSET ?" % (wsql, SORTS[sort]),
                params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
            cat_counts = dict(conn.execute(
                "SELECT category, COUNT(*) FROM deals WHERE sold_out=0 GROUP BY category").fetchall())
            surge_count = conn.execute(
                "SELECT COUNT(*) FROM deals WHERE is_surge=1 AND sold_out=0 "
                "AND posted_at >= datetime('now', 'localtime', '-1 day')").fetchone()[0]
        categories = [c[0] for c in db.get_categories()] + [OTHER]
        return render_template(
            "index.html", deals=deals, total=total, page=page,
            pages=max(1, int(math.ceil(total / float(PAGE_SIZE)))),
            categories=categories, cat_counts=cat_counts, surge_count=surge_count,
            sources=ALL_CRAWLERS,
            f={"cat": cat, "q": q, "source": source, "surge": surge, "sold": show_sold, "sort": sort})

    @app.route("/crawl", methods=["POST"])
    def crawl_now():
        scheduler.trigger_crawl()
        flash("수집을 시작했습니다. 잠시 후 새로고침 해 주세요.")
        return redirect(request.referrer or url_for("index"))

    # ------------------------------------------------------------ watches
    @app.route("/watches")
    def watches():
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT w.*, (SELECT COUNT(*) FROM alerts a WHERE a.watch_id = w.id) AS alert_count "
                "FROM watches w ORDER BY w.active DESC, w.id DESC").fetchall()
        prefill = request.args.get("prefill", "")
        prefill_kw = " ".join(sorted(title_tokens(prefill), key=lambda t: prefill.lower().find(t))[:4])
        return render_template(
            "watches.html", watches=rows, prefill=prefill, prefill_kw=prefill_kw,
            categories=[c[0] for c in db.get_categories()] + [OTHER],
            tab=request.args.get("tab", "keyword"))

    @app.route("/watches", methods=["POST"])
    def watch_add():
        kind = request.form.get("kind", "keyword")
        name = request.form.get("name", "").strip()
        now = db.now_str()
        if kind == "keyword":
            keywords = request.form.get("keywords", "").strip()
            if not keywords:
                flash("키워드를 입력해 주세요.", "error")
                return redirect(url_for("watches"))
            with db.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO watches(kind, name, keywords, exclude, category, max_price, "
                    "created_at) VALUES ('keyword', ?, ?, ?, ?, ?, ?)",
                    (name or keywords, keywords, request.form.get("exclude", "").strip(),
                     request.form.get("category") or None,
                     _int_or_none(request.form.get("max_price")), now))
                wid = cur.lastrowid
            n = watcher.match_existing(wid)
            flash("키워드 감시를 등록했습니다. 최근 7일 딜 중 %d건이 일치합니다." % n)
        else:
            url = request.form.get("url", "").strip()
            if not url.startswith("http"):
                flash("http(s):// 로 시작하는 상품 URL을 입력해 주세요.", "error")
                return redirect(url_for("watches", tab="url"))
            with db.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO watches(kind, name, url, price_selector, target_price, drop_pct, "
                    "created_at) VALUES ('url', ?, ?, ?, ?, ?, ?)",
                    (name, url, request.form.get("price_selector", "").strip() or None,
                     _int_or_none(request.form.get("target_price")),
                     _int_or_none(request.form.get("drop_pct")), now))
                wid = cur.lastrowid
            price, err = watcher.check_url_watch(wid)
            if err:
                flash("등록했지만 가격을 가져오지 못했습니다: %s" % err, "error")
            else:
                flash("URL 감시를 등록했습니다. 현재가 {:,}원".format(price))
            return redirect(url_for("watch_detail", wid=wid))
        return redirect(url_for("watch_detail", wid=wid))

    def _get_watch(wid):
        with db.connect() as conn:
            w = conn.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()
        if w is None:
            abort(404)
        return w

    @app.route("/watches/<int:wid>")
    def watch_detail(wid):
        w = _get_watch(wid)
        history, chart, deals = [], None, []
        if w["kind"] == "url":
            with db.connect() as conn:
                history = conn.execute(
                    "SELECT price, checked_at FROM price_history WHERE watch_id=? "
                    "ORDER BY id DESC LIMIT 500", (wid,)).fetchall()[::-1]
            chart = _price_chart(history, w["target_price"])
        else:
            deals = watcher.matched_deals(w)
        with db.connect() as conn:
            alerts = conn.execute(
                "SELECT * FROM alerts WHERE watch_id=? ORDER BY id DESC LIMIT 30", (wid,)).fetchall()
        return render_template("watch_detail.html", w=w, history=history[::-1][:30],
                               chart=chart, deals=deals, alerts=alerts,
                               categories=[c[0] for c in db.get_categories()] + [OTHER])

    @app.route("/watches/<int:wid>/edit", methods=["POST"])
    def watch_edit(wid):
        w = _get_watch(wid)
        f = request.form
        with db.connect() as conn:
            if w["kind"] == "keyword":
                conn.execute(
                    "UPDATE watches SET name=?, keywords=?, exclude=?, category=?, max_price=? WHERE id=?",
                    (f.get("name", "").strip() or f.get("keywords", ""), f.get("keywords", "").strip(),
                     f.get("exclude", "").strip(), f.get("category") or None,
                     _int_or_none(f.get("max_price")), wid))
            else:
                conn.execute(
                    "UPDATE watches SET name=?, url=?, price_selector=?, target_price=?, drop_pct=? WHERE id=?",
                    (f.get("name", "").strip(), f.get("url", "").strip(),
                     f.get("price_selector", "").strip() or None,
                     _int_or_none(f.get("target_price")), _int_or_none(f.get("drop_pct")), wid))
        flash("저장했습니다.")
        return redirect(url_for("watch_detail", wid=wid))

    @app.route("/watches/<int:wid>/toggle", methods=["POST"])
    def watch_toggle(wid):
        _get_watch(wid)
        with db.connect() as conn:
            conn.execute("UPDATE watches SET active = 1 - active WHERE id=?", (wid,))
        return redirect(request.referrer or url_for("watches"))

    @app.route("/watches/<int:wid>/delete", methods=["POST"])
    def watch_delete(wid):
        with db.connect() as conn:
            conn.execute("DELETE FROM watches WHERE id=?", (wid,))
            conn.execute("DELETE FROM price_history WHERE watch_id=?", (wid,))
            conn.execute("DELETE FROM alerts WHERE watch_id=?", (wid,))
        flash("삭제했습니다.")
        return redirect(url_for("watches"))

    @app.route("/watches/<int:wid>/check", methods=["POST"])
    def watch_check(wid):
        price, err = watcher.check_url_watch(wid)
        if err:
            flash("가격 확인 실패: %s" % err, "error")
        else:
            flash("현재가 {:,}원".format(price))
        return redirect(url_for("watch_detail", wid=wid))

    # ------------------------------------------------------------- alerts
    @app.route("/alerts")
    def alerts():
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT a.*, w.name AS watch_name, w.kind AS watch_kind FROM alerts a "
                "LEFT JOIN watches w ON w.id = a.watch_id ORDER BY a.id DESC LIMIT 300").fetchall()
        return render_template("alerts.html", alerts=rows)

    @app.route("/alerts/read", methods=["POST"])
    def alerts_read():
        with db.connect() as conn:
            conn.execute("UPDATE alerts SET is_read=1 WHERE is_read=0")
        return redirect(url_for("alerts"))

    @app.route("/alerts/clear", methods=["POST"])
    def alerts_clear():
        with db.connect() as conn:
            conn.execute("DELETE FROM alerts WHERE is_read=1")
        return redirect(url_for("alerts"))

    # ----------------------------------------------------------- settings
    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            values = {}
            for key in config.DEFAULT_SETTINGS:
                if key == "enabled_sources":
                    values[key] = ",".join(request.form.getlist("enabled_sources"))
                elif key in request.form:
                    values[key] = request.form.get(key, "").strip()
            db.save_settings(values)
            flash("설정을 저장했습니다.")
            return redirect(url_for("settings"))
        with db.connect() as conn:
            logs = {r["source"]: r for r in conn.execute("SELECT * FROM crawl_log").fetchall()}
        s = db.get_settings()
        enabled = set(x.strip() for x in s.get("enabled_sources", "").split(","))
        return render_template("settings.html", s=s, sources=ALL_CRAWLERS, enabled=enabled,
                               logs=logs, categories=db.get_categories())

    @app.route("/settings/categories", methods=["POST"])
    def settings_categories():
        names = request.form.getlist("cat_name")
        kws = request.form.getlist("cat_keywords")
        items = [(n.strip(), k.strip()) for n, k in zip(names, kws) if n.strip()]
        new_name = request.form.get("new_name", "").strip()
        if new_name:
            items.append((new_name, request.form.get("new_keywords", "").strip()))
        db.save_categories(items)
        n = collector.reclassify_all()
        flash("카테고리를 저장하고 딜 %d건을 다시 분류했습니다." % n)
        return redirect(url_for("settings") + "#categories")

    @app.route("/settings/test-notify", methods=["POST"])
    def settings_test_notify():
        res = notifier.push_external("✅ DealRadar 테스트 알림입니다.")
        flash(" / ".join(res) if res else "설정된 외부 알림 채널이 없습니다.",
              "error" if any("실패" in r for r in res) or not res else None)
        return redirect(url_for("settings"))

    # ---------------------------------------------------------------- api
    @app.route("/api/status")
    def api_status():
        with db.connect() as conn:
            unread = conn.execute("SELECT COUNT(*) FROM alerts WHERE is_read=0").fetchone()[0]
            latest = conn.execute("SELECT MAX(crawled_at) FROM deals").fetchone()[0]
        return jsonify(unread=unread, busy=scheduler.running_job, latest=latest)

    @app.route("/api/deals")
    def api_deals():
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM deals WHERE sold_out=0 ORDER BY posted_at DESC LIMIT 100").fetchall()
        return jsonify([dict(r) for r in rows])

    return app


def _price_chart(history, target=None, w=640, h=180, pad=28):
    """가격 이력 -> SVG polyline 좌표 (외부 라이브러리 없이)."""
    if len(history) < 1:
        return None
    prices = [r["price"] for r in history]
    lo, hi = min(prices), max(prices)
    if target:
        lo, hi = min(lo, target), max(hi, target)
    if hi == lo:
        hi, lo = hi * 1.05 + 1, lo * 0.95
    n = len(prices)

    def x(i):
        return pad + (w - 2 * pad) * (i / float(n - 1) if n > 1 else 0.5)

    def y(p):
        return pad + (h - 2 * pad) * (1 - (p - lo) / float(hi - lo))

    pts = " ".join("%.1f,%.1f" % (x(i), y(p)) for i, p in enumerate(prices))
    low_i = prices.index(min(prices))
    return {
        "w": w, "h": h, "points": pts,
        "lo": int(lo), "hi": int(hi),
        "target_y": y(target) if target else None,
        "low": {"x": x(low_i), "y": y(prices[low_i]), "p": prices[low_i]},
        "last": {"x": x(n - 1), "y": y(prices[-1]), "p": prices[-1]},
        "first_at": history[0]["checked_at"][5:16], "last_at": history[-1]["checked_at"][5:16],
    }
