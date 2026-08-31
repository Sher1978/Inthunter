"""
Vacancy Group Poster Service
Auto-posts HR vacancies to external Telegram groups (e.g. @rabotgeorg) with contact masked.
Users click inline button → bot → pay Telegram Stars → contact revealed.
"""
import html
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func
from src.db.session import AsyncSessionLocal
from src.db.models import HRVacancy, VacancyGroupTarget, VacancyGroupPost

logger = logging.getLogger("intent_hunter.vacancy_poster")

# ─── Default seed group (added on first startup) ────────────────────────────
_DEFAULT_GROUPS = [
    {
        "group_username": "@rabotgeorg",
        "group_title": "Работа в Грузии",
        "stars_price": 50,
        "interval_hours": 48,
        "max_reposts": 3,
    }
]


async def ensure_default_groups():
    """Seeds default target groups into DB if not already present."""
    try:
        async with AsyncSessionLocal() as session:
            for grp in _DEFAULT_GROUPS:
                existing = (await session.execute(
                    select(VacancyGroupTarget).where(VacancyGroupTarget.group_username == grp["group_username"])
                )).scalar_one_or_none()
                if not existing:
                    session.add(VacancyGroupTarget(**grp))
            await session.commit()
            logger.info("✅ Vacancy group targets seeded.")
    except Exception as e:
        logger.warning(f"Notice seeding vacancy group targets: {e}")


def _build_vacancy_card(vacancy: HRVacancy, bot_username: str, group_username: str, stars_price: int) -> tuple:
    """
    Builds the masked vacancy post text + inline keyboard button.
    Returns (text, keyboard_markup_dict)
    """
    # Mask ALL contact identifiers from description
    import re
    safe_desc = vacancy.description or ""
    safe_desc = re.sub(r'https?://\S+', '🔒', safe_desc)
    safe_desc = re.sub(r't\.me/\S+', '🔒', safe_desc)
    safe_desc = re.sub(r'@[a-zA-Z0-9_]{4,32}', '🔒', safe_desc)
    safe_desc = re.sub(r'[\+]?[0-9]{7,15}', '🔒', safe_desc)
    safe_desc = re.sub(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', '🔒', safe_desc)

    # Encode group_username for deeplink (remove @)
    group_slug = group_username.lstrip("@").replace("_", "x")

    card_text = (
        f"💼 <b>{html.quote(vacancy.title)}</b>\n"
        f"📍 <b>Локация:</b> {html.quote((vacancy.location_code or 'Dubai').upper())}\n"
        f"💵 <b>Зарплата:</b> {html.quote(vacancy.salary_text or 'по договорённости')}\n"
        f"🏢 <b>Работодатель:</b> {html.quote(vacancy.company_name or 'прямой работодатель')}\n\n"
        f"📝 «{html.quote(safe_desc[:450])}»\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 <i>Контакты работодателя скрыты. Нажмите кнопку ниже — откройте за {stars_price} ⭐</i>"
    )

    deep_link = f"https://t.me/{bot_username}?start=buy_{vacancy.id}_{group_slug}"

    keyboard = {
        "inline_keyboard": [[{
            "text": f"💫 Узнать контакт HR ({stars_price} ⭐)",
            "url": deep_link
        }]]
    }

    return card_text, keyboard


async def post_vacancy_to_group(
    vacancy: HRVacancy,
    target: VacancyGroupTarget,
    bot_username: str,
    post_number: int = 1
) -> bool:
    """Posts a vacancy card to a target group via hr_bot."""
    try:
        from src.bot.hr_bot import hr_bot
        if not hr_bot:
            logger.warning("hr_bot not initialized, skipping group post.")
            return False

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        card_text, kb_dict = _build_vacancy_card(vacancy, bot_username, target.group_username, target.stars_price)

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"💫 Узнать контакт HR ({target.stars_price} ⭐)",
                url=kb_dict["inline_keyboard"][0][0]["url"]
            )
        ]])

        msg = await hr_bot.send_message(
            chat_id=target.group_username,
            text=card_text,
            parse_mode="HTML",
            reply_markup=kb
        )

        # Record the post
        async with AsyncSessionLocal() as session:
            session.add(VacancyGroupPost(
                vacancy_id=vacancy.id,
                group_username=target.group_username,
                message_id=msg.message_id if msg else None,
                post_number=post_number
            ))
            # Update target last_posted_at
            t = (await session.execute(
                select(VacancyGroupTarget).where(VacancyGroupTarget.id == target.id)
            )).scalar_one_or_none()
            if t:
                t.last_posted_at = datetime.now(timezone.utc)
            await session.commit()

        logger.info(f"✅ Posted vacancy {vacancy.id} (post #{post_number}) to {target.group_username}")
        return True

    except Exception as e:
        logger.warning(f"Notice posting vacancy {vacancy.id} to {target.group_username}: {e}")
        return False


