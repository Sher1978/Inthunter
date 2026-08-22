import asyncio
import sys
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import Partner, UserProfile
from src.config import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

async def main():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Partner))
        partners = list(res.scalars().all())
        
        superadmin_count = 0
        demoted_count = 0

        SUPERADMIN_IDS = [8866001783, 260669598]
        for p in partners:
            u_stmt = select(UserProfile).where(UserProfile.user_id == p.telegram_id)
            u_prof = (await session.execute(u_stmt)).scalar_one_or_none()
            username = u_prof.username.lower() if u_prof and u_prof.username else ""

            if username == settings.SUPERADMIN_USERNAME.lower() or p.telegram_id in SUPERADMIN_IDS:
                p.role = "SUPERADMIN"
                p.moderation_status = "APPROVED"
                superadmin_count += 1
                print(f"[OK] Retained SUPERADMIN for ID {p.telegram_id} (@{username or 'N/A'})")
            elif p.role == "SUPERADMIN":
                p.role = "DEMO"
                demoted_count += 1
                print(f"[DEMOTED] Demoted ID {p.telegram_id} (username: @{username or 'N/A'}) from SUPERADMIN to DEMO")
        
        await session.commit()
        print(f"\nCleanup finished. Superadmins: {superadmin_count}, Demoted: {demoted_count}")

if __name__ == "__main__":
    asyncio.run(main())
