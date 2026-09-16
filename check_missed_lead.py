import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import AIEvaluationLog
from sqlalchemy import select

async def check_eval():
    async with AsyncSessionLocal() as db:
        stmt = select(AIEvaluationLog).where(AIEvaluationLog.message_text.ilike("%Нужен проверенный таргетолог%")).order_by(AIEvaluationLog.created_at.desc()).limit(5)
        res = await db.execute(stmt)
        logs = res.scalars().all()
        
        for log in logs:
            print(f"Message: {log.message_text}")
            print(f"Is Lead: {log.is_lead}")
            print(f"Reasoning: {log.reasoning}")
            print("-" * 50)
            
        if not logs:
            print("No evaluation logs found for this message.")

        # Let's also check another message
        stmt = select(AIEvaluationLog).where(AIEvaluationLog.message_text.ilike("%Нужен таргетолог VK%")).order_by(AIEvaluationLog.created_at.desc()).limit(5)
        res = await db.execute(stmt)
        logs = res.scalars().all()
        
        for log in logs:
            print(f"Message: {log.message_text}")
            print(f"Is Lead: {log.is_lead}")
            print(f"Reasoning: {log.reasoning}")
            print("-" * 50)

if __name__ == "__main__":
    asyncio.run(check_eval())
