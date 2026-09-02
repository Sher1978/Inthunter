import sys
import os
import asyncio
import re

sys.path.insert(0, os.path.abspath("."))

from src.db.session import AsyncSessionLocal
from src.db.models import ChannelCandidate, DiscoveredChat, BlacklistedChat
from sqlalchemy import select, delete, func

SPAM_PATTERNS = [
    r'[\u4e00-\u9fff]',  # Chinese
    r'[\uac00-\ud7af]',  # Korean
    r'trader', r'cricket', r'crypto', r'pump', r'casino', r'baccarat', r'betting', r'gambling',
    r'担保', r'公群', r'开房', r'记录', r'사기', r'骗子', r'套路', r'柬埔寨',
    r'movie', r'movies', r'bollywood', r'free-content', r'free_content', r'18\+', r'adult', r'erotic', r'porn'
]

def is_spam(text: str) -> bool:
    if not text:
        return False
    return any(re.search(pat, text, re.IGNORECASE) for pat in SPAM_PATTERNS)

async def clean_candidates():
    async with AsyncSessionLocal() as session:
        # 1. Clean ChannelCandidate
        cands_res = await session.execute(select(ChannelCandidate).where(ChannelCandidate.status == "DISCOVERED"))
        cands = list(cands_res.scalars().all())
        print(f"Total ChannelCandidates before cleanup: {len(cands)}")

        cand_delete_ids = []
        for c in cands:
            if is_spam(c.title or "") or is_spam(c.username_or_link or ""):
                cand_delete_ids.append(c.id)

        print(f"ChannelCandidates identified as spam: {len(cand_delete_ids)}")
        if cand_delete_ids:
            for i in range(0, len(cand_delete_ids), 500):
                batch = cand_delete_ids[i:i+500]
                await session.execute(delete(ChannelCandidate).where(ChannelCandidate.id.in_(batch)))
            await session.commit()
            print(f"✅ DELETED {len(cand_delete_ids)} spam items from ChannelCandidate table!")

        # 2. Clean DiscoveredChat
        disc_res = await session.execute(select(DiscoveredChat).where(DiscoveredChat.audit_status == "PENDING"))
        discs = list(disc_res.scalars().all())
        print(f"Total DiscoveredChat pending before cleanup: {len(discs)}")

        disc_delete_ids = []
        for d in discs:
            if is_spam(d.title or "") or is_spam(d.chat_username or "") or is_spam(d.verdict_reason or ""):
                disc_delete_ids.append(d.id)

        print(f"DiscoveredChat identified as spam: {len(disc_delete_ids)}")
        if disc_delete_ids:
            for i in range(0, len(disc_delete_ids), 500):
                batch = disc_delete_ids[i:i+500]
                await session.execute(delete(DiscoveredChat).where(DiscoveredChat.id.in_(batch)))
            await session.commit()
            print(f"✅ DELETED {len(disc_delete_ids)} spam items from DiscoveredChat table!")

if __name__ == "__main__":
    asyncio.run(clean_candidates())