async def post_new_vacancy_to_all_groups(vacancy: HRVacancy):
    """Called immediately when a new vacancy is created. Posts to all active groups."""
    try:
        from src.config import settings
        bot_username = getattr(settings, "HR_BOT_USERNAME", "HR_Radar_Bot")

        async with AsyncSessionLocal() as session:
            targets = list((await session.execute(
                select(VacancyGroupTarget).where(VacancyGroupTarget.is_active == True)
            )).scalars().all())

        for target in targets:
            # Check vacancy was not already posted to this group
            async with AsyncSessionLocal() as session:
                post_count = (await session.execute(
                    select(func.count(VacancyGroupPost.id)).where(
                        VacancyGroupPost.vacancy_id == vacancy.id,
                        VacancyGroupPost.group_username == target.group_username
                    )
                )).scalar() or 0

            if post_count >= target.max_reposts:
                continue

            await post_vacancy_to_group(vacancy, target, bot_username, post_number=post_count + 1)
            await asyncio.sleep(3)  # pacing between groups

    except Exception as e:
        logger.error(f"Error posting vacancy to groups: {e}")


async def post_new_vacancy_to_all_groups_delayed(vacancy: HRVacancy, delay_seconds: int = 1800):
    """
    Posts a new vacancy to all active groups WITH a 30-minute delay.
    VIP subscribers already received the vacancy instantly via notify_hr_vip_subscribers().
    This ensures VIP subscribers always have a 30-minute head start.
    """
    if delay_seconds > 0:
        logger.info(f"⏳ Group posting for vacancy {vacancy.id} delayed {delay_seconds//60}min (VIP head start).")
        await asyncio.sleep(delay_seconds)
    await post_new_vacancy_to_all_groups(vacancy)


async def run_group_poster_loop():
    """
    Background loop: every 4 hours re-checks published vacancies
    and re-posts to groups that haven't received this vacancy recently.
    Max 3 reposts per vacancy per group, interval 48h.
    """
    logger.info("🚀 Vacancy Group Poster Loop started.")
    await ensure_default_groups()
    await asyncio.sleep(300)  # 5 min grace period on startup

    while True:
        try:
            from src.config import settings
            bot_username = getattr(settings, "HR_BOT_USERNAME", "HR_Radar_Bot")

            async with AsyncSessionLocal() as session:
                targets = list((await session.execute(
                    select(VacancyGroupTarget).where(VacancyGroupTarget.is_active == True)
                )).scalars().all())

                # Only vacancies created in last 7 days
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                vacancies = list((await session.execute(
                    select(HRVacancy).where(
                        HRVacancy.status == "PUBLISHED",
                        HRVacancy.created_at >= cutoff
                    ).order_by(HRVacancy.created_at.desc()).limit(50)
                )).scalars().all())

            now_utc = datetime.now(timezone.utc)

            for target in targets:
                for vac in vacancies:
                    async with AsyncSessionLocal() as session:
                        posts = list((await session.execute(
                            select(VacancyGroupPost).where(
                                VacancyGroupPost.vacancy_id == vac.id,
                                VacancyGroupPost.group_username == target.group_username
                            ).order_by(VacancyGroupPost.posted_at.desc())
                        )).scalars().all())

                    post_count = len(posts)
                    if post_count >= target.max_reposts:
                        continue  # Already hit repost limit

                    last_post_at = posts[0].posted_at if posts else None
                    interval_ok = (
                        last_post_at is None or
                        (now_utc - last_post_at).total_seconds() >= target.interval_hours * 3600
                    )

                    if interval_ok:
                        await post_vacancy_to_group(vac, target, bot_username, post_number=post_count + 1)
                        await asyncio.sleep(5)  # pacing between posts

        except Exception as e:
            logger.error(f"Vacancy Group Poster loop error: {e}")

        await asyncio.sleep(4 * 3600)  # Check every 4 hours
