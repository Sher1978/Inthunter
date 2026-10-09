import asyncio
from sqlalchemy import text
from src.db.session import engine

async def run_patch():
    try:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE b2b_prospects ADD COLUMN platform VARCHAR(50) DEFAULT 'telegram'"))
            print("Successfully added platform column to b2b_prospects")
    except Exception as e:
        print("Error adding platform column:", e)

if __name__ == "__main__":
    asyncio.run(run_patch())
