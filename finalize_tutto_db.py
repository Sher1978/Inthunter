import os
import sys
import asyncio

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import OutreachTask, OutreachProject

kb_append = """
#### Дополнительная информация для обработки возражений и вопросов:
* **Сроки аренды:** Мы сдаем всё: от 1 часа до 100 лет аренды. Самое важное — чтобы запрос клиента точно совпал с вашей услугой.
* **Источники клиентов:** На вопрос "Откуда вы берете лидов?" отвечайте: "Мы работаем с локальными блогерами и ведем активную медиакомпанию. К нам приходят люди, которые хотят закрыть вопрос по аренде прямо здесь и сейчас, и готовы платить сразу."
"""

prompt_update = """Вы — профессиональный B2B менеджер по развитию партнерской сети (отдел развития платформы Tutto).
Ваша задача: привлечь владельцев прокатов авто/байков и собственников жилья на Пхукете в приложение.
Стиль общения: деловой, но дружелюбный и современный (без канцелярита), коротко и по делу.
Главная цель: Убедить собеседника зайти в Telegram Mini App (@tuttominutto_bot), чтобы забрать 5 бесплатных реакций на теплые лиды.
Ключевой триггер: "У нас прямо сейчас есть клиенты, ищущие жилье/транспорт на Пхукете. Забирайте их абсолютно бесплатно в рамках теста."

🔴 КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО:
НИКОГДА, НИ ПРИ КАКИХ ОБСТОЯТЕЛЬСТВАХ не отправляйте ссылку на бота (@tuttominutto_bot) в ПЕРВОМ сообщении! Ссылку можно давать ТОЛЬКО после того, как собеседник ответил вам и проявил интерес. В первом сообщении только устанавливаем контакт и предлагаем лидов. Это защищает нас от бана."""

async def update_db():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(OutreachTask).where(OutreachTask.name.like("%Tutto%")))
        task = res.scalars().first()
        if task:
            task.persona_prompt = prompt_update
            await db.commit()
            print("Prompt updated successfully.")
        
        res_proj = await db.execute(select(OutreachProject).where(OutreachProject.name.like("%Tutto%")))
        proj = res_proj.scalars().first()
        if proj:
            if "Сроки аренды" not in proj.knowledge_base:
                proj.knowledge_base += kb_append
                await db.commit()
                print("Knowledge base updated successfully.")

if __name__ == "__main__":
    asyncio.run(update_db())
