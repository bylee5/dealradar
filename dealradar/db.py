# -*- coding: utf-8 -*-
"""SQLite 스키마와 공통 DB 헬퍼."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    shop            TEXT,
    price           INTEGER,
    price_text      TEXT,
    shipping        TEXT,
    category        TEXT,
    source_category TEXT,
    thumbnail       TEXT,
    author          TEXT,
    comments        INTEGER DEFAULT 0,
    recommends      INTEGER DEFAULT 0,
    views           INTEGER DEFAULT 0,
    discount_pct    INTEGER,
    hot_score       REAL DEFAULT 0,
    is_surge        INTEGER DEFAULT 0,
    surge_reason    TEXT,
    ref_price       INTEGER,                -- 과거 비슷한 상품 대표가(중앙값)
    multi_source    INTEGER DEFAULT 1,      -- 비슷한 딜이 올라온 커뮤니티 수
    sold_out        INTEGER DEFAULT 0,
    posted_at       TEXT,
    crawled_at      TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_deals_posted ON deals(posted_at);
CREATE INDEX IF NOT EXISTS idx_deals_category ON deals(category);

CREATE TABLE IF NOT EXISTS watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,          -- 'keyword' | 'url'
    name            TEXT NOT NULL,
    keywords        TEXT,                   -- 공백/쉼표 구분, 모두 포함해야 매칭
    exclude         TEXT,                   -- 하나라도 포함되면 제외
    category        TEXT,
    max_price       INTEGER,                -- 키워드 감시: 이 가격 이하만
    url             TEXT,                   -- URL 감시 대상
    price_selector  TEXT,                   -- URL 감시: (선택) 가격 CSS 선택자
    target_price    INTEGER,                -- URL 감시: 목표가
    drop_pct        INTEGER,                -- URL 감시: 직전 대비 하락률 알림
    current_price   INTEGER,
    lowest_price    INTEGER,
    last_checked    TEXT,
    last_error      TEXT,
    active          INTEGER DEFAULT 1,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    checked_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_watch ON price_history(watch_id);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    INTEGER,
    deal_id     INTEGER,
    title       TEXT NOT NULL,
    message     TEXT,
    url         TEXT,
    price       INTEGER,
    is_read     INTEGER DEFAULT 0,
    created_at  TEXT,
    UNIQUE(watch_id, deal_id)
);

CREATE TABLE IF NOT EXISTS categories (
    name        TEXT PRIMARY KEY,
    keywords    TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE TABLE IF NOT EXISTS crawl_log (
    source      TEXT PRIMARY KEY,
    last_run    TEXT,
    last_count  INTEGER,
    last_error  TEXT
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect():
    d = os.path.dirname(config.DB_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connect():
    """쓰기 충돌을 줄이기 위해 프로세스 내에서는 락으로 직렬화한다."""
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    from dealradar.categories import DEFAULT_CATEGORIES

    with connect() as conn:
        conn.executescript(SCHEMA)
        for key, value in config.DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
        cnt = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if cnt == 0:
            for i, (name, kws) in enumerate(DEFAULT_CATEGORIES):
                conn.execute(
                    "INSERT INTO categories(name, keywords, sort_order) VALUES (?, ?, ?)",
                    (name, ", ".join(kws), i),
                )


# ---------------------------------------------------------------- settings
def get_settings():
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = dict(config.DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def get_setting_int(settings, key, default=0):
    try:
        return int(str(settings.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def save_settings(values):
    with connect() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )


# -------------------------------------------------------------- categories
def get_categories():
    """[(name, [keyword, ...]), ...] 정렬 순서대로."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, keywords FROM categories ORDER BY sort_order, name"
        ).fetchall()
    result = []
    for r in rows:
        kws = [k.strip() for k in r["keywords"].replace("\n", ",").split(",") if k.strip()]
        result.append((r["name"], kws))
    return result


def save_categories(items):
    """items: [(name, keywords_text), ...] — 전체 교체."""
    with connect() as conn:
        conn.execute("DELETE FROM categories")
        for i, (name, kws) in enumerate(items):
            conn.execute(
                "INSERT INTO categories(name, keywords, sort_order) VALUES (?, ?, ?)",
                (name, kws, i),
            )
