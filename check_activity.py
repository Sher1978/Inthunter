import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import UserActivityLog, AIEvaluationLog
from sqlalchemy import select

async def check_user_activity():
    async with AsyncSessionLocal() as db:
        # Fetch all recent messages and filter in python
        stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(100)
        res = await db.execute(stmt)
        logs = res.scalars().all()
        
        found = False
        for log in logs:
            if "таргетолог" in log.message_text.lower():
                print(f"UserActivityLog ID: {log.id}")
                print(f"Message: {log.message_text}")
                print("-" * 50)
                found = True
        
        if not found:
            print("No matching UserActivityLog found in last 100.")
            
        print("Checking AIEvaluationLog...")
        stmt2 = select(AIEvaluationLog).order_by(AIEvaluationLog.created_at.desc()).limit(100)
        res2 = await db.execute(stmt2)
        eval_logs = res2.scalars().all()
        
        found_eval = False
        for log in eval_logs:
            if "таргетолог" in log.message_text.lower():
                print(f"AIEvaluationLog ID: {log.id}")
                print(f"Is Lead: {log.is_lead}")
                print(f"Reasoning: {log.reasoning}")
                print("-" * 50)
                found_eval = True
                
        if not found_eval:
            print("No matching AIEvaluationLog found in last 100.")

if __name__ == "__main__":
    asyncio.run(check_user_activity())
