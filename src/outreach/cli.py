import argparse
import asyncio
import sys
import logging
from sqlalchemy import select, func, update

from src.db.session import AsyncSessionLocal
from src.db.models import OutreachAccount, B2BProspect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outreach_cli")

async def import_session(session_string: str, phone_number: str = None, proxy_url: str = None, name: str = "Екатерина", role: str = "Руководитель B2B развития LeadRadar", max_daily_limit: int = 15):
    """
    Imports a Pyrogram StringSession into OutreachAccount database table.
    """
    async with AsyncSessionLocal() as db:
        acc = OutreachAccount(
            session_string=session_string.strip(),
            phone_number=phone_number.strip() if phone_number else None,
            proxy_url=proxy_url.strip() if proxy_url else None,
            manager_name=name.strip() if name else "Екатерина",
            manager_role=role.strip() if role else "Руководитель B2B развития LeadRadar",
            max_daily_limit=max_daily_limit,
            status="ACTIVE"
        )
        db.add(acc)
        await db.commit()
        await db.refresh(acc)
        print(f"✅ Outreach Account #{acc.id} imported! Persona: {acc.manager_name} ({acc.manager_role}), Phone: {acc.phone_number or 'N/A'}, Proxy: {acc.proxy_url or 'None'}, Limit: {acc.max_daily_limit}/day")

async def approve_prospect(prospect_id: int):
    """
    Approves a B2BProspect from PENDING_APPROVAL to READY_FOR_OUTREACH.
    """
    async with AsyncSessionLocal() as db:
        prospect = (await db.execute(select(B2BProspect).where(B2BProspect.id == prospect_id))).scalar_one_or_none()
        if not prospect:
            print(f"❌ Prospect ID #{prospect_id} not found!")
            return
        prospect.status = "READY_FOR_OUTREACH"
        await db.commit()
        print(f"✅ Prospect ID #{prospect.id} (@{prospect.username}) approved for outreach!")

async def print_stats():
    """
    Outputs current outreach engine statistics.
    """
    async with AsyncSessionLocal() as db:
        total_accs = (await db.execute(select(func.count(OutreachAccount.id)))).scalar() or 0
        active_accs = (await db.execute(select(func.count(OutreachAccount.id)).where(OutreachAccount.status == "ACTIVE"))).scalar() or 0
        cooldown_accs = (await db.execute(select(func.count(OutreachAccount.id)).where(OutreachAccount.status == "COOL_DOWN"))).scalar() or 0
        banned_accs = (await db.execute(select(func.count(OutreachAccount.id)).where(OutreachAccount.status == "BANNED"))).scalar() or 0
        total_sent_today = (await db.execute(select(func.sum(OutreachAccount.daily_sent_count)))).scalar() or 0

        total_prospects = (await db.execute(select(func.count(B2BProspect.id)))).scalar() or 0
        ready_prospects = (await db.execute(select(func.count(B2BProspect.id)).where(B2BProspect.status == "READY_FOR_OUTREACH"))).scalar() or 0
        sent_prospects = (await db.execute(select(func.count(B2BProspect.id)).where(B2BProspect.status == "SENT"))).scalar() or 0
        failed_prospects = (await db.execute(select(func.count(B2BProspect.id)).where(B2BProspect.status == "FAILED"))).scalar() or 0

        print("\n" + "="*50)
        print("🚀 LEADRADAR OUTREACH ENGINE STATISTICS")
        print("="*50)
        print(f"📱 ACCOUNTS: Total: {total_accs} | Active: {active_accs} | CoolDown: {cooldown_accs} | Banned: {banned_accs}")
        print(f"✉️ MESSAGES TODAY: {total_sent_today} DMs dispatched across active farm")
        print(f"🎯 PROSPECTS: Total: {total_prospects} | Ready: {ready_prospects} | Sent: {sent_prospects} | Failed: {failed_prospects}")
        print("="*50 + "\n")

def main():
    parser = argparse.ArgumentParser(description="LeadRadar Outreach Engine CLI Utility")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Import Session Command
    import_parser = subparsers.add_parser("import_session", help="Import Pyrogram StringSession into DB")
    import_parser.add_argument("--session", required=True, help="Pyrogram StringSession text")
    import_parser.add_argument("--phone", required=False, help="Account phone number")
    import_parser.add_argument("--proxy", required=False, help="Proxy URL (http://user:pass@host:port)")
    import_parser.add_argument("--name", default="Екатерина", help="Manager Persona Name (e.g. Екатерина)")
    import_parser.add_argument("--role", default="Руководитель B2B развития LeadRadar", help="Manager Job Title")
    import_parser.add_argument("--limit", type=int, default=15, help="Max daily send limit (default 15)")

    # Approve Prospect Command
    approve_parser = subparsers.add_parser("approve_prospect", help="Approve prospect for outreach")
    approve_parser.add_argument("--id", type=int, required=True, help="Prospect ID")

    # Stats Command
    stats_parser = subparsers.add_parser("stats", help="Print outreach engine stats")

    args = parser.parse_args()

    if args.command == "import_session":
        asyncio.run(import_session(args.session, args.phone, args.proxy, args.name, args.role, args.limit))
    elif args.command == "approve_prospect":
        asyncio.run(approve_prospect(args.id))
    elif args.command == "stats":
        asyncio.run(print_stats())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
