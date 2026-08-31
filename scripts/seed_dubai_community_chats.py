import asyncio
import sys
import os
from sqlalchemy import select

sys.path.insert(0, os.path.abspath("."))
from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel, ChannelCandidate
from src.ai.grok_channel_finder import GrokChannelFinder

# 🏙️ ТРИ КАТЕГОРИИ ЖИВЫХ СООБЩЕСТВ СКАУТА ДУБАЯ (SCOUT 3-CATEGORY SYSTEM)
DUBAI_SCOUT_3_CATEGORIES = {
    # --------------------------------------------------------------------------
    # КАТЕГОРИЯ 1: Районные чаты жителей и ЖК (DISTRICT_NEIGHBORHOOD)
    # Люди живут в конкретных районах и просят рекомендации жилья/услуг на месте.
    # --------------------------------------------------------------------------
    "DISTRICT_NEIGHBORHOOD": [
        {"username": "@dubai_marina_chat",        "title": "Dubai Marina Residents & Expat Chat",     "niche": "district_marina"},
        {"username": "@jbr_marina_rent",          "title": "JBR & Marina Expat Community",            "niche": "district_jbr"},
        {"username": "@businessbay_dubai_chat",    "title": "Business Bay Community Chat",             "niche": "district_businessbay"},
        {"username": "@jvc_dubai_chat",           "title": "JVC & JVT Dubai Living & Community",      "niche": "district_jvc"},
        {"username": "@downtown_dubai_chat",      "title": "Downtown Dubai Expat Community",          "niche": "district_downtown"},
        {"username": "@palm_jumeirah_community",  "title": "Palm Jumeirah Residents Chat",            "niche": "district_palm"},
        {"username": "@dubai_hills_community",    "title": "Dubai Hills Estate Residents",            "niche": "district_dubaihills"},
        {"username": "@damac_hills_chat",         "title": "Damac Hills Community & Living",          "niche": "district_damac"},
        {"username": "@creek_harbour_dubai",      "title": "Dubai Creek Harbour Residents",           "niche": "district_creek"},
        {"username": "@bluewaters_dubai_chat",     "title": "Bluewaters Island Expat Community",       "niche": "district_bluewaters"}
    ],

    # --------------------------------------------------------------------------
    # КАТЕГОРИЯ 2: Экспаты, Вопросы & Бытовая Взаимопомощь (EXPAT_COMMUNITY_QA)
    # Бытовые вопросы, рекомендации релокации, семьи, визы, советчики.
    # --------------------------------------------------------------------------
    "EXPAT_COMMUNITY_QA": [
        {"username": "@chatrudubai",              "title": "Дубай чат ОАЭ (Главный Сообщество)",      "niche": "expat_qa"},
        {"username": "@dubai_expats_group",       "title": "Dubai Expats & Life Community",           "niche": "expat_qa"},
        {"username": "@dubai_life_chat",          "title": "Жизнь в Дубае | Вопросы и Ответы",        "niche": "expat_qa"},
        {"username": "@dubai_ask_help",           "title": "Дубай Вопросы & Взаимопомощь Экспатов",   "niche": "expat_qa"},
        {"username": "@dubai_services_chat",      "title": "Дубай Рекомендации & Услуги",             "niche": "services_visa"},
        {"username": "@dubai_money_exchange",        "title": "Дубай Обмен Валюты & Крипта",              "niche": "currency_exchange"}
    ],

    # --------------------------------------------------------------------------
    # КАТЕГОРИЯ 3: Бизнес, Нетворкинг & Услуги (BUSINESS_NETWORKING)
    # Предприниматели, B2B контрактеры, работа, фриланс, коммерческое сотрудничество.
    # --------------------------------------------------------------------------
    "BUSINESS_NETWORKING": [
        {"username": "@biznesuae",                "title": "Бизнес Чат Дубай ОАЭ",                    "niche": "b2b"},
        {"username": "@dubai_business_chat",      "title": "Дубай Нетворкинг & Предприниматели",      "niche": "b2b"},
        {"username": "@jobs_in_dubai",               "title": "Jobs in Dubai , UAE",                      "niche": "hr_hiring"},
        {"username": "@dubai_work24",                "title": "Дубай работа | Jobs in Dubai OAE",         "niche": "hr_hiring"},
        {"username": "@workinuae",                   "title": "Jobs in UAE / Работа в ОАЭ",               "niche": "hr_hiring"},
        {"username": "@beautyservicesdubai",         "title": "Сфера красоты ОАЭ",                        "niche": "services"},
        {"username": "@oae_visa",                    "title": "Оформление визы | EasyVisa World",         "niche": "services_visa"}
    ]
}

async def run_scout_harvest():
    sys.stdout.reconfigure(encoding="utf-8")
    print("==========================================================================")
    print("🤖 ИИ-СКАУТИНГ ДУБАЯ: СБОР ЧАТОВ ПО 3М КЛЮЧЕВЫМ КАТЕГОРИЯМ СООБЩЕСТВ")
    print("==========================================================================\n")

    await init_db()

    # 1. Direct Seeding of Curated Chats into MonitoredChannels
    total_added = 0
    async with AsyncSessionLocal() as session:
        for cat_name, items in DUBAI_SCOUT_3_CATEGORIES.items():
            print(f"📁 Категория Скаута [{cat_name}]: Импорт {len(items)} целевых групп...")
            for c in items:
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
                    total_added += 1
                else:
                    existing.chat_type = "group"
                    existing.location_code = "dubai"
        await session.commit()
    print(f"   ✅ Всего закружено {total_added} проверенных групп живого общения по 3 категориям.\n")

    # 2. Run Grok Scout 3-Category Discovery
    print("2️⃣ Запуск Grok Scout Search по 3м категориям...")
    finder = GrokChannelFinder()
    scout_results = await finder.search_3_scout_categories(location="dubai", limit_per_category=10)

    total_candidates = 0
    async with AsyncSessionLocal() as session:
        for cat_key, candidates in scout_results.items():
            print(f"   🔎 Категория Скаута [{cat_key}]: Найдено {len(candidates)} кандидатов Grok...")
            for cand in candidates:
                uname = cand.get("username", "").strip().lower()
                if not uname:
                    continue
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
                            title=cand.get("title", uname),
                            source=f"SCOUT_{cat_key}",
                            niche_code=cat_key.lower(),
                            location_code="dubai",
                            status="DISCOVERED"
                        ))
                        total_candidates += 1
        await session.commit()

    print("\n==========================================================================")
    print("🎉 СКАУТИНГ ПО 3М КАТЕГОРИЯМ УСПЕШНО ЗАВЕРШЕН!")
    print(f"📊 Добавлено целевых чатов в прослушку (MonitoredChannel): {total_added}")
    print(f"📊 Новых кандидатов в очереди аудита (ChannelCandidate): {total_candidates}")
    print("==========================================================================\n")

if __name__ == "__main__":
    asyncio.run(run_scout_harvest())
