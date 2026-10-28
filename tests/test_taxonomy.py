"""Unit tests for LeadRadar taxonomy and geo/niche routing."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from src.core.taxonomy import (
    LOCATIONS, NICHES, detect_geo, normalize_niche, is_valid_b2b_niche
)


class TestTaxonomy(unittest.TestCase):
    def test_goa_geo_detection(self):
        self.assertEqual(detect_geo("Аренда байка в Арамболе на месяц"), "goa")
        self.assertEqual(detect_geo("Вила в Сиолиме, срочно!"), "goa")
        self.assertEqual(detect_geo("t.me/goa_chat_north"), "goa")
        self.assertEqual(detect_geo("Обмен рубли на рупии в Морджиме"), "goa")
        self.assertEqual(detect_geo("Looking for apartment in Mandrem or Kerim"), "goa")

    def test_avoid_false_positive_tai(self):
        # 'tai' in retail/detail/mountain should NOT trigger phuket
        self.assertEqual(detect_geo("Retail store manager vacancy", fallback="global"), "global")
        self.assertEqual(detect_geo("Full project details in direct", fallback="global"), "global")
        self.assertEqual(detect_geo("Mountain tour guide", fallback="global"), "global")
        # Actual phuket/thailand keywords should trigger phuket
        self.assertEqual(detect_geo("Пляж Бангтао аренда кондо"), "phuket")
        self.assertEqual(detect_geo("Аренда байка Пхукет Раваи"), "phuket")

    def test_avoid_false_positive_rub(self):
        # 'руб' inside 'зарубеж' or 'порубить' should not trigger moscow
        self.assertEqual(detect_geo("Поездки за рубеж и отдых", fallback="global"), "global")
        self.assertEqual(detect_geo("Москва Сити офис аренда"), "moscow")

    def test_niche_normalization(self):
        self.assertEqual(normalize_niche("REAL_ESTATE"), "real_estate")
        self.assertEqual(normalize_niche("auto_rental"), "bike_rent")
        self.assertEqual(normalize_niche("FINANCE_CRYPTO"), "currency_exchange")
        self.assertEqual(normalize_niche("crypto"), "currency_exchange")
        self.assertEqual(normalize_niche("VISA_RUN"), "services_visa")
        self.assertEqual(normalize_niche("unknown"), "general")
        self.assertEqual(normalize_niche(None), "general")

    def test_valid_b2b_niche(self):
        self.assertTrue(is_valid_b2b_niche("real_estate"))
        self.assertTrue(is_valid_b2b_niche("bike_rent"))
        self.assertTrue(is_valid_b2b_niche("currency_exchange"))
        self.assertFalse(is_valid_b2b_niche("general"))
        self.assertFalse(is_valid_b2b_niche("unknown"))
        self.assertFalse(is_valid_b2b_niche(None))


if __name__ == "__main__":
    unittest.main()

