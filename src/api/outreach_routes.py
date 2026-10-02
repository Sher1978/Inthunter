from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pydantic import BaseModel
import datetime

from src.db.session import get_db
from src.db.models import OutreachProject, OutreachTask, OutreachTaskAccount, OutreachAccount

outreach_router = APIRouter()

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    knowledge_base: Optional[str] = None
    funnel_stages: Optional[list] = []

class TaskCreate(BaseModel):
    project_id: int
    name: str
    niche_code: Optional[str] = None
    location_code: Optional[str] = None
    persona_prompt: Optional[str] = None
    working_hours_start: Optional[str] = "09:00"
    working_hours_end: Optional[str] = "18:00"
    timezone: Optional[str] = "Asia/Dubai"
    filters: Optional[dict] = {}

class TaskAccountLink(BaseModel):
    account_id: int
    daily_limit: int = 15

@outreach_router.get("/projects")
async def get_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OutreachProject).order_by(OutreachProject.id.desc()))
    return result.scalars().all()

@outreach_router.post("/projects")
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)):
    project = OutreachProject(
        name=data.name,
        description=data.description,
        knowledge_base=data.knowledge_base,
        funnel_stages=data.funnel_stages
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project

@outreach_router.get("/projects/{project_id}/tasks")
async def get_tasks_for_project(project_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OutreachTask).where(OutreachTask.project_id == project_id))
    return result.scalars().all()

@outreach_router.post("/tasks")
async def create_task(data: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = OutreachTask(
        project_id=data.project_id,
        name=data.name,
        niche_code=data.niche_code,
        location_code=data.location_code,
        persona_prompt=data.persona_prompt,
        working_hours_start=data.working_hours_start,
        working_hours_end=data.working_hours_end,
        timezone=data.timezone,
        filters=data.filters
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task

@outreach_router.post("/tasks/{task_id}/accounts")
async def link_account_to_task(task_id: int, data: TaskAccountLink, db: AsyncSession = Depends(get_db)):
    task = await db.get(OutreachTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    link = OutreachTaskAccount(
        task_id=task_id,
        account_id=data.account_id,
        daily_limit=data.daily_limit
    )
    db.add(link)
    await db.commit()
    return {"status": "success", "message": "Account linked to task"}

@outreach_router.get("/tasks/{task_id}/accounts")
async def get_task_accounts(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OutreachTaskAccount).where(OutreachTaskAccount.task_id == task_id))
    return result.scalars().all()
