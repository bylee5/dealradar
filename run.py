# -*- coding: utf-8 -*-
"""DealRadar 실행 진입점.

    python run.py              # 웹 서버 + 백그라운드 수집 시작
    python run.py --crawl      # 수집 1회만 실행하고 종료 (cron 등에서 사용)
    python run.py --no-scheduler   # 웹 화면만 (수집은 다른 프로세스에서)
"""
import argparse
import logging
import sys

import config
from dealradar import collector, db, watcher
from dealradar.scheduler import scheduler
from dealradar.web import create_app


def main():
    if sys.version_info < (3, 6):
        sys.exit("Python 3.6 이상이 필요합니다.")
    ap = argparse.ArgumentParser(description="DealRadar - 핫딜 모아보기 & 관심상품 감시")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--crawl", action="store_true", help="수집 1회 실행 후 종료")
    ap.add_argument("--no-scheduler", action="store_true", help="백그라운드 수집 끄기")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    db.init_db()

    if args.crawl:
        report = collector.run_crawl()
        for src, (n, err) in report.items():
            print("%-12s %s" % (src, "오류: %s" % err if err else "새 딜 %d건" % n))
        settings = db.get_settings()
        watcher.run_url_checks(db.get_setting_int(settings, "url_check_interval_min", 60))
        return

    if not args.no_scheduler:
        scheduler.start()
    app = create_app()
    print(" * DealRadar: http://%s:%d" % (args.host, args.port))
    # reloader 를 쓰면 스케줄러가 두 번 뜨므로 끈다
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
