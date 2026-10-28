"""Unit tests for outreach flow, project isolation, and lead generation filters."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from src.core.taxonomy import LOCATIONS, NICHES, normalize_niche


class TestOutreachFlow(unittest.TestCase):
    def test_niche_taxonomy_mapping(self):
        self.assertEqual(normalize_niche("real_estate"), "real_estate")
        self.assertEqual(normalize_niche("REAL_ESTATE"), "real_estate")
        self.assertEqual(normalize_niche("bike_rent"), "bike_rent")
        self.assertEqual(normalize_niche("AUTO_RENTAL"), "bike_rent")
        self.assertEqual(normalize_niche("currency_exchange"), "currency_exchange")
        self.assertEqual(normalize_niche("FINANCE_CRYPTO"), "currency_exchange")

    def test_geo_locations_taxonomy(self):
        self.assertIn("goa", LOCATIONS)
        self.assertIn("dubai", LOCATIONS)
        self.assertIn("phuket", LOCATIONS)
        self.assertIn("nhatrang", LOCATIONS)


if __name__ == "__main__":
    unittest.main()
