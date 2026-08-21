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
logger = logging.getLogger("intent_hunter.seed_curated")

from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel

CURATED_CHANNELS = [
    # 🇻🇳 NHATRANG TOP 25
    {"username_or_link": "@nhatrang_chat", "title": "Чат Нячанга | Вьетнам Общение", "niche_code": "community", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_realty", "title": "Аренда Недвижимости Нячанг", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_doska", "title": "Барахолка Нячанг Объявления", "niche_code": "general_market", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_services", "title": "Услуги и Визаран Нячанг", "niche_code": "services_visa", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_moto", "title": "Аренда Байков & Трансфер Нячанг", "niche_code": "bike_rent", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_rent_apartments", "title": "Аренда Квартир Нячанг", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_longterm_rent", "title": "Долгосрочная Аренда Нячанг", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@muong_thanh_nhatrang_chat", "title": "Muong Thanh Apartments", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@scenia_goldcoast_living", "title": "Scenia Bay & Gold Coast Living", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_exchange_currency", "title": "Обмен Валют и USDT Нячанг", "niche_code": "currency_exchange", "location_code": "nhatrang"},
    {"username_or_link": "@vietnam_visa_run_chat", "title": "Визаран и Визы Вьетнам", "niche_code": "services_visa", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_jobs_vacancies", "title": "Работа и Вакансии Нячанг", "niche_code": "services_visa", "location_code": "nhatrang"},
    {"username_or_link": "@camranh_resort_rent", "title": "Камрань Аренда и Отели", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_flea_market", "title": "Барахолка и Обновки Нячанг", "niche_code": "general_market", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_beauty_masters", "title": "Бьюти и Мастера Нячанг", "niche_code": "services_visa", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_med_doctors", "title": "Врачи и Медицина Нячанг", "niche_code": "services_visa", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_cleaning_service", "title": "Клининг и Уборка Нячанг", "niche_code": "services_visa", "location_code": "nhatrang"},
    {"username_or_link": "@camranh_airport_transfer", "title": "Трансфер Аэропорт Камрань", "niche_code": "bike_rent", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_expats_group", "title": "Клуб Экспатов Нячанг", "niche_code": "community", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_daily_rent", "title": "Посуточная Аренда Квартир", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_direct_owners", "title": "Прямые Собственники Нячанг", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_home_finder", "title": "Подбор Жилья под Ключ", "niche_code": "real_estate", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_food_seafood", "title": "Рестораны и Доставка Нячанг", "niche_code": "community", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_moto_delivery", "title": "Доставка и Сервис Байков", "niche_code": "bike_rent", "location_code": "nhatrang"},
    {"username_or_link": "@nhatrang_events_tickets", "title": "Мероприятия и Анонсы Нячанг", "niche_code": "community", "location_code": "nhatrang"},

    # 🇦🇪 DUBAI TOP 25
    {"username_or_link": "@dubai_realty_chat", "title": "Дубай Недвижимость Чат", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@rent_in_dubai", "title": "Чат аренды недвижимости в Дубае", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@dubai_property_chat", "title": "Недвижимость ОАЭ / Дубай", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@dubairenta", "title": "Аренда квартир в Дубае", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@jbr_marina_rent", "title": "Marina & JBR Rentals", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@businessbay_dubai_chat", "title": "Business Bay Realty Chat", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@jvc_dubai_chat", "title": "JVC & JVT Dubai Living", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@dubai_villas_rent", "title": "Аренда Вилл Дубай", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@dubai_shortstay_rent", "title": "Краткосрочная Аренда Дубай", "niche_code": "real_estate", "location_code": "dubai"},
    {"username_or_link": "@biznesuae", "title": "Бизнес чат Дубай / ОАЭ", "niche_code": "services_visa", "location_code": "dubai"},
    {"username_or_link": "@dubai_business_chat", "title": "Дубай Бизнес & Нетворкинг", "niche_code": "services_visa", "location_code": "dubai"},
    {"username_or_link": "@uae_company_setup", "title": "Открытие Компаний ОАЭ", "niche_code": "services_visa", "location_code": "dubai"},
    {"username_or_link": "@oae_visa", "title": "Визы & Релокация ОАЭ", "niche_code": "services_visa", "location_code": "dubai"},
    {"username_or_link": "@dubai_tax_legal", "title": "Налоги & Бухгалтерия Дубай", "niche_code": "services_visa", "location_code": "dubai"},
    {"username_or_link": "@dubai_cars_chat", "title": "Авто Дубай / Аренда & Продажа", "niche_code": "auto_kasko", "location_code": "dubai"},
    {"username_or_link": "@dubai_supercars_rent", "title": "Аренда Суперкаров Дубай", "niche_code": "auto_kasko", "location_code": "dubai"},
    {"username_or_link": "@uae_car_market", "title": "Купля-Продажа Авто ОАЭ", "niche_code": "auto_kasko", "location_code": "dubai"},
    {"username_or_link": "@dubai_car_selection", "title": "Автоподбор Дубай", "niche_code": "auto_kasko", "location_code": "dubai"},
    {"username_or_link": "@crypto_dybai", "title": "Обмен Валют и Крипта Дубай", "niche_code": "currency_exchange", "location_code": "dubai"},
    {"username_or_link": "@dubai_currency_exchange", "title": "Обмен Валюты Дубай Чат", "niche_code": "currency_exchange", "location_code": "dubai"},
    {"username_or_link": "@dubai_p2p_crypto", "title": "P2P Crypto Dubai", "niche_code": "currency_exchange", "location_code": "dubai"},
    {"username_or_link": "@dubai_usdt_cash", "title": "USDT / Cash Dubai Exchange", "niche_code": "currency_exchange", "location_code": "dubai"},
    {"username_or_link": "@dubai_rub_aed_exchange", "title": "Обмен Рубли / Дирхамы", "niche_code": "currency_exchange", "location_code": "dubai"},
    {"username_or_link": "@rudubaichat", "title": "Русскоязычное коммьюнити Дубай", "niche_code": "community", "location_code": "dubai"},
    {"username_or_link": "@chat_dubai", "title": "Главный Чат Русскоязычных жителей Дубай", "niche_code": "community", "location_code": "dubai"},
]

async def seed_nhatrang():
    logger.info("================================================================")
    logger.info("🇻🇳🇦🇪 SEEDING TOP-50 CURATED CHANNELS (NHATRANG & DUBAI)")
    logger.info("================================================================")

    await init_db()
    async with AsyncSessionLocal() as session:
        for ch in CURATED_CHANNELS:
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
            else:
                existing.location_code = ch["location_code"]
                existing.status = "JOINED"
                existing.title = ch["title"]
        await session.commit()
    logger.info("Curated Top-50 channels seeding complete.")

if __name__ == "__main__":
    asyncio.run(seed_nhatrang())
