# -*- coding: utf-8 -*-
"""백그라운드 스케줄러: 별도 스레드에서 주기적으로 수집/가격확인을 수행."""
import logging
import threading
import time

from dealradar import collector, db, watcher

log = logging.getLogger(__name__)


class Scheduler(object):
    TICK = 20  # 초

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._wake = threading.Event()
        self.last_crawl = 0.0
        self.last_url_check = 0.0
        self._force_url = False
        self.last_report = {}
        self.running_job = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="dealradar-scheduler")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def trigger_crawl(self):
        """'지금 수집' — 다음 틱을 기다리지 않고 즉시 실행."""
        self.last_crawl = 0.0
        self._wake.set()

    def trigger_url_check(self):
        """모든 URL 감시를 주기와 상관없이 즉시 확인."""
        self._force_url = True
        self.last_url_check = 0.0
        self._wake.set()

    @property
    def busy(self):
        return self.running_job is not None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("scheduler tick failed")
            self._wake.wait(self.TICK)
            self._wake.clear()

    def _tick(self):
        settings = db.get_settings()
        crawl_iv = max(1, db.get_setting_int(settings, "crawl_interval_min", 5)) * 60
        url_iv_min = max(5, db.get_setting_int(settings, "url_check_interval_min", 60))
        now = time.time()
        with self._busy:
            if now - self.last_crawl >= crawl_iv:
                self.running_job = "핫딜 수집 중"
                try:
                    self.last_report = collector.run_crawl()
                finally:
                    self.last_crawl = time.time()
                    self.running_job = None
            # URL 감시는 항목별 last_checked 기준으로 주기가 지난 것만 확인한다
            if now - self.last_url_check >= 60:
                self.running_job = "가격 확인 중"
                force, self._force_url = self._force_url, False
                try:
                    watcher.run_url_checks(url_iv_min, force=force)
                finally:
                    self.last_url_check = time.time()
                    self.running_job = None


scheduler = Scheduler()
