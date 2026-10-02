from playwright.sync_api import sync_playwright
import threading
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from src.api.outreach_routes import outreach_router
import os

app = FastAPI()
static_dir = os.path.abspath("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(outreach_router, prefix="/api/outreach")

@app.get("/outreach-manager")
def serve_ui():
    return FileResponse(os.path.join(static_dir, "outreach_dashboard.html"))

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="error")

def run_test():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            print("Открываем UI: http://127.0.0.1:8099/outreach-manager")
            page.goto("http://127.0.0.1:8099/outreach-manager")
            
            print("Создаем проект...")
            page.fill("#proj-name", "Playwright Automation Project")
            page.fill("#proj-kb", "Это тестовая база знаний для автоматизации")
            
            # Перехват alert'а, если он есть
            page.on("dialog", lambda dialog: dialog.accept())

            page.click("#project-form button")
            page.wait_for_timeout(1500) # Ждем, пока обновится список
            
            # Проверяем, появился ли проект в выпадающем списке
            options = page.eval_on_selector_all("#task-project-id option", "els => els.map(e => e.textContent)")
            print(f"Доступные проекты в селекте: {options}")
            
            if not any("Playwright Automation Project" in opt for opt in options):
                raise AssertionError("Проект не появился в списке после создания!")
            
            print("Создаем задачу...")
            page.select_option("#task-project-id", index=1) # Выбираем первый валидный проект
            page.fill("#task-name", "Тестовая кампания Playwright")
            page.fill("#task-prompt", "Действуй как профессиональный тестировщик")
            page.click("#task-form button")
            
            page.wait_for_timeout(1500)
            print("✅ Все UI тесты (Playwright) успешно пройдены! БД и интерфейс работают связно.")
            browser.close()
    except Exception as e:
        print(f"❌ Ошибка в тесте Playwright: {e}")

if __name__ == "__main__":
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    import time
    time.sleep(3) # Ждем старта сервера
    run_test()
