"""
vqs_auditor.py — VQS Self-Learning AI Audit Loop

Every 2 hours, takes a random sample of VQS-dropped messages, sends to AI,
finds false positives (messages incorrectly rejected by VQS), and sends
Telegram notification to superadmins with approve/reject correction buttons.

Approved corrections are added to the VQS whitelist via add_to_vqs_whitelist().
"""
import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("intent_hunter.vqs_auditor")

AUDIT_INTERVAL_SECONDS = 7200      # Run every 2 hours
SAMPLE_SIZE = 30                    # Messages to sample per audit pass
BATCH_SIZE = 10                     # Messages per AI call (to stay within token limits)
MIN_DROPS_TO_AUDIT = 5              # Min dropped messages needed to run audit


async def run_vqs_audit_loop():
    """Main background loop: runs VQS audit every AUDIT_INTERVAL_SECONDS."""
    logger.info("🧪 VQS Auditor loop started — will audit every 2 hours.")
    # Wait 10 min after startup before first audit (let ingestor warm up)
    await asyncio.sleep(600)

    while True:
        try:
            await run_single_vqs_audit_pass()
        except asyncio.CancelledError:
            logger.info("VQS Auditor loop cancelled.")
            break
        except Exception as e:
            logger.error(f"VQS Audit loop error: {e}", exc_info=True)

        await asyncio.sleep(AUDIT_INTERVAL_SECONDS)


async def run_single_vqs_audit_pass():
    """
    One audit pass:
    1. Sample TRASH-dropped messages from AIEvaluationLog (niche_code='dropped')
    2. Send to AI in batches for binary classification: real lead or spam?
    3. For confirmed false positives → notify superadmins with correction buttons
    """
    from src.db.session import AsyncSessionLocal
    from src.db.models import AIEvaluationLog
    from sqlalchemy import select, func

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        # Sample recent VQS-dropped messages
        stmt = (
            select(AIEvaluationLog)
            .where(
                AIEvaluationLog.niche_code == "dropped",
                AIEvaluationLog.created_at >= cutoff
            )
            .order_by(func.random())
            .limit(SAMPLE_SIZE)
        )
        res = await session.execute(stmt)
        dropped_logs = list(res.scalars().all())

    if len(dropped_logs) < MIN_DROPS_TO_AUDIT:
        logger.info(f"🧪 VQS Audit: only {len(dropped_logs)} drops in last 24h, skipping (min={MIN_DROPS_TO_AUDIT})")
        return

    logger.info(f"🧪 VQS Audit: reviewing {len(dropped_logs)} VQS-dropped messages...")

    false_positives = []

    # Process in batches to reduce AI API calls
    for i in range(0, len(dropped_logs), BATCH_SIZE):
        batch = dropped_logs[i:i + BATCH_SIZE]
        batch_fps = await _audit_batch(batch)
        false_positives.extend(batch_fps)
        await asyncio.sleep(3)  # Small delay between batches

    if not false_positives:
        logger.info("🧪 VQS Audit complete: no false positives found in this pass.")
        return

    logger.info(f"🧪 VQS Audit: found {len(false_positives)} potential false positives! Notifying superadmins...")
    await _notify_superadmins_false_positives(false_positives)


async def _audit_batch(logs: list) -> list:
    """
    Sends a batch of VQS-dropped messages to AI for review.
    Returns list of dicts: {log, trigger_phrase, ai_reasoning}
    for messages AI classifies as potential buyer leads.
    """
    try:
        from src.ai.scorer import _eval_with_groq
        call_api_fn = _make_groq_text_caller()
    except ImportError:
        logger.warning("VQS Auditor: AI scorer not available, skipping batch")
        return []

    false_positives = []

    for log in logs:
        try:
            result = await _classify_single_dropped(log, call_api_fn)
            if result:
                false_positives.append(result)
        except Exception as e:
            logger.debug(f"VQS audit single message error: {e}")

    return false_positives


