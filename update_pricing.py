import os
import sys
import asyncio

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import OutreachProject

pricing_info = """
#### Финансы и Система Оплат (Точные цифры):
* **Для пользователя (клиента):** Платформа абсолютно бесплатна.
* **Для бизнеса (партнеров):** За регистрацию на платформе мы сразу дарим стартовый пакет в размере **20 долларов**!
* **Модель списаний (Как это работает):** Клиент создает заявку ➔ Бизнесы отвечают на неё своими оффертами (предложениями) с условиями ➔ Деньги списываются с баланса бизнеса **ТОЛЬКО** в тот момент, когда клиент принимает именно эту офферту.
* **Стоимость принятой офферты:**
  - $2 за аренду транспорта (байки/авто).
  - $5 за аренду жилья.
Таким образом, вы платите только за конкретного, закрытого на сделку лида, и начинаете с подарочных $20!
"""

async def update_db():
    async with AsyncSessionLocal() as db:
        res_proj = await db.execute(select(OutreachProject).where(OutreachProject.name.like("%Tutto%")))
        proj = res_proj.scalars().first()
        if proj:
            if "Финансы и Система Оплат (Точные цифры)" not in proj.knowledge_base:
                proj.knowledge_base += pricing_info
                await db.commit()
                print("Pricing updated successfully.")
            else:
                print("Pricing already exists.")

if __name__ == "__main__":
    asyncio.run(update_db())
