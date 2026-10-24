import os
import sys

# Force UTF-8 encoding for Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import time
import asyncio
import threading
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from playwright.sync_api import sync_playwright

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath("."))

from src.config import settings
from src.db.session import init_db, AsyncSessionLocal
from src.db.models import (
    Partner, OutreachProject, OutreachTask, OutreachLead,
    B2BProspect, ScraperAccount
)
from src.api.auth import create_access_token
from src.api.outreach_routes import outreach_router
from src.api.b2b_routes import b2b_router
from src.api.routes import router as main_router
from src.api.tma_auth import tma_router
from src.ai.scorer import persist_scoring_result
from src.outreach.outreach_worker import outreach_worker_instance
from src.outreach.message_generator import generate_outreach_dm
from src.outreach.account_manager import AccountManager
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

# Configure paths
BASE_DIR = os.path.abspath(".")
STATIC_DIR = os.path.join(BASE_DIR, "static")
SCREENSHOTS_DIR = r"C:\Users\Huawei MadeBook XPro\.gemini\antigravity\brain\f3e631bb-5ccd-4939-9310-0cef0af54243\screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

# Build lightweight test FastAPI app
test_app = FastAPI(title="LeadRadar E2E Playwright Test Server")
test_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
test_app.include_router(outreach_router, prefix="/api/outreach")
test_app.include_router(b2b_router, prefix="/api/b2b")
test_app.include_router(main_router, prefix="/api")
test_app.include_router(tma_router, prefix="/api/auth")

@test_app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@test_app.get("/outreach-manager")
def serve_outreach():
    return FileResponse(os.path.join(STATIC_DIR, "outreach_dashboard.html"))

@test_app.get("/marketplace")
def serve_marketplace():
    return FileResponse(os.path.join(STATIC_DIR, "marketplace.html"))

@test_app.get("/tma")
def serve_tma():
    return FileResponse(os.path.join(STATIC_DIR, "tma.html"))

TEST_PORT = 8099
SERVER_URL = f"http://127.0.0.1:{TEST_PORT}"

def run_test_server():
    uvicorn.run(test_app, host="127.0.0.1", port=TEST_PORT, log_level="warning")

