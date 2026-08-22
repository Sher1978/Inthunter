import logging
from datetime import datetime, timezone
from sqlalchemy import select
from pyrogram import Client, filters
from pyrogram.types import Message

from src.db.session import AsyncSessionLocal
from src.db.models import OutreachAccount, B2BProspect
from src.outreach.dialogue_engine import generate_dialogue_reply

logger = logging.getLogger("intent_hunter.outreach.listener")

def register_incoming_message_handler(app: Client, account: OutreachAccount):
    """
    Registers an async incoming private message handler on a Pyrogram Client instance.
    """
    m_name = account.manager_name or "Екатерина"
    m_role = account.manager_role or "Руководитель B2B развития LeadRadar"

    @app.on_message(filters.private & ~filters.me)
    async def handle_prospect_reply(client: Client, message: Message):
        if not message.text:
            return

        sender_id = message.from_user.id if message.from_user else None
        sender_uname = message.from_user.username if message.from_user else None
        user_txt = message.text.strip()

        logger.info(f"📩 Incoming reply received on Manager '{m_name}' (Acc #{account.id}) from @{sender_uname} ({sender_id}): «{user_txt[:60]}»")

        async with AsyncSessionLocal() as db:
            # Find matching prospect
            stmt = select(B2BProspect).where(
                (B2BProspect.telegram_id == sender_id) |
                (B2BProspect.username == sender_uname)
            ) if sender_uname else select(B2BProspect).where(B2BProspect.telegram_id == sender_id)
            
            prospect = (await db.execute(stmt)).scalars().first()
            if not prospect:
                logger.info(f"No prospect record found for incoming sender {sender_id} (@{sender_uname}). Initializing ad-hoc prospect.")
                prospect = B2BProspect(
                    telegram_id=sender_id,
                    username=sender_uname,
                    niche="OTHER_B2B",
                    raw_ad_text="Direct Telegram Reply",
                    sales_hook="Интерес к сервису LeadRadar",
                    status="READY_FOR_OUTREACH",
                    assigned_account_id=account.id
                )
                db.add(prospect)
                await db.flush()

            # Record user message in dialogue history
            history = list(prospect.dialogue_history or [])
            history.append({
                "role": "user",
                "text": user_txt,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # Generate Gemini AI response from manager persona
            reply_txt = await generate_dialogue_reply(
                user_message=user_txt,
                dialogue_history=history,
                niche=prospect.niche,
                raw_ad_text=prospect.raw_ad_text,
                sales_hook=prospect.sales_hook,
                manager_name=m_name,
                manager_role=m_role
            )

            # Record manager response in dialogue history
            history.append({
                "role": "manager",
                "text": reply_txt,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            prospect.dialogue_history = history
            await db.commit()

            # Send Pyrogram reply
            try:
                await client.send_message(chat_id=message.chat.id, text=reply_txt)
                logger.info(f"💬 Replied as Manager '{m_name}' to @{sender_uname}: «{reply_txt[:60]}»")
            except Exception as e:
                logger.error(f"Failed to send Pyrogram reply to @{sender_uname}: {e}")