def _make_groq_text_caller():
    """Returns a simple async function that sends a text prompt to Groq and returns the string response."""
    async def call_groq_text(prompt: str) -> str:
        try:
            from groq import AsyncGroq
            from src.config import settings
            raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
            key_pool = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("gsk_")]
            if not key_pool:
                return ""
            import random
            api_key = random.choice(key_pool)
            client = AsyncGroq(api_key=api_key)
            resp = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.1,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.debug(f"VQS auditor Groq call error: {e}")
            return ""
    return call_groq_text


async def _classify_single_dropped(log, call_api_fn) -> Optional[dict]:
    """
    Ask AI: 'Is this message actually a buyer-intent lead that VQS incorrectly rejected?'
    Returns dict if false positive, None if correctly rejected.
    """
    text = (log.message_text or "").strip()
    if not text or len(text) < 15:
        return None

    vqs_reason = log.reasoning or "VQS drop reason unknown"

    prompt = f"""You are an expert lead qualification reviewer for a real estate, car rental, and currency exchange platform in Southeast Asia.

A message was REJECTED by the automated VQS filter with reason: "{vqs_reason}"

Message text (in Russian, English, or mixed):
\"\"\"
{text[:600]}
\"\"\"

Your task: Determine if this rejection was a FALSE POSITIVE — i.e., the message is actually from a REAL BUYER looking to rent, buy, or get a service.

Respond with EXACTLY this format (no other text):
VERDICT: YES or NO
CONFIDENCE: 0-100
KEY_PHRASE: (the specific phrase that indicates buyer intent, max 5 words, or NONE)
REASON: (one sentence in Russian explaining your verdict)

YES = this IS a buyer-intent message and should have reached the AI scorer
NO = this is correctly rejected (spam, vendor ad, off-topic, bot, etc.)"""

    try:
        response = await call_api_fn(prompt)
        if not response:
            return None

        # Parse structured response
        lines = response.strip().split('\n')
        verdict = None
        confidence = 0
        key_phrase = None
        reason = ""

        for line in lines:
            line = line.strip()
            if line.startswith("VERDICT:"):
                verdict = line.split(":", 1)[1].strip().upper()
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = int(line.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    confidence = 50
            elif line.startswith("KEY_PHRASE:"):
                kp = line.split(":", 1)[1].strip()
                key_phrase = kp if kp.upper() != "NONE" else None
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()

        if verdict == "YES" and confidence >= 60:
            logger.info(f"🟡 VQS False Positive detected! log_id={log.id} | key='{key_phrase}' | conf={confidence}%")
            return {
                "log_id": log.id,
                "log": log,
                "key_phrase": key_phrase,
                "confidence": confidence,
                "ai_reasoning": reason,
                "vqs_reason": vqs_reason,
                "message_preview": text[:200],
            }

    except Exception as e:
        logger.debug(f"VQS audit API call error: {e}")

    return None


async def _notify_superadmins_false_positives(false_positives: list):
    """
    Sends Telegram notification to all superadmins with a summary of VQS false positives
    and inline buttons to approve/reject each correction.
    """
    try:
        from src.bot.alert_bot import bot
        from src.db.session import AsyncSessionLocal
        from src.db.models import Partner
        from sqlalchemy import select
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        if not bot:
            logger.warning("VQS Auditor: bot not available, cannot send notification")
            return

        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(Partner.telegram_id).where(Partner.role == "SUPERADMIN")
            )
            superadmin_ids = list(res.scalars().all())

        if not superadmin_ids:
            logger.warning("VQS Auditor: no superadmin IDs found")
            return

        # Send summary header
        summary_text = (
            f"🧪 <b>VQS САМОАУДИТ: найдены ложные срабатывания!</b>\n"
            f"──────────────────────────\n\n"
            f"ИИ проверил <b>{SAMPLE_SIZE}</b> сообщений, отклонённых VQS-фильтром за 24ч.\n"
            f"Обнаружено <b>{len(false_positives)}</b> потенциальных ложных срабатываний.\n\n"
            f"📋 Для каждого сообщения ниже нажмите:\n"
            f"• ✅ <b>Добавить в Whitelist</b> — ИИ прав, VQS ошибся\n"
            f"• ❌ <b>Пропустить</b> — VQS прав, это не лид\n\n"
            f"<i>Approved patterns будут добавлены в VQS Whitelist и больше не отрезаться.</i>"
        )

        for sa_id in superadmin_ids:
            try:
                await bot.send_message(sa_id, summary_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"VQS Auditor: failed to send summary to {sa_id}: {e}")

        # Send each false positive as individual message with action buttons
        for fp in false_positives[:10]:  # Cap at 10 per audit to avoid spam
            log = fp["log"]
            key_phrase = fp["key_phrase"]
            confidence = fp["confidence"]
            ai_reason = fp["ai_reasoning"]
            vqs_reason = fp["vqs_reason"]
            preview = fp["message_preview"]

            fp_text = (
                f"🟡 <b>Ложное срабатывание #{fp['log_id'][-6:]}</b>\n"
                f"──────────\n"
                f"🚫 <b>VQS отклонил:</b> <code>{vqs_reason[:80]}</code>\n"
                f"🔑 <b>ИИ нашёл ключ:</b> <code>{key_phrase or 'не определён'}</code>\n"
                f"📊 <b>Уверенность ИИ:</b> {confidence}%\n"
                f"💡 <b>Вывод ИИ:</b> {ai_reason}\n\n"
                f"💬 <b>Текст сообщения:</b>\n"
                f"<i>{preview[:300]}</i>"
            )

            # Build callback data for approve/reject
            # Format: vqs_approve:{log_id}:{key_phrase_escaped}
            kp_safe = (key_phrase or "")[:30].replace(":", "_").replace(" ", "_")
            log_id_str = str(log.id) if hasattr(log, 'id') else str(fp.get('log_id', ''))
            approve_cb = f"vqs_approve:{log_id_str}:{kp_safe}"
            reject_cb  = f"vqs_reject:{log_id_str}"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Добавить в Whitelist", callback_data=approve_cb),
                InlineKeyboardButton(text="❌ Пропустить", callback_data=reject_cb),
            ]])

            for sa_id in superadmin_ids:
                try:
                    await bot.send_message(sa_id, fp_text, parse_mode="HTML", reply_markup=keyboard)
                    await asyncio.sleep(0.3)  # Avoid flood
                except Exception as e:
                    logger.warning(f"VQS Auditor: failed to send FP card to {sa_id}: {e}")

    except Exception as e:
        logger.error(f"VQS Auditor notify error: {e}", exc_info=True)