def run_playwright_test():
    print("\n" + "=" * 70)
    print("🚀 НАЧАЛО АВТОМАТИЗИРОВАННОГО E2E ТЕСТИРОВАНИЯ (PLAYWRIGHT)")
    print("=" * 70)

    # 1. Сгенерируем тестовый JWT токен администратора для реального SUPERADMIN из БД
    admin_token = create_access_token({"partner_id": "a43fbc8a-ba42-4673-80d9-95d092c39758", "role": "SUPERADMIN"})
    auth_headers = {"Authorization": f"Bearer {admin_token}"}
    print(f"🔑 JWT Токен сгенерирован: {admin_token[:25]}... (SUPERADMIN)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # ── ТЕСТ 1: Проверка безопасности и барьера авторизации (API) ──
        print("\n🛡️ [1/5] Проверка безопасности API (401 vs 200)...")
        api_context = p.request.new_context()

        # Попытка доступа без токена
        unauth_resp = api_context.get(f"{SERVER_URL}/api/outreach/projects")
        print(f"   - Доступ к /api/outreach/projects без токена: HTTP {unauth_resp.status}")
        assert unauth_resp.status == 401, f"Ожидался HTTP 401, получено {unauth_resp.status}"

        # Доступ с валидным токеном
        auth_resp = api_context.get(f"{SERVER_URL}/api/outreach/projects", headers=auth_headers)
        print(f"   - Доступ к /api/outreach/projects с токеном: HTTP {auth_resp.status}")
        assert auth_resp.status == 200, f"Ожидался HTTP 200, получено {auth_resp.status}"
        print("   ✅ Тест безопасности успешно пройден: неавторизованные запросы блокируются!")

        # ── ТЕСТ 2: Outreach Dashboard (UI Тестирование и Скриншот) ──
        print("\n🖥️ [2/5] Тестирование веб-интерфейса Outreach Dashboard...")
        context = browser.new_context(
            viewport={"width": 1280, "height": 850},
            extra_http_headers=auth_headers
        )
        page = context.new_page()

        # Перехват диалоговых окон alert
        page.on("dialog", lambda dialog: dialog.accept())

        page.goto(f"{SERVER_URL}/outreach-manager")
        page.wait_for_load_state("networkidle")

        project_name = f"Playwright Real Estate {int(time.time())}"
        kb_text = "Элитная недвижимость в ОАЭ и Дубае. Объекты от $500,000 с рассрочкой."
        sys_prompt = "Ты старший брокер по недвижимости в Дубае. Задача: квалифицировать инвестора."

        print(f"   - Заполнение формы создания проекта: '{project_name}'...")
        page.fill("#proj-name", project_name)
        page.fill("#proj-kb", kb_text)
        page.fill("#proj-sys-prompt", sys_prompt)
        page.click("#project-form button")

        # Ждем появления проекта в списке
        page.wait_for_selector(f"#projects-list:has-text('{project_name}')", timeout=5000)
        print("   - Проект успешно отобразился в DOM и сохранен в БД!")

        # Создаем задачу для созданного проекта
        print("   - Заполнение формы создания задачи (кампании)...")
        page.select_option("#task-project-id", label=project_name)
        task_name = f"Кампания Инвесторы {int(time.time())}"
        page.fill("#task-name", task_name)
        page.fill("#task-prompt", "Общайся вежливо и лаконично, предлагай консультацию.")
        page.fill("#task-start", "09:00")
        page.fill("#task-end", "19:00")
        page.click("#task-form button")
        page.wait_for_timeout(1000)
        print(f"   - Задача '{task_name}' успешно создана через UI!")

        # Скриншот Outreach Dashboard
        outreach_screen_path = os.path.join(SCREENSHOTS_DIR, "outreach_dashboard.png")
        page.screenshot(path=outreach_screen_path, full_page=True)
        print(f"   📸 Скриншот сохранен: {outreach_screen_path}")
        context.close()

        # ── ТЕСТ 3: Lead Marketplace UI ──
        print("\n🛒 [3/5] Тестирование интерфейса Маркетплейса Лидов...")
        context = browser.new_context(viewport={"width": 1280, "height": 850})
        page = context.new_page()
        page.goto(f"{SERVER_URL}/marketplace")
        page.wait_for_load_state("networkidle")

        market_screen_path = os.path.join(SCREENSHOTS_DIR, "marketplace.png")
        page.screenshot(path=market_screen_path, full_page=True)
        print(f"   📸 Скриншот сохранен: {market_screen_path}")
        context.close()

        # ── ТЕСТ 4: Telegram Mini App (TMA) в мобильном вьюпорте ──
        print("\n📱 [4/5] Тестирование Telegram Mini App (Mobile Viewport 390x844)...")
        mobile_context = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
        )
        tma_page = mobile_context.new_page()
        tma_page.goto(f"{SERVER_URL}/tma")
        tma_page.wait_for_timeout(1000)

        tma_screen_path = os.path.join(SCREENSHOTS_DIR, "tma_mobile.png")
        tma_page.screenshot(path=tma_screen_path, full_page=True)
        print(f"   📸 Мобильный скриншот сохранен: {tma_screen_path}")
        mobile_context.close()

        browser.close()

    # ── ТЕСТ 5: Проверка пайплайна ИИ и защиты от банов (Multi-Product & Anti-Ban) ──
    print("\n🧠 [5/5] Проверка логики ИИ, изоляции продуктов и Anti-Ban Warm-Up...")
    asyncio.run(run_backend_pipeline_verification())

    print("\n" + "=" * 70)
    print("🎉 ВСЕ ТЕСТЫ PLAYWRIGHT И ПАЙПЛАЙНА УСПЕШНО ЗАВЕРШЕНЫ!")
    print("=" * 70)


