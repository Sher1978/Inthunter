import asyncio
import logging
import sys
from sqlalchemy import select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("intent_hunter.seed_goa")

from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel, AIEvaluationLog, LeadSearchQuery

CURATED_GOA_CHANNELS = [
    # 🇮🇳 СЕВЕРНЫЙ ГОА: АРАМБОЛЬ, МАНДРЕМ, МОРДЖИМ, СИОЛИМ, КЕРИМ, ВАГАТОР
    {"username_or_link": "@arambol", "title": "New Goa People 🇮🇳 Арамболь Гоа чат", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Arambol_goa", "title": "🏝 GOA | Арамболь", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_mandrem", "title": "🏝 GOA | Мандрем", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_morjim", "title": "🏝️ GOA | Морджим / Ашвем", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_siolim", "title": "🏝️ GOA | Сиолим", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_kerim", "title": "🏝️ GOA | Керим", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_vagator", "title": "🏝 GOA | Вагатор", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_anjuna", "title": "🏝 GOA | Анжуна", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_mapusa", "title": "🏝 GOA | Мапуса & Панаджи", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@indiadacha", "title": "Дзен Дача Арамболь Индия", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@Goa_shantimaker", "title": "🏝 GOA | ShantiMaker 💃🕺", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@ChatGOA", "title": "Гоа чат. Общение и объявления", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@goa_party", "title": "GOA Party & Мероприятия", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@goachanels", "title": "🏝️ GOA | Гоанские группы Каталог", "niche_code": "community", "location_code": "goa"},
    {"username_or_link": "@allrusindia", "title": "Индия: каталог чатов и каналов", "niche_code": "community", "location_code": "goa"},

    # 🏠 АРЕНДА ЖИЛЬЯ И НЕДВИЖИМОСТЬ В ГОА
    {"username_or_link": "@myflats", "title": "🏝 GOA | Аренда жилья без посредников", "niche_code": "real_estate", "location_code": "goa"},
    {"username_or_link": "@arenda_goa", "title": "Гоа аренда | жильё недвижимость виллы", "niche_code": "real_estate", "location_code": "goa"},
    {"username_or_link": "@goahouses", "title": "Аренда жилья в Гоа (Дома, Апартаменты)", "niche_code": "real_estate", "location_code": "goa"},

    # 🛵 ТРАНСПОРТ, БАЙКИ И АВТО
    {"username_or_link": "@arendavgoa", "title": "Аренда в ГОА | Байки | Скутеры | Авто", "niche_code": "bike_rent", "location_code": "goa"},
    {"username_or_link": "@goabikes", "title": "Аренда байков в Гоа (Scooter / Enfield)", "niche_code": "bike_rent", "location_code": "goa"},

    # 💱 ОБМЕН ВАЛЮТ, РУПИИ, USDT
    {"username_or_link": "@PravaSimkiValuta", "title": "🏝 GOA | Валюта / USDT / Симки", "niche_code": "currency_exchange", "location_code": "goa"},
    {"username_or_link": "@Goa_Fullpower", "title": "Обмен Рубль / Рупия / USDT ВСЯ ИНДИЯ", "niche_code": "currency_exchange", "location_code": "goa"},

    # 💼 УСЛУГИ, БАРАХОЛКА, ЕДА
    {"username_or_link": "@goa_uslugi", "title": "🏝 GOA | Частные услуги и Мастера", "niche_code": "services_visa", "location_code": "goa"},
    {"username_or_link": "@Goa_market", "title": "🏝 GOA | Купить / Продать Барахолка", "niche_code": "general_market", "location_code": "goa"},
    {"username_or_link": "@goa_eda", "title": "🏝 GOA | Домашняя Еда и Доставка", "niche_code": "food_delivery", "location_code": "goa"},
]

GOA_SEARCH_QUERIES = [
    "сниму дом арамболь",
    "сниму жилье морджим",
    "аренда квартиры сиолим",
    "нужен байк арамболь",
    "аренда скутера гоа",
    "обмен рублей на рупии гоа",
    "обмен usdt рупии арамболь",
    "визаран индия гоа",
    "трансфер аэропорт манохар гоа",
    "клининг уборка виллы гоа"
]

async def seed_goa():
    logger.info("================================================================")
    logger.info("🇮🇳 SEEDING CURATED NORTH GOA CHANNELS & RESOURCES")
    logger.info("================================================================")

    await init_db()

    async with AsyncSessionLocal() as session:
        added_count = 0
        updated_count = 0
        for ch in CURATED_GOA_CHANNELS:
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == ch["username_or_link"])
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                new_ch = MonitoredChannel(
                    username_or_link=ch["username_or_link"],
                    niche_code=ch["niche_code"],
                    location_code=ch["location_code"],
                    title=ch["title"],
                    status="JOINED"
                )
                session.add(new_ch)
                added_count += 1
            else:
                existing.location_code = ch["location_code"]
                existing.niche_code = ch["niche_code"]
                existing.title = ch["title"]
                existing.status = "JOINED"
                updated_count += 1
        await session.commit()
        logger.info(f"✅ Seeding channels complete: {added_count} added, {updated_count} updated.")

    # 2. Seed default Goa Lead Search Queries
    async with AsyncSessionLocal() as session:
        added_queries = 0
        for q_text in GOA_SEARCH_QUERIES:
            stmt = select(LeadSearchQuery).where(LeadSearchQuery.query_text == q_text)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                session.add(LeadSearchQuery(query_text=q_text, interval_minutes=60))
                added_queries += 1
        await session.commit()
        logger.info(f"✅ Seeded {added_queries} new Goa search queries.")

    # 3. Seed initial AI Evaluation Logs with Goa exemplars
    async with AsyncSessionLocal() as session:
        check_stmt = select(AIEvaluationLog).where(AIEvaluationLog.location_code == "goa")
        existing_goa_logs = list((await session.execute(check_stmt)).scalars().all())

        if not existing_goa_logs:
            logger.info("Step 3: Seeding initial Goa AI Evaluation Logs with CoT reasoning...")
            goa_evals = [
                AIEvaluationLog(
                    user_id=883001,
                    username="@dmitry_arambol",
                    first_name="Дмитрий Волков",
                    chat_title="New Goa People 🇮🇳 Арамболь Гоа чат",
                    message_text="Ищу дом или этаж дома в Арамболе или Мандреме на весь сезон (с ноября по апрель). Обязательно с нормальной кухней, интернетом (оптоволокно) и кондиционером. Бюджет до 45 000 рупий в месяц. Нас двое, без животных.",
                    is_lead=True,
                    reasoning="Пользователь ищет долгосрочную аренду жилья (дом/этаж) в Северном Гоа (Арамболь/Мандрем) на весь сезон с четкими критериями (оптоволокно, кондиционер, кухня) и конкретным бюджетом (45 000 INR/мес). Прямой покупательский интент B2C.",
                    niche_code="real_estate",
                    location_code="goa",
                    temperature="HOT",
                    confidence_score=0.98
                ),
                AIEvaluationLog(
                    user_id=883002,
                    username="@kirill_rider_goa",
                    first_name="Кирилл",
                    chat_title="Аренда в ГОА | Байки | Скутеры | Авто",
                    message_text="Привет! Нужен скутер Honda Activa 6G или Dio в хорошем состоянии на 2 месяца в Сиолиме. Заберу сегодня, шлемы нужны на двоих. Кто сдает, напишите в ЛС с ценой.",
                    is_lead=True,
                    reasoning="Прямой запрос на аренду скутера на срок 2 месяца в конкретной локации (Сиолим, Северный Гоа) с готовностью забрать сегодня. Высокий покупательский интент.",
                    niche_code="bike_rent",
                    location_code="goa",
                    temperature="HOT",
                    confidence_score=0.95
                ),
                AIEvaluationLog(
                    user_id=883003,
                    username="@anna_travel_in",
                    first_name="Анна Смирнова",
                    chat_title="🏝 GOA | Валюта / USDT / Симки",
                    message_text="Ребята, подскажите, кто сейчас в Морджиме или Ашвеме меняет USDT на наличные рупии? Нужно поменять 800 USDT, желательно с доставкой к кафе Морджим.",
                    is_lead=True,
                    reasoning="Запрос на обмен валюты (USDT -> рупии) на сумму 800 USDT с геолокацией Морджим/Ашвем. Прямой целевой интент для ниши обмена валюты.",
                    niche_code="currency_exchange",
                    location_code="goa",
                    temperature="HOT",
                    confidence_score=0.94
                ),
                AIEvaluationLog(
                    user_id=994001,
                    username="@goa_sunrise",
                    first_name="Алина",
                    chat_title="New Goa People 🇮🇳 Арамболь Гоа чат",
                    message_text="Сегодня такой невероятный закат на драм-серкле в Арамболе! Кто идет вечером в шек слушать музыку?",
                    is_lead=False,
                    reasoning="Разговорное общение о досуге и закате. Коммерческий интент отсутствует. Отфильтровано как шум сообщества.",
                    niche_code="community",
                    location_code="goa",
                    temperature="WARM",
                    confidence_score=0.03
                )
            ]
            session.add_all(goa_evals)
            await session.commit()
            logger.info("✅ Goa AI Evaluation Logs seeded successfully!")

    logger.info("🇮🇳 NORTH GOA SETUP COMPLETE!")

if __name__ == "__main__":
    asyncio.run(seed_goa())
