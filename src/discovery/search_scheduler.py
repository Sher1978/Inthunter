import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import LeadSearchQuery
from src.discovery.global_message_search import GlobalMessageSearcher

logger = logging.getLogger("intent_hunter.glde")

async def run_glde_scheduler_loop():
    """
    Background loop that picks up active LeadSearchQueries and executes them via GLDE.
    """
    logger.info("🚀 Global Lead Discovery Engine (GLDE) Scheduler started.")
    await asyncio.sleep(20) # wait for system to boot
    
    while True:
        try:
            async with AsyncSessionLocal() as session:
                # Find queries that need to be run (last_run_at + interval_minutes < now, or never run)
                now = datetime.now(timezone.utc)
                stmt = select(LeadSearchQuery).where(LeadSearchQuery.is_active == True)
                res = await session.execute(stmt)
                queries = list(res.scalars().all())
                
                queries_to_run = []
                for q in queries:
                    if not q.last_run_at or (now - q.last_run_at).total_seconds() / 60 >= q.interval_minutes:
                        queries_to_run.append(q)
                
                if queries_to_run:
                    logger.info(f"🔄 GLDE Scheduler: Found {len(queries_to_run)} active queries ready for execution.")
                    
                    for q in queries_to_run:
                        # 10s delay between query executions to respect anti-ban pacing
                        await asyncio.sleep(10) 
                        
                        found_count = await GlobalMessageSearcher.execute_global_search(q.query_text, limit=40)
                        
                        # Update DB record
                        q.last_run_at = datetime.now(timezone.utc)
                        q.leads_found_count += found_count
                        await session.commit()
                        
                        logger.info(f"✅ GLDE: Query '{q.query_text}' finished. Session total extracted: {found_count} (All time: {q.leads_found_count})")
                        
        except Exception as e:
            logger.error(f"❌ GLDE Scheduler loop error: {e}")
            
        await asyncio.sleep(60) # check every minute
