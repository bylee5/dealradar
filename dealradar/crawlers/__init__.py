# -*- coding: utf-8 -*-
from dealradar.crawlers.html_sites import Arcalive, Clien, Fmkorea, Quasarzone
from dealradar.crawlers.rss_sites import Ppomppu, Ruliweb

# 새 사이트를 추가하려면 crawl() -> [Deal, ...] 을 구현한 클래스를 만들어 여기에 등록한다.
ALL_CRAWLERS = [Ppomppu(), Ruliweb(), Clien(), Quasarzone(), Fmkorea(), Arcalive()]
CRAWLERS = {c.key: c for c in ALL_CRAWLERS}
SOURCE_NAMES = {c.key: c.name for c in ALL_CRAWLERS}
