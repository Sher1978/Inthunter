"""Unit test for scoring pipeline, VQS filtering and persistence logic."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from src.ingestion.vendor_quality import evaluate_vendor_quality
from src.core.taxonomy import normalize_niche


class TestScoringPipeline(unittest.TestCase):
    def test_vqs_garbage_filtering(self):
        # Casino / gambling must be dropped
        score, intent, reason = evaluate_vendor_quality("Казино онлайн ставки 1xbet бонус 100%")
        self.assertEqual(intent, "TRASH")

        # Crypto exchange message MUST NOT be dropped as trash
        score, intent, reason = evaluate_vendor_quality("Обмен валют и крипты в Гоа, наличные рупии за USDT")
        self.assertNotEqual(intent, "TRASH")
        self.assertEqual(intent, "VENDOR_OFFER")

    def test_vqs_emoji_vendor_offers(self):
        # Structured ad with 12 emojis should be accepted as VENDOR_OFFER
        msg = (
            "🏡 Сдам виллу в Арамболе\n"
            "📍 Северный Гоа\n"
            "🛏 2 спальни\n"
            "🏊 Бассейн\n"
            "🛵 Байк в аренду\n"
            "💰 70000 INR\n"
            "⚡ Быстрый интернет\n"
            "🌴 Тихое место\n"
            "✅ Доступно с завтра\n"
            "📞 Писать в ЛС"
        )
        score, intent, reason = evaluate_vendor_quality(msg)
        self.assertNotEqual(intent, "TRASH")
        self.assertEqual(intent, "VENDOR_OFFER")

    def test_niche_normalization_covers_b2b_variants(self):
        self.assertEqual(normalize_niche("real_estate"), "real_estate")
        self.assertEqual(normalize_niche("REAL_ESTATE"), "real_estate")
        self.assertEqual(normalize_niche("currency_exchange"), "currency_exchange")
        self.assertEqual(normalize_niche("crypto"), "currency_exchange")
        self.assertEqual(normalize_niche("bike_rent"), "bike_rent")
        self.assertEqual(normalize_niche("auto_rental"), "bike_rent")
        self.assertEqual(normalize_niche("services_visa"), "services_visa")
        self.assertEqual(normalize_niche("other_b2b"), "other_b2b")


if __name__ == "__main__":
    unittest.main()
