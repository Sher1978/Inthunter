import asyncio
import sys
import os
from sqlalchemy import select

sys.path.insert(0, os.path.abspath("."))
from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel, ChannelCandidate
from src.ai.grok_channel_finder import GrokChannelFinder

# 🇦🇪 100% STRICTLY DUBAI TELEGRAM GROUPS & CHANNELS
CURATED_DUBAI_CHATS = [
    # Недвижимость и Аренда Дубая (Dubai Real Estate & Rentals)
    {"username": "@dubairealestate_chat",      "title": "Дубай Недвижимость | Аренда & Продажа",  "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_realty_chat",          "title": "Dubai Realty & Expat Community",         "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_rent_flat",           "title": "Дубай Аренда Квартир & Вилл",            "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_property_market",      "title": "Dubai Property Market & Deals",          "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_realtors_uae",         "title": "Риелторы Дубая & Агенты",                "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_housing_chat",         "title": "Дубай Поиск Жилья & Сдача",              "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_nedvizhimost_oae",      "title": "Дубай недвижимость | ОАЭ",                 "niche": "real_estate", "location": "dubai"},
    {"username": "@dubaiNedvizhimost",           "title": "Дубай Недвижимость",                       "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_realty",                "title": "Dubai Realty",                             "niche": "real_estate", "location": "dubai"},
    {"username": "@emiratesrealestate",          "title": "Emirates Real Estate",                     "niche": "real_estate", "location": "dubai"},
    {"username": "@rent_in_dubai",               "title": "Чат аренды недвижимости в Дубае",          "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_property_chat",         "title": "Недвижимость ОАЭ / Дубай",                 "niche": "real_estate", "location": "dubai"},
    {"username": "@dubairenta",                  "title": "Аренда квартир в Дубае",                   "niche": "real_estate", "location": "dubai"},
    {"username": "@jbr_marina_rent",             "title": "Marina & JBR Rentals",                     "niche": "real_estate", "location": "dubai"},
    {"username": "@businessbay_dubai_chat",       "title": "Business Bay Realty Chat",                 "niche": "real_estate", "location": "dubai"},
    {"username": "@jvc_dubai_chat",              "title": "JVC & JVT Dubai Living",                   "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_villas_rent",           "title": "Аренда Вилл Дубай",                        "niche": "real_estate", "location": "dubai"},
    {"username": "@dubai_shortstay_rent",        "title": "Краткосрочная Аренда Дубай",               "niche": "real_estate", "location": "dubai"},

    # Общение, Бизнес, Услуги, Визы, Обмен Валют и Вакансии (Dubai Services & Expat Life)
    {"username": "@chatrudubai",                 "title": "Дубай чат ОАЭ, Dubai chat UAE",            "niche": "community",   "location": "dubai"},
    {"username": "@dubai_services_chat",         "title": "Дубай Услуги & Специалисты",              "niche": "services_visa", "location": "dubai"},
    {"username": "@dubai_money_exchange",        "title": "Дубай Обмен Валюты & Крипта",              "niche": "currency_exchange", "location": "dubai"},
    {"username": "@dubai_auto_rent",             "title": "Дубай Аренда Авто & Каско",                "niche": "auto_kasko",  "location": "dubai"},
    {"username": "@dubai_expats_group",          "title": "Dubai Expats & Business Community",         "niche": "b2b",         "location": "dubai"},
    {"username": "@jobs_in_dubai",               "title": "Jobs in Dubai , UAE",                      "niche": "hr_hiring",   "location": "dubai"},
    {"username": "@beautyservicesdubai",         "title": "Сфера красоты ОАЭ",                        "niche": "medical",     "location": "dubai"},
    {"username": "@oae_visa",                    "title": "Оформление визы | EasyVisa World",         "niche": "services_visa", "location": "dubai"},
    {"username": "@dubai_hotel_jobs",            "title": "Dubai hotel Jobs",                         "niche": "hr_hiring",   "location": "dubai"},
    {"username": "@mnogovacansii",               "title": "Работа в Дубае",                           "niche": "hr_hiring",   "location": "dubai"},
    {"username": "@jobs_in_dubai_uaee",          "title": "Работа в Дубае и Эмиратах - Jobs in Dubai", "niche": "hr_hiring",   "location": "dubai"},
    {"username": "@dubai_work24",                "title": "Дубай работа | Jobs in Dubai OAE",         "niche": "hr_hiring",   "location": "dubai"},
    {"username": "@workinuae",                   "title": "Jobs in UAE / Работа в ОАЭ",               "niche": "hr_hiring",   "location": "dubai"},
    {"username": "@biznesuae",                   "title": "Бизнес чат Дубай / ОАЭ",                   "niche": "b2b",         "location": "dubai"},
    {"username": "@dubai_business_chat",         "title": "Дубай Бизнес & Нетворкинг",                "niche": "b2b",         "location": "dubai"}
]

async def run_dubai_harvest():
    sys.stdout.reconfigure(encoding="utf-8")
    print("=========================================================")
    print("🇦🇪 СБОР И ИМПОРТ ЦЕЛЕВЫХ ЧАТОВ ДУБАЯ (DUBAI, UAE)")
    print("=========================================================\n")

    # Initialize DB Schema & Migrations first
    await init_db()

    added_monitored = 0
    added_candidates = 0

    # 1. Import Curated Dubai Target Chats directly into MonitoredChannel
    print("1️⃣ Импорт проверенного пула чатов Дубая в прослушку...")
    async with AsyncSessionLocal() as session:
        for c in CURATED_DUBAI_CHATS:
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
                existing.location_code = "dubai"
        await session.commit()
    print(f"   ✅ Добавлено {added_monitored} целевых чатов Дубая в monitored_channels.\n")

    # 2. Run Grok AI Search Harvester strictly for Dubai queries
    print("2️⃣ Запуск ИИ-Сборщика Grok Search для локации Дубай...")
    dubai_queries = [
        ("чат аренда недвижимости дубай", "real_estate"),
        ("дубай снять квартиру без посредников", "real_estate"),
        ("дубай аренда виллы марина jbr", "real_estate"),
        ("обмен валюты дубай крипта наличные", "currency_exchange"),
        ("дубай услуги визы резидентство", "services_visa"),
        ("бизнес нетворкинг дубай оаэ", "b2b")
    ]

    finder = GrokChannelFinder()
    for query, niche in dubai_queries:
        print(f"   🔍 Grok ИИ-Поиск по запросу: '{query}'...")
        try:
            results = await finder.search_channels_and_groups(keywords=query, niche_code=niche, limit=15)
            async with AsyncSessionLocal() as session:
                for r in results:
                    uname = r["username"].strip().lower()
                    if not uname.startswith("@") and not uname.startswith("http"):
                        uname = f"@{uname}"
                    
                    # Check if exists in MonitoredChannel
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
                                source="GROK_HARVESTER",
                                niche_code=niche,
                                location_code="dubai",
                                status="DISCOVERED"
                            ))
                            added_candidates += 1
                await session.commit()
        except Exception as err:
            print(f"   ⚠️ Ошибка при поиске по запросу '{query}': {err}")

    print("\n=========================================================")
    print(f"🎉 СБОР ПО ДУБАЮ УСПЕШНО ЗАВЕРШЕН!")
    print(f"🇦🇪 Прямых целевых чатов Дубая в прослушке: {added_monitored}")
    print(f"🇦🇪 Новых кандидатов Дубая в очереди ИИ-аудита: {added_candidates}")
    print("=========================================================\n")

if __name__ == "__main__":
    asyncio.run(run_dubai_harvest())
