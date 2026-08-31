import asyncio
import sys
import os
from sqlalchemy import select, delete

sys.path.insert(0, os.path.abspath("."))
from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel, ChannelCandidate
from src.ai.grok_channel_finder import GrokChannelFinder

# 🏙️ 100% STRICTLY DUBAI DISTRICT & EXPAT COMMUNITY CHATS (Группы живого общения районов и экспатов Дубая)
DUBAI_COMMUNITY_DISTRICT_CHATS = [
    # Районные чаты жителей и экспатов Дубая (Dubai District & Neighborhood Chats)
    {"username": "@chatrudubai",              "title": "Дубай чат ОАЭ (Главный Сообщество)",      "niche": "community", "location": "dubai"},
    {"username": "@dubai_marina_chat",        "title": "Dubai Marina Residents & Expat Chat",     "niche": "community", "location": "dubai"},
    {"username": "@jbr_marina_rent",          "title": "JBR & Marina Expat Community",            "niche": "community", "location": "dubai"},
    {"username": "@businessbay_dubai_chat",    "title": "Business Bay Community Chat",             "niche": "community", "location": "dubai"},
    {"username": "@jvc_dubai_chat",           "title": "JVC & JVT Dubai Living & Community",      "niche": "community", "location": "dubai"},
    {"username": "@downtown_dubai_chat",      "title": "Downtown Dubai Expat Community",          "niche": "community", "location": "dubai"},
    {"username": "@palm_jumeirah_community",  "title": "Palm Jumeirah Residents Chat",            "niche": "community", "location": "dubai"},
    {"username": "@dubai_hills_community",    "title": "Dubai Hills Estate Residents",            "niche": "community", "location": "dubai"},
    {"username": "@damac_hills_chat",         "title": "Damac Hills Community & Living",          "niche": "community", "location": "dubai"},
    {"username": "@creek_harbour_dubai",      "title": "Dubai Creek Harbour Residents",           "niche": "community", "location": "dubai"},
    {"username": "@bluewaters_dubai_chat",     "title": "Bluewaters Island Expat Community",       "niche": "community", "location": "dubai"},

    # Сообщества живого общения, вопросов и взаимопомощи (Expat Life & Q/A Chats)
    {"username": "@dubai_expats_group",       "title": "Dubai Expats & Life Community",           "niche": "community", "location": "dubai"},
    {"username": "@dubai_life_chat",          "title": "Жизнь в Дубае | Вопросы и Ответы",        "niche": "community", "location": "dubai"},
    {"username": "@dubai_ask_help",           "title": "Дубай Вопросы & Взаимопомощь Экспатов",   "niche": "community", "location": "dubai"},
    {"username": "@dubai_services_chat",      "title": "Дубай Рекомендации & Услуги",             "niche": "community", "location": "dubai"},
    {"username": "@dubai_business_chat",      "title": "Дубай Нетворкинг & Предприниматели",      "niche": "b2b",       "location": "dubai"},
    {"username": "@biznesuae",                "title": "Бизнес Чат Дубай ОАЭ",                    "niche": "b2b",       "location": "dubai"}
]

async def seed_community_chats():
    sys.stdout.reconfigure(encoding="utf-8")
    print("=========================================================")
    print("🏙️ ИМПОРТ РАЙОННЫХ И ОБЩЕСТВЕННЫХ ЧАТОВ ДУБАЯ (COMMUNITY)")
    print("=========================================================\n")

    await init_db()

    # 1. Purge commercial ad/spam boards from MonitoredChannel
    print("1️⃣ Очистка досок коммерческого спама агентов из базы...")
    async with AsyncSessionLocal() as session:
        all_ch = list((await session.execute(select(MonitoredChannel))).scalars().all())
        deleted_count = 0
        for ch in all_ch:
            uname = (ch.username_or_link or '').lower()
            title = (ch.title or '').lower()
            # If it's a pure real estate seller board (realty, realtor, property, nedvizhimost) without community tag
            is_spam_board = any(k in uname or k in title for k in ['realtor', 'property_market', 'nedvizhimost', 'realty_market'])
            if is_spam_board:
                await session.delete(ch)
                deleted_count += 1
        await session.commit()
    print(f"   🧹 Удалено {deleted_count} рекламных досок спама агентов.\n")

    # 2. Add Dubai District & Community Chats directly into MonitoredChannel
    print("2️⃣ Импорт районных чатов жителей Дубая (Марина, JBR, Downtown, JVC, Business Bay)...")
    added_monitored = 0
    async with AsyncSessionLocal() as session:
        for c in DUBAI_COMMUNITY_DISTRICT_CHATS:
            u_clean = c["username"].strip().lower()
            existing = (await session.execute(
                select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(u_clean))
            )).scalar_one_or_none()

            if not existing:
                ch = MonitoredChannel(
                    username_or_link=u_clean,
                    title=c["title"],
                    niche_code=c["niche"],
                    location_code="dubai",
                    platform="telegram",
                    chat_type="group",
                    status="PENDING"
                )
                session.add(ch)
                added_monitored += 1
            else:
                existing.chat_type = "group"
                existing.location_code = "dubai"
        await session.commit()
    print(f"   ✅ Загружено {added_monitored} новых районных чатов живого общения Дубая.\n")

    # 3. Query Grok AI strictly for Dubai Neighborhood & Expat Community groups
    print("3️⃣ ИИ-Поиск Grok по районным чатам и сообществам экспатов Дубая...")
    grok_queries = [
        ("дубай марина чат жителей экспатов", "community"),
        ("чат jbr business bay дубай вопросы", "community"),
        ("дубай жвк jvc чат жителей вопросы", "community"),
        ("чат экспатов дубай рекомендации вопросы", "community")
    ]

    finder = GrokChannelFinder()
    added_candidates = 0
    for query, niche in grok_queries:
        print(f"   🔍 Grok Поиск по районному запросу: '{query}'...")
        try:
            results = await finder.search_channels_and_groups(keywords=query, niche_code=niche, limit=15)
            async with AsyncSessionLocal() as session:
                for r in results:
                    uname = r["username"].strip().lower()
                    if not uname.startswith("@") and not uname.startswith("http"):
                        uname = f"@{uname}"
                    
                    ex_mon = (await session.execute(
                        select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(uname))
                    )).scalar_one_or_none()

                    if not ex_mon:
                        ex_cand = (await session.execute(
                            select(ChannelCandidate).where(ChannelCandidate.username_or_link.ilike(uname))
                        )).scalar_one_or_none()
                        
                        if not ex_cand:
                            session.add(ChannelCandidate(
                                username_or_link=uname,
                                title=r.get("title", uname),
                                source="GROK_COMMUNITY_HARVESTER",
                                niche_code=niche,
                                location_code="dubai",
                                status="DISCOVERED"
                            ))
                            added_candidates += 1
                await session.commit()
        except Exception as err:
            print(f"   ⚠️ Ошибка поиска по запросу '{query}': {err}")

    print("\n=========================================================")
    print(f"🎉 СБОР РАЙОННЫХ ЧАТОВ ДУБАЯ ЗАВЕРШЕН!")
    print(f"🏙️ Районных чатов жителей Дубая в прослушке: {added_monitored}")
    print(f"🏙️ Районных кандидатов в очереди аудита: {added_candidates}")
    print("=========================================================\n")

if __name__ == "__main__":
    asyncio.run(seed_community_chats())
