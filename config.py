# -*- coding: utf-8 -*-
"""기본 설정. 대부분의 값은 웹 화면의 '설정' 페이지에서 바꿀 수 있고 DB에 저장된다."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.environ.get("DEALRADAR_DB", os.path.join(BASE_DIR, "data", "dealradar.db"))
HOST = os.environ.get("DEALRADAR_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEALRADAR_PORT", "5000"))
SECRET_KEY = os.environ.get("DEALRADAR_SECRET", "dealradar-local-secret")

# 요청 시 사용할 User-Agent / 타임아웃
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_TIMEOUT = 10

# 오래된 딜 정리 (일)
DEAL_RETENTION_DAYS = 90

# 설정 페이지 기본값 (DB settings 테이블이 비어 있을 때 사용)
DEFAULT_SETTINGS = {
    "crawl_interval_min": "5",          # 핫딜 수집 주기(분)
    "url_check_interval_min": "60",     # URL 가격 추적 주기(분)
    "surge_discount_pct": "40",         # 제목의 할인율이 이 값 이상이면 '급할인'
    "surge_history_drop_pct": "25",     # 과거 동일 상품 대비 이 이상 싸면 '급할인'
    "surge_recommend": "20",            # 추천수가 이 이상이면 '급할인'
    "surge_notify_categories": "육아,배드민턴",  # 이 카테고리의 급할인은 자동 알림
    "enabled_sources": "ppomppu,ruliweb,clien,quasarzone,fmkorea,arcalive",
    "telegram_token": "",
    "telegram_chat_id": "",
    "webhook_url": "",                  # Slack / Discord 웹훅 URL
}
