import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.module_manager import module_manager
from src.db.models import OutreachProject, OutreachTask, B2BProspect
from src.outreach.outreach_worker import OutreachWorker


class TestGeoMatrixAndIsolation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reset default states for test consistency
        module_manager.set_status("reader", True)
        module_manager.set_status("ai_scorer", True)
        module_manager.set_status("outreach", True)
        module_manager.set_status("global_killswitch", False)

        module_manager.set_geo_all("goa", True)
        module_manager.set_geo_all("dubai", True)
        module_manager.set_geo_all("vietnam", False)
        module_manager.set_geo_all("thailand", False)

    def test_geo_canonicalization(self):
        self.assertEqual(module_manager.canonicalize_geo("goa"), "goa")
        self.assertEqual(module_manager.canonicalize_geo("north_goa"), "goa")
        self.assertEqual(module_manager.canonicalize_geo("nhatrang"), "vietnam")
        self.assertEqual(module_manager.canonicalize_geo("danang"), "vietnam")
        self.assertEqual(module_manager.canonicalize_geo("phuket"), "thailand")
        self.assertEqual(module_manager.canonicalize_geo("samui"), "thailand")
        self.assertEqual(module_manager.canonicalize_geo("ae"), "dubai")
        self.assertEqual(module_manager.canonicalize_geo(None), "global")

    def test_geo_gatekeeper_switches(self):
        # Goa is ON
        self.assertTrue(module_manager.is_geo_enabled("goa", "reader"))
        self.assertTrue(module_manager.is_geo_enabled("goa", "ai_scorer"))
        self.assertTrue(module_manager.is_geo_enabled("goa", "outreach"))

        # Vietnam is OFF
        self.assertFalse(module_manager.is_geo_enabled("vietnam", "reader"))
        self.assertFalse(module_manager.is_geo_enabled("nhatrang", "reader"))
        self.assertFalse(module_manager.is_geo_enabled("danang", "outreach"))

        # Enable only reader for Vietnam
        module_manager.set_geo_status("vietnam", "reader", True)
        self.assertTrue(module_manager.is_geo_enabled("nhatrang", "reader"))
        self.assertFalse(module_manager.is_geo_enabled("nhatrang", "outreach"))

        # Global master switch overrides GEO
        module_manager.set_status("reader", False)
        self.assertFalse(module_manager.is_geo_enabled("goa", "reader"))
        self.assertFalse(module_manager.is_geo_enabled("nhatrang", "reader"))

    def test_geo_set_all(self):
        module_manager.set_geo_all("thailand", True)
        self.assertTrue(module_manager.is_geo_enabled("phuket", "reader"))
        self.assertTrue(module_manager.is_geo_enabled("phuket", "ai_scorer"))
        self.assertTrue(module_manager.is_geo_enabled("phuket", "outreach"))

        module_manager.set_geo_all("thailand", False)
        self.assertFalse(module_manager.is_geo_enabled("phuket", "reader"))
        self.assertFalse(module_manager.is_geo_enabled("phuket", "ai_scorer"))
        self.assertFalse(module_manager.is_geo_enabled("phuket", "outreach"))


if __name__ == "__main__":
    unittest.main()