async def vqs_recheck_single(log_id: str) -> dict:
    """
    Manually recheck a single VQS-dropped message via API (called from /ai/vqs-drops/{id}/recheck).
    Returns: {verdict, confidence, key_phrase, ai_reasoning, is_false_positive}
    """
    from src.db.session import AsyncSessionLocal
    from src.db.models import AIEvaluationLog
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(AIEvaluationLog).where(AIEvaluationLog.id == log_id)
        )
        log = res.scalars().first()

    if not log:
        return {"error": "Log not found"}

    try:
        from src.ai.groq_client import call_groq_api as call_api
    except ImportError:
        try:
            from src.ai.scorer import _call_ai_api as call_api
        except ImportError:
            return {"error": "AI API client not available"}

    result = await _classify_single_dropped(log, call_api)

    if result:
        return {
            "is_false_positive": True,
            "verdict": "YES",
            "confidence": result["confidence"],
            "key_phrase": result["key_phrase"],
            "ai_reasoning": result["ai_reasoning"],
            "vqs_reason": log.reasoning,
        }
    else:
        return {
            "is_false_positive": False,
            "verdict": "NO",
            "confidence": 0,
            "key_phrase": None,
            "ai_reasoning": "VQS correctly rejected this message — not a buyer-intent lead.",
            "vqs_reason": log.reasoning,
        }
