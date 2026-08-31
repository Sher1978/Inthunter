routes_path = 'src/api/routes.py'

new_routes = """

# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# VACANCY GROUP AUTO-POSTING MANAGEMENT ROUTES
# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

class VacancyGroupTargetCreateSchema(BaseModel):
    group_username: str
    group_title: str = ""
    stars_price: int = 50
    interval_hours: int = 48
    max_reposts: int = 3

@router.get("/vacancy-groups")
async def list_vacancy_groups(db: AsyncSession = Depends(get_db)):
    from src.db.models import VacancyGroupTarget, VacancyGroupPost, VacancyContactPurchase
    groups = list((await db.execute(select(VacancyGroupTarget).order_by(VacancyGroupTarget.id))).scalars().all())
    result = []
    for g in groups:
        post_count = (await db.execute(select(func.count(VacancyGroupPost.id)).where(VacancyGroupPost.group_username == g.group_username))).scalar() or 0
        stars_earned = (await db.execute(select(func.sum(VacancyContactPurchase.stars_paid)).where(VacancyContactPurchase.group_source.ilike(f"%{g.group_username.lstrip('@')}%")))).scalar() or 0
        purchases = (await db.execute(select(func.count(VacancyContactPurchase.id)).where(VacancyContactPurchase.group_source.ilike(f"%{g.group_username.lstrip('@')}%")))).scalar() or 0
        result.append({"id": g.id, "group_username": g.group_username, "group_title": g.group_title, "stars_price": g.stars_price, "is_active": g.is_active, "interval_hours": g.interval_hours, "max_reposts": g.max_reposts, "posts_total": post_count, "purchases_total": purchases, "stars_earned": int(stars_earned), "last_posted_at": g.last_posted_at.isoformat() if g.last_posted_at else None, "created_at": g.created_at.isoformat() if g.created_at else None})
    return {"status": "ok", "groups": result}

@router.post("/vacancy-groups")
async def create_vacancy_group(data: VacancyGroupTargetCreateSchema, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyGroupTarget
    uname = data.group_username if data.group_username.startswith("@") else f"@{data.group_username}"
    existing = (await db.execute(select(VacancyGroupTarget).where(VacancyGroupTarget.group_username == uname))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Group {uname} already exists")
    grp = VacancyGroupTarget(group_username=uname, group_title=data.group_title or uname, stars_price=data.stars_price, interval_hours=data.interval_hours, max_reposts=data.max_reposts, is_active=True)
    db.add(grp)
    await db.commit()
    await db.refresh(grp)
    return {"status": "created", "id": grp.id, "group_username": grp.group_username}

@router.patch("/vacancy-groups/{group_id}/toggle")
async def toggle_vacancy_group(group_id: int, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyGroupTarget
    grp = (await db.execute(select(VacancyGroupTarget).where(VacancyGroupTarget.id == group_id))).scalar_one_or_none()
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    grp.is_active = not grp.is_active
    await db.commit()
    return {"status": "ok", "group_username": grp.group_username, "is_active": grp.is_active}

@router.delete("/vacancy-groups/{group_id}")
async def delete_vacancy_group(group_id: int, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyGroupTarget
    grp = (await db.execute(select(VacancyGroupTarget).where(VacancyGroupTarget.id == group_id))).scalar_one_or_none()
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.delete(grp)
    await db.commit()
    return {"status": "deleted"}

@router.get("/vacancy-groups/purchases")
async def list_contact_purchases(db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyContactPurchase
    rows = list((await db.execute(select(VacancyContactPurchase).order_by(VacancyContactPurchase.purchased_at.desc()).limit(100))).scalars().all())
    total_stars = sum(r.stars_paid for r in rows)
    return {"status": "ok", "total_purchases": len(rows), "total_stars_earned": total_stars, "purchases": [{"id": r.id, "vacancy_id": r.vacancy_id, "buyer_telegram_id": r.buyer_telegram_id, "buyer_username": r.buyer_username, "stars_paid": r.stars_paid, "group_source": r.group_source, "purchased_at": r.purchased_at.isoformat()} for r in rows]}
"""

with open(routes_path, 'a', encoding='utf-8') as f:
    f.write(new_routes)

lines = len(open(routes_path, encoding='utf-8').readlines())
print(f'Done. Total lines: {lines}')
