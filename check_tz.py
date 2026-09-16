import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://inthunter:260669@localhost:5432/inthunter_db')
    row = await conn.fetchrow('SELECT created_at FROM leads ORDER BY created_at DESC LIMIT 1')
    print(repr(row['created_at']))
    await conn.close()

asyncio.run(main())
