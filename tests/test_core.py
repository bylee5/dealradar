# -*- coding: utf-8 -*-
"""네트워크 없이 도는 단위 테스트:  python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

config.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

from dealradar import collector, db, watcher  # noqa: E402
from dealradar.categories import DEFAULT_CATEGORIES, classify  # noqa: E402
from dealradar.crawlers.base import (  # noqa: E402
    Deal, parse_discount_pct, parse_loose_time, parse_price, parse_price_from_title, parse_shop)
from dealradar.scoring import evaluate, jaccard, title_tokens  # noqa: E402


class ParserTest(unittest.TestCase):
    def test_price_from_title(self):
        self.assertEqual(parse_price_from_title("[지마켓] 액츠 세제 3.1L (16,520원/무료)")[:1], (16520,))
        self.assertEqual(parse_price_from_title("[G마켓]코카콜라 190ml 60캔(23,490/무배)")[0], 23490)
        self.assertEqual(parse_price_from_title("[알리] 에어프라이어 (코인딜49,259/무료)")[0], 49259)
        self.assertEqual(parse_price_from_title("[네이버] 스위치 타이틀 46,000원/유배")[0], 46000)
        self.assertEqual(parse_price_from_title("[11번가] 노트북 (129.9만원/무료)")[0], 1299000)
        self.assertIsNone(parse_price_from_title("[스팀] 디아블로2 35% 할인 (11/13~19)")[0])

    def test_price(self):
        self.assertEqual(parse_price("￦1,299,000"), 1299000)
        self.assertIsNone(parse_price("$16.61"))
        self.assertEqual(parse_price("12,320원"), 12320)

    def test_shop_discount(self):
        self.assertEqual(parse_shop("[쿠팡] 하기스 기저귀"), "쿠팡")
        self.assertEqual(parse_shop("(에픽 모바일) 게임 무료"), "에픽 모바일")
        self.assertEqual(parse_discount_pct("최대 70% 할인 + 쿠폰 10%"), 70)
        self.assertIsNone(parse_discount_pct("100% 정품"))

    def test_loose_time(self):
        from datetime import datetime
        now = datetime(2026, 11, 13, 14, 0, 0)
        self.assertEqual(parse_loose_time("16분 전", now), "2026-11-13 13:44:00")
        self.assertEqual(parse_loose_time("13:57", now), "2026-11-13 13:57:00")
        self.assertEqual(parse_loose_time("23:10", now), "2026-11-12 23:10:00")
        self.assertEqual(parse_loose_time("2026-11-10 16:59:18", now), "2026-11-10 16:59:18")
        self.assertEqual(parse_loose_time("11-10", now), "2026-11-10 00:00:00")


class CategoryTest(unittest.TestCase):
    def test_classify(self):
        cats = DEFAULT_CATEGORIES
        self.assertEqual(classify("[쿠팡] 하기스 네이처메이드 기저귀 4팩", cats), "육아")
        self.assertEqual(classify("[네이버] 요넥스 아스트록스 77 프로", cats), "배드민턴")
        self.assertEqual(classify("[지마켓] 셔틀콕 AS-30 1타", cats), "배드민턴")
        self.assertEqual(classify("[11번가] 로지텍 기계식 키보드", cats), "디지털/가전")
        self.assertEqual(classify("알 수 없는 물건", cats), "기타")


class ScoringTest(unittest.TestCase):
    def test_tokens(self):
        a = title_tokens("[쿠팡] 하기스 네이처메이드 밴드형 3단계 (35,000원/무료)")
        b = title_tokens("[G마켓] 하기스 네이처메이드 밴드형 3단계 공용")
        self.assertNotIn("쿠팡", a)
        self.assertGreaterEqual(jaccard(a, b), 0.6)

    def test_evaluate(self):
        s = dict(config.DEFAULT_SETTINGS)
        d = {"title": "[쿠팡] 요넥스 라켓 역대가 60% 할인", "price": 50000}
        disc, score, surge, reason = evaluate(d, s)
        self.assertEqual(disc, 60)
        self.assertEqual(surge, 1)
        self.assertIn("60% 할인", reason)
        d2 = {"title": "[쿠팡] 일반 상품", "price": 10000}
        self.assertEqual(evaluate(d2, s)[2], 0)
        self.assertEqual(evaluate(d2, s, ref_price=20000)[2], 1)  # 이전 대비 -50%
        self.assertEqual(evaluate(dict(d, sold_out=1), s)[2], 0)


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_store_history_and_watch(self):
        settings = db.get_settings()
        cats = db.get_categories()
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO watches(kind, name, keywords, exclude, max_price, active) "
                "VALUES ('keyword', '기저귀', '하기스 네이처메이드, 팸퍼스', '1단계', 40000, 1)")
            idx = collector.SimilarityIndex(conn)
            old = Deal("ppomppu", "1", "[쿠팡] 하기스 네이처메이드 밴드형 3단계 (50,000원/무료)",
                       "http://x/1", posted_at="2026-10-01 10:00:00")
            collector._store(conn, [old], settings, cats, idx)
            new = Deal("clien", "2", "[G마켓] 하기스 네이처메이드 밴드형 3단계 (30,000원/무료)",
                       "http://x/2", posted_at="2026-11-10 10:00:00")
            ids = collector._store(conn, [new], settings, cats, idx)
            row = conn.execute("SELECT * FROM deals WHERE id=?", (ids[0],)).fetchone()
        self.assertEqual(row["category"], "육아")
        self.assertEqual(row["ref_price"], 50000)
        self.assertEqual(row["is_surge"], 1)          # 이전 대비 -40%
        n = watcher.match_deals(ids, push=False)
        self.assertEqual(n, 1)
        self.assertEqual(watcher.match_deals(ids, push=False), 0)  # 중복 알림 없음

    def test_matches(self):
        w = {"keywords": "아스트록스 77, 나노플레어 800", "exclude": "중고", "category": None, "max_price": 200000}
        base = {"sold_out": 0, "category": "배드민턴", "price": 150000}
        self.assertTrue(watcher.matches(w, dict(base, title="요넥스 아스트록스77 프로 4U")))
        self.assertTrue(watcher.matches(w, dict(base, title="나노플레어 800 게임")))
        self.assertFalse(watcher.matches(w, dict(base, title="아스트록스 77 중고")))
        self.assertFalse(watcher.matches(w, dict(base, title="아스트록스 88")))
        self.assertFalse(watcher.matches(w, dict(base, title="아스트록스 77", price=250000)))


if __name__ == "__main__":
    unittest.main()
