# -*- coding: utf-8 -*-
"""알림: 앱 내 알림(alerts 테이블) + 선택적으로 텔레그램 / Slack / Discord 웹훅."""
import logging
import sqlite3

from dealradar import db
from dealradar.crawlers.base import post_json

log = logging.getLogger(__name__)


def _fmt_price(p):
    return "{:,}원".format(p) if p else "가격 미확인"


def push_external(text, settings=None):
    """설정된 외부 채널로 전송. 결과 메시지 목록을 반환."""
    settings = settings or db.get_settings()
    results = []
    token = (settings.get("telegram_token") or "").strip()
    chat_id = (settings.get("telegram_chat_id") or "").strip()
    if token and chat_id:
        try:
            post_json("https://api.telegram.org/bot%s/sendMessage" % token, {
                "chat_id": chat_id, "text": text, "disable_web_page_preview": False,
            })
            results.append("텔레그램 전송 성공")
        except Exception as e:
            log.warning("telegram failed: %s", e)
            results.append("텔레그램 실패: %s" % e)
    hook = (settings.get("webhook_url") or "").strip()
    if hook:
        payload = {"content": text} if "discord" in hook else {"text": text}
        try:
            post_json(hook, payload)
            results.append("웹훅 전송 성공")
        except Exception as e:
            log.warning("webhook failed: %s", e)
            results.append("웹훅 실패: %s" % e)
    return results


def create_alert(watch_id, title, message, url=None, price=None, deal_id=None,
                 push=True, settings=None):
    """알림 생성. 같은 (watch, deal) 조합은 한 번만. 새로 생겼으면 True."""
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO alerts(watch_id, deal_id, title, message, url, price, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (watch_id, deal_id, title, message, url, price, db.now_str()),
            )
    except sqlite3.IntegrityError:
        return False
    if push:
        text = "🔔 %s\n%s\n%s\n%s" % (title, message or "", _fmt_price(price), url or "")
        push_external(text, settings)
    return True