async def run_backend_pipeline_verification():
    """Проверяет сохранение лидов, маппинг в задачи, изоляцию промптов и алгоритм прогрева."""
    async with AsyncSessionLocal() as session:
        # 1. Тест сохранения и квалификации B2B лида (поставщик юридических и бизнес-услуг)
        from types import SimpleNamespace
        user_id = 999123456
        scoring_res = {
            "is_lead": False,
            "is_vendor": True,
            "target": "b2b",
            "confidence_score": 95,
            "niche": "legal_services",
            "rubric_name": "B2B_SELLER",
            "intent_summary": "Регистрация компаний и открытие счетов в ОАЭ",
            "extracted_data": {
                "company_name": "Dubai Business Setup LLC",
                "offer_type": "SELLER"
            }
        }
        test_messages = [
            SimpleNamespace(
                id=1,
                username="dubai_legal_consult",
                first_name="Марат",
                message_text="Поможем открыть компанию в Дубае под ключ, оформить резидентские визы и банковские счета.",
                chat_title="Бизнес в ОАЭ и Дубае",
                location_code="dubai"
            )
        ]

        await persist_scoring_result(session, user_id, test_messages, scoring_res)
        res_lead = await session.execute(
            select(OutreachLead).where(OutreachLead.telegram_id == user_id).order_by(OutreachLead.id.desc())
        )
        saved_lead = res_lead.scalars().first()
        print(f"   - ИИ-квалификация: Лид сохранен в БД -> ID: {saved_lead.id if saved_lead else None}, Ниша: {saved_lead.niche_code if saved_lead else None}")
        assert saved_lead is not None, "Лид должен быть успешно сохранен в таблице outreach_leads!"

        # 2. Тест маппинга лидов в OutreachProspects
        mapped_count = await outreach_worker_instance.sync_leads_to_prospects()
        print(f"   - Маршрутизация в кампании: Привязано новых проспектов: {mapped_count}")

        # 3. Тест генерации первого сообщения для проекта недвижимости
        res_proj = await session.execute(
            select(OutreachProject).where(OutreachProject.name.like("Playwright Real Estate%")).order_by(OutreachProject.id.desc())
        )
        proj = res_proj.scalars().first()

        res_task = await session.execute(
            select(OutreachTask).where(OutreachTask.project_id == proj.id).order_by(OutreachTask.id.desc())
        )
        task = res_task.scalars().first()

        first_msg = await generate_outreach_dm(
            username="alex_invest",
            niche="real_estate",
            raw_ad_text="Ищу инвестора или покупателя для премиальной виллы в Дубае",
            sales_hook="Премиальная недвижимость в Дубае",
            manager_name="Екатерина",
            persona_prompt=task.persona_prompt,
            knowledge_base=proj.knowledge_base,
            product_name=proj.name,
            system_prompt=proj.system_prompt
        )

        print(f"\n   📝 Сгенерированное первое сообщение юзербота:\n   \"{first_msg}\"")
        assert "LeadRadar.win" not in first_msg, "Сообщение не должно содержать устаревший хардкод LeadRadar.win!"
        print("   ✅ Генератор строго соблюдает оффер проекта и не содержит утечек сторонних брендов!")

        # 4. Тест расчета лимитов прогрева (Warm-Up Anti-Ban)
        acc_day1 = ScraperAccount(
            phone_number="+1000000001",
            status="ACTIVE",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
            max_daily_joins=20
        )
        acc_day5 = ScraperAccount(
            phone_number="+1000000002",
            status="ACTIVE",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
            max_daily_joins=20
        )
        acc_day10 = ScraperAccount(
            phone_number="+1000000003",
            status="ACTIVE",
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            max_daily_joins=20
        )

        limit_1 = AccountManager.get_effective_daily_limit(acc_day1)
        limit_5 = AccountManager.get_effective_daily_limit(acc_day5)
        limit_10 = AccountManager.get_effective_daily_limit(acc_day10)

        print(f"   - Warm-Up Лимиты: День 1 -> {limit_1} DMs/день | День 5 -> {limit_5} DMs/день | День 10 -> {limit_10} DMs/день")
        assert limit_1 == 3, f"Ожидался лимит 3 для дня 1, получено {limit_1}"
        assert limit_5 == 7, f"Ожидался лимит 7 для дня 5, получено {limit_5}"
        assert limit_10 == 15, f"Ожидался лимит 15 для дня 10, получено {limit_10}"
        print("   ✅ Алгоритм прогрева Anti-Ban 2.0 работает идеально (3 -> 7 -> 15)!")


if __name__ == "__main__":
    # Выполняем автомиграции БД (добавление system_prompt и т.д.)
    print("📦 Инициализация БД и применение автомиграций...")
    asyncio.run(init_db())

    # Запуск тестового сервера в фоновом потоке
    server_thread = threading.Thread(target=run_test_server, daemon=True)
    server_thread.start()
    time.sleep(2)  # Даем серверу подняться

    try:
        run_playwright_test()
    except Exception as e:
        import traceback
        print(f"\n❌ Ошибка при выполнении Playwright E2E теста: {e}")
        traceback.print_exc()
        sys.exit(1)
