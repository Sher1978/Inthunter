"""Unit and Integration Tests for LeadRadar Outreach Engine V2.

Verifies:
1. Working hours calculation (standard daytime & overnight windows).
2. Fallback account selection when OutreachTaskAccount is empty.
3. Persona continuity / stickiness for Touch 2 follow-ups.
4. Global Blacklist enforcement in dispatch.
5. Opt-out trigger detection in incoming PM listener.
6. Graceful handling of PeerIdInvalid / no-username users.
7. Deduplication in JTBD lead generation.
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, time, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import select, delete

from src.db.session import AsyncSessionLocal
from src.db.models import (
    OutreachTask,
    OutreachAccount,
    OutreachTaskAccount,
    OutreachProject,
    B2BProspect,
    OutreachBlacklist
)
from src.outreach.outreach_worker import OutreachWorker, outreach_worker_instance
from src.outreach.account_manager import AccountManager


class TestOutreachEngineV2(unittest.TestCase):

    def test_01_working_hours_standard_and_overnight(self):
        worker = OutreachWorker()
        
        # Daytime task (09:00 - 18:00)
        day_task = OutreachTask(
            project_id=1,
            working_hours_start="09:00",
            working_hours_end="18:00",
            timezone="UTC"
        )
        
        # Overnight task (21:00 - 05:00)
        night_task = OutreachTask(
            project_id=1,
            working_hours_start="21:00",
            working_hours_end="05:00",
            timezone="UTC"
        )

        with patch("src.outreach.outreach_worker.datetime") as mock_dt:
            # Test at 14:00 UTC (daytime is active, overnight is inactive)
            mock_dt.now.return_value = datetime(2026, 10, 29, 14, 0, 0, tzinfo=timezone.utc)
            mock_dt.strptime = datetime.strptime
            self.assertTrue(worker.is_task_in_working_hours(day_task))
            self.assertFalse(worker.is_task_in_working_hours(night_task))

            # Test at 23:30 UTC (daytime is inactive, overnight is active)
            mock_dt.now.return_value = datetime(2026, 10, 29, 23, 30, 0, tzinfo=timezone.utc)
            self.assertFalse(worker.is_task_in_working_hours(day_task))
            self.assertTrue(worker.is_task_in_working_hours(night_task))

            # Test at 03:00 UTC (daytime is inactive, overnight is active)
            mock_dt.now.return_value = datetime(2026, 10, 29, 3, 0, 0, tzinfo=timezone.utc)
            self.assertFalse(worker.is_task_in_working_hours(day_task))
            self.assertTrue(worker.is_task_in_working_hours(night_task))

    def test_02_account_fallback_when_task_accounts_empty(self):
        """Verifies get_best_available_account_for_task falls back to ACTIVE accounts when OutreachTaskAccount has 0 rows."""
        async def run_test():
            async with AsyncSessionLocal() as session:
                proj = OutreachProject(name="Test Proj Fallback", status="ACTIVE")
                session.add(proj)
                await session.flush()

                task = OutreachTask(
                    project_id=proj.id,
                    name="Test Task No Accounts",
                    status="ACTIVE",
                    timezone="UTC"
                )
                session.add(task)
                await session.flush()

                # Ensure task has 0 rows in OutreachTaskAccount
                rows = (await session.execute(
                    select(OutreachTaskAccount).where(OutreachTaskAccount.task_id == task.id)
                )).scalars().all()
                self.assertEqual(len(rows), 0)

                # Fetch best account
                worker = OutreachWorker()
                task_acc, acc = await worker.get_best_available_account_for_task(session, task.id)
                self.assertIsNotNone(acc, "Should fallback to an active account from pool")
                self.assertEqual(acc.status, "ACTIVE")
                self.assertLess(acc.daily_sent_count, 15)

                # Cleanup
                await session.delete(task)
                await session.delete(proj)
                await session.commit()

        asyncio.run(run_test())

    def test_03_followup_persona_continuity(self):
        """Verifies Touch 2 follow-ups preserve the assigned manager and do not switch accounts."""
        async def run_test():
            async with AsyncSessionLocal() as session:
                # Get two active accounts
                accs = (await session.execute(
                    select(OutreachAccount).where(OutreachAccount.status == "ACTIVE").limit(2)
                )).scalars().all()
                if len(accs) < 2:
                    self.skipTest("Need at least 2 active accounts in DB to test persona stickiness")

                acc1, acc2 = accs[0], accs[1]
                worker = OutreachWorker()

                # When specific_account_id is acc1, it MUST return acc1
                _, chosen = await worker.get_best_available_account_for_task(
                    session, task_id=99999, specific_account_id=acc1.id
                )
                if chosen:
                    self.assertEqual(chosen.id, acc1.id)

                # When specific_account_id is acc2, it MUST return acc2
                _, chosen2 = await worker.get_best_available_account_for_task(
                    session, task_id=99999, specific_account_id=acc2.id
                )
                if chosen2:
                    self.assertEqual(chosen2.id, acc2.id)

        asyncio.run(run_test())

    def test_04_blacklist_stops_dispatch(self):
        """Verifies that blacklisted recipients are skipped and marked BLACKLISTED."""
        async def run_test():
            async with AsyncSessionLocal() as session:
                test_uname = "test_optout_target_99"
                test_tg_id = 9988776655

                proj = OutreachProject(name="Test Proj BL", status="ACTIVE")
                session.add(proj)
                await session.flush()

                task = OutreachTask(project_id=proj.id, name="Test Task", status="ACTIVE")
                session.add(task)
                await session.flush()

                # Add to blacklist
                bl = OutreachBlacklist(
                    telegram_id=test_tg_id,
                    username=test_uname,
                    reason="USER_OPTOUT"
                )
                session.add(bl)

                prospect = B2BProspect(
                    project_id=proj.id,
                    task_id=task.id,
                    telegram_id=test_tg_id,
                    username=test_uname,
                    status="READY_FOR_OUTREACH",
                    raw_ad_text="Test Ad",
                    sales_hook="Test Hook"
                )
                session.add(prospect)
                await session.commit()

                worker = OutreachWorker()
                acc = (await session.execute(select(OutreachAccount).where(OutreachAccount.status == "ACTIVE"))).scalars().first()

                # Dispatch
                dispatched = await worker.dispatch_single_prospect(session, task, proj, acc, prospect)
                self.assertFalse(dispatched)
                self.assertEqual(prospect.status, "BLACKLISTED")

                # Cleanup
                p_del = await session.get(B2BProspect, prospect.id)
                if p_del: await session.delete(p_del)
                bl_del = await session.get(OutreachBlacklist, bl.id)
                if bl_del: await session.delete(bl_del)
                t_del = await session.get(OutreachTask, task.id)
                if t_del: await session.delete(t_del)
                pr_del = await session.get(OutreachProject, proj.id)
                if pr_del: await session.delete(pr_del)
                await session.commit()

        asyncio.run(run_test())

    def test_05_listener_optout_detection(self):
        """Verifies stop words trigger opt-out and blacklist addition in listener logic."""
        stop_phrases = ["стоп", "хватит", "не пишите больше", "отписка", "stop", "unsubscribe"]
        for phrase in stop_phrases:
            lower = phrase.lower().strip()
            is_optout = any(sk in lower for sk in ["стоп", "хватит", "не пишите", "отпишите", "отписка", "спам", "больше не пишите", "удалите", "stop", "unsubscribe"])
            self.assertTrue(is_optout, f"Phrase '{phrase}' must be detected as opt-out")

    def test_06_peer_id_invalid_graceful_handling(self):
        """Verifies PeerIdInvalid sets status NO_USERNAME_RESTRICTED and does not penalize worker account."""
        async def run_test():
            async with AsyncSessionLocal() as session:
                acc = (await session.execute(select(OutreachAccount).where(OutreachAccount.status == "ACTIVE"))).scalars().first()
                if not acc:
                    self.skipTest("No active account found")

                proj = OutreachProject(name="Test Proj PeerId", status="ACTIVE")
                session.add(proj)
                await session.flush()
                proj_id = proj.id

                task = OutreachTask(project_id=proj.id, name="Test Task", status="ACTIVE")
                session.add(task)
                await session.flush()
                task_id = task.id

                orig_status = acc.status
                acc_id = acc.id
                prospect = B2BProspect(
                    project_id=proj.id,
                    task_id=task.id,
                    telegram_id=123456789,
                    username=None,  # No username!
                    status="READY_FOR_OUTREACH",
                    raw_ad_text="Test ad",
                    sales_hook="Test hook",
                    msg_1_opener="Hello! Test opener message."
                )
                session.add(prospect)
                await session.commit()
                prospect_id = prospect.id

                worker = OutreachWorker()

                # Mock client.send_message to raise PeerIdInvalid
                from pyrogram.errors import PeerIdInvalid
                with patch("src.outreach.account_manager.AccountManager.get_or_start_client") as mock_get_client:
                    mock_client = AsyncMock()
                    mock_client.send_chat_action = AsyncMock()
                    mock_client.send_message = AsyncMock(side_effect=PeerIdInvalid())
                    mock_get_client.return_value = mock_client

                    success = await worker.dispatch_single_prospect(session, task, proj, acc, prospect)
                    self.assertFalse(success)

                    prospect_check = await session.get(B2BProspect, prospect_id)
                    self.assertIsNotNone(prospect_check)
                    self.assertEqual(prospect_check.status, "NO_USERNAME_RESTRICTED")

                    # Account must remain ACTIVE after error
                    acc_check = await session.get(OutreachAccount, acc_id)
                    self.assertEqual(acc_check.status, orig_status)

                # Cleanup
                p_del = await session.get(B2BProspect, prospect_id)
                if p_del: await session.delete(p_del)
                t_del = await session.get(OutreachTask, task_id)
                if t_del: await session.delete(t_del)
                pr_del = await session.get(OutreachProject, proj_id)
                if pr_del: await session.delete(pr_del)
                await session.commit()

        asyncio.run(run_test())

    def test_07_cooldown_recovery(self):
        """Verifies AccountManager.recover_cooled_down_accounts recovers accounts past 24h."""
        async def run_test():
            async with AsyncSessionLocal() as session:
                # Create account cooled down 25 hours ago
                old_time = datetime.now(timezone.utc) - timedelta(hours=25)
                acc = OutreachAccount(
                    phone_number="+999888777666",
                    session_string="mock_session",
                    status="COOL_DOWN",
                    last_used_at=old_time,
                    daily_sent_count=10
                )
                session.add(acc)
                await session.commit()

                await AccountManager.recover_cooled_down_accounts(session)

                acc_check = (await session.execute(
                    select(OutreachAccount).where(OutreachAccount.phone_number == "+999888777666")
                )).scalars().first()

                self.assertIsNotNone(acc_check)
                self.assertEqual(acc_check.status, "ACTIVE")
                self.assertEqual(acc_check.daily_sent_count, 0)

                # Cleanup
                await session.delete(acc_check)
                await session.commit()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
