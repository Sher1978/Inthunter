"""Comprehensive End-to-End Test for LeadRadar Outreach Pipeline."""

import sys
import os
import asyncio
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.core.taxonomy import detect_geo, normalize_niche, is_valid_b2b_niche
from src.ingestion.vendor_quality import evaluate_vendor_quality
from src.outreach.jtbd_architect import generate_leads_communication
from src.db.models import OutreachProject, OutreachTask, B2BProspect, OutreachLead


class TestLeadRadarOutreachPipeline(unittest.TestCase):
    def test_01_taxonomy_and_word_boundaries(self):
        # North Goa locations
        self.assertEqual(detect_geo("Аренда байка в Арамболе"), "goa")
        self.assertEqual(detect_geo("Вила в Сиолиме срочно"), "goa")
        self.assertEqual(detect_geo("Апартаменты в Мандреме"), "goa")
        self.assertEqual(detect_geo("Обмен рублей на рупии в Морджиме"), "goa")
        
        # Word boundaries avoid false positives
        self.assertEqual(detect_geo("Retail supervisor position", fallback="global"), "global")
        self.assertEqual(detect_geo("Поездки за рубеж", fallback="global"), "global")

        # Niche normalization
        self.assertEqual(normalize_niche("REAL_ESTATE"), "real_estate")
        self.assertEqual(normalize_niche("AUTO_RENTAL"), "bike_rent")
        self.assertEqual(normalize_niche("FINANCE_CRYPTO"), "currency_exchange")
        self.assertEqual(normalize_niche("crypto"), "currency_exchange")
        self.assertEqual(normalize_niche("visa_run"), "services_visa")

    def test_02_vqs_filtering_rules(self):
        # Gambling / scam dropped
        _, intent_scam, _ = evaluate_vendor_quality("Казино онлайн ставки 1xbet заработок в сети")
        self.assertEqual(intent_scam, "TRASH")

        # Crypto exchange NOT dropped
        _, intent_crypto, _ = evaluate_vendor_quality("Обмен валют и крипты в Гоа, меняем usdt на рупии")
        self.assertNotEqual(intent_crypto, "TRASH")

        # Emojis on commercial villa ad NOT dropped
        ad_text = "🏡 Сдам виллу в Арамболе 📍 Морджим 🏊 Бассейн 💰 80000 INR 🌴 Пишите в лс"
        _, intent_ad, _ = evaluate_vendor_quality(ad_text)
        self.assertNotEqual(intent_ad, "TRASH")

    def test_03_database_project_task_binding(self):
        async def run_db_check():
            async with AsyncSessionLocal() as session:
                # Check that Goa channels exist
                from src.db.models import MonitoredChannel
                goa_channels = (await session.execute(
                    select(MonitoredChannel).where(MonitoredChannel.location_code == "goa")
                )).scalars().all()
                self.assertGreater(len(goa_channels), 0, "Goa channels must exist in DB")

                # Check that active tasks match project_id
                tasks = (await session.execute(select(OutreachTask))).scalars().all()
                for t in tasks:
                    self.assertIsNotNone(t.project_id, "OutreachTask must be bound to a project_id")

                # Check that all prospects in DB have valid non-empty status
                prospects = (await session.execute(select(B2BProspect))).scalars().all()
                for p in prospects:
                    self.assertIn(p.approval_status, ["DRAFT", "APPROVED", "REJECTED"])

        asyncio.run(run_db_check())

    def test_04_api_stats_and_communication_table(self):
        async def run_api_check():
            from src.api.outreach_routes import get_available_leads_stats, get_communication_table
            async with AsyncSessionLocal() as db:
                stats = await get_available_leads_stats(
                    project_id=13,
                    location_code="all",
                    niche_code="all",
                    flow_filter="ALL",
                    db=db,
                    current_user=None
                )
                self.assertIn("b2b_count", stats)
                self.assertIn("b2c_count", stats)
                self.assertIn("total", stats)

                table = await get_communication_table(
                    project_id=13,
                    flow="ALL",
                    status=None,
                    approval=None,
                    location=None,
                    niche=None,
                    db=db,
                    current_user=None
                )
                self.assertIsInstance(table, list)

        asyncio.run(run_api_check())



if __name__ == "__main__":
    unittest.main()
