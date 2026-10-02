import os
import sys
import asyncio

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import OutreachTask, OutreachProject

async def update_prompt():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(OutreachTask).where(OutreachTask.name.like("%Tutto%")))
        task = res.scalars().first()
        if task:
            task.persona_prompt = task.persona_prompt.replace("@TuttoMinutoBot", "@tuttominutto_bot")
            task.persona_prompt = task.persona_prompt.replace("TuttoMinutoBot", "tuttominutto_bot")
            await db.commit()
            print("Task prompt updated.")
            
        res_proj = await db.execute(select(OutreachProject).where(OutreachProject.name.like("%Tutto%")))
        proj = res_proj.scalars().first()
        if proj:
            proj.knowledge_base = proj.knowledge_base.replace("@TuttoMinutoBot", "@tuttominutto_bot")
            await db.commit()
            print("Knowledge base updated.")

if __name__ == "__main__":
    asyncio.run(update_prompt())
