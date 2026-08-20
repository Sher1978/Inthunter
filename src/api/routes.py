from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from pydantic import BaseModel, Field
from src.db.models import UserProfile, UserActivityLog, Lead, Partner, LeadPurchase, MonitoredChannel, Rubric
from src.bot.keyboards import NICHE_NAMES, register_dynamic_rubric

router = APIRouter()

class AddChannelSchema(BaseModel):
    username_or_link: str = Field(..., example="@auto_moscow_chat")
    niche_code: str = Field(default="auto_kasko", example="auto_kasko")
    location_code: Optional[str] = Field(default=None, example="nhatrang")
    title: str = Field(default=None, example="Чат Автомобилистов Москвы")
    chat_type: str = Field(default="channel", example="group")

class GrokSearchSchema(BaseModel):
    keywords: str = Field(..., example="нячанг аренда жилья")
    niche_code: str = Field(default="general", example="real_estate")

class GrokChatMessageSchema(BaseModel):
    role: str = Field(..., example="user")
    content: str = Field(..., example="Найди группы с арендой жилья")

class GrokChatRequestSchema(BaseModel):
    user_input: str = Field(..., example="Ищи чаты в Нячанге")
    history: list = Field(default=[], example=[])
    niche_code: str = Field(default="general", example="real_estate")

class AddRubricSchema(BaseModel):
    code: str = Field(..., example="legal_services")
    name: str = Field(..., example="⚖️ Юридические услуги")
    icon: str = Field(default="🏷️", example="⚖️")

class UpdateRubricSchema(BaseModel):
    name: str = Field(..., example="⚖️ Юридические консультации")
    icon: str = Field(default="🏷️", example="⚖️")

class UpdateChannelSchema(BaseModel):
    location_code: Optional[str] = None
    niche_code: Optional[str] = None

class VerifyPasscodeSchema(BaseModel):
    passcode: str = Field(..., example="260669")

@router.post("/auth/verify-passcode")
async def verify_admin_passcode(data: VerifyPasscodeSchema):
    if data.passcode.strip() == settings.ADMIN_PASSCODE:
        return {"status": "ok", "message": "Авторизация успешна"}
    return {"status": "error", "message": "Неверный пароль администратора"}

@router.post("/grok/search-channels")
async def grok_search_channels(data: GrokSearchSchema):
    from src.ai.grok_channel_finder import GrokChannelFinder
    finder = GrokChannelFinder()
    candidates = await finder.search_channels_and_groups(keywords=data.keywords, niche_code=data.niche_code, limit=8)
    return {"status": "ok", "keywords": data.keywords, "candidates": candidates}

@router.post("/grok/chat")
async def grok_proactive_chat(data: GrokChatRequestSchema):
    from src.ai.grok_channel_finder import GrokChannelFinder
    finder = GrokChannelFinder()
    res = await finder.proactive_chat_dialog(
        messages_history=data.history,
        user_input=data.user_input,
        niche_code=data.niche_code
    )
    return {"status": "ok", "response": res}

@router.get("/channels")
async def list_monitored_channels(
    location: str = None,
    niche: str = None,
    query: str = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc())
    if niche and niche != "all":
        stmt = stmt.where(MonitoredChannel.niche_code == niche)
    
    if location and location != "all":
        stmt = stmt.where(MonitoredChannel.location_code == location)

    res = await db.execute(stmt)
    channels = list(res.scalars().all())

    if query:
        q_clean = query.strip().lower()
        channels = [
            c for c in channels
            if q_clean in (c.title or "").lower() or q_clean in c.username_or_link.lower()
        ]

    return [
        {
            "id": c.id,
            "title": c.title,
            "username_or_link": c.username_or_link,
            "niche_code": c.niche_code,
            "location_code": getattr(c, "location_code", "nhatrang") or "nhatrang",
            "chat_type": getattr(c, "chat_type", "channel") or "channel",
            "status": c.status,
            "error_message": c.error_message,
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in channels
    ]

@router.post("/channels")
async def add_monitored_channel(data: AddChannelSchema, db: AsyncSession = Depends(get_db)):
    raw_target = data.username_or_link.strip()

    # Check for internal client /c/ links
    if "/c/" in raw_target:
        return {
            "status": "error",
            "message": "Ссылка формата /c/3493020432 является внутренней ссылкой веб-клиента. Укажите публичный юзернейм (@username) или инвайт-ссылку (https://t.me/+...)."
        }

    if "/+" in raw_target or "joinchat/" in raw_target:
        canonical_target = raw_target
        clean_user = raw_target.split("/")[-1].replace("+", "").strip()
    else:
        clean_user = raw_target.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split("/")[0].strip()
        if not clean_user or len(clean_user) < 2:
            return {"status": "error", "message": "Укажите корректный юзернейм или ссылку на Telegram канал."}
        canonical_target = f"@{clean_user}"

    # Check if exists by username or raw link
    stmt = select(MonitoredChannel).where(
        (MonitoredChannel.username_or_link.ilike(f"%{clean_user}%"))
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    
    if existing:
        return {
            "status": "exists",
            "message": f"Чат или канал {canonical_target} уже есть в списке прослушки!",
            "channel_id": existing.id,
            "channel_status": existing.status
        }

    # Infer location code if not specified
    loc_code = data.location_code
    if not loc_code or loc_code == "all":
        u_low = clean_user.lower()
        if "danang" in u_low or "дананг" in u_low:
            loc_code = "danang"
        elif "dubai" in u_low or "дубай" in u_low:
            loc_code = "dubai"
        elif "phuket" in u_low or "пхукет" in u_low:
            loc_code = "phuket"
        elif "bali" in u_low or "бали" in u_low:
            loc_code = "bali"
        elif "tbilisi" in u_low or "тбилиси" in u_low:
            loc_code = "tbilisi"
        elif "nhatrang" in u_low or "нячанг" in u_low:
            loc_code = "nhatrang"
        else:
            loc_code = "global"

    channel = MonitoredChannel(
        username_or_link=canonical_target,
        title=data.title or canonical_target,
        niche_code=data.niche_code,
        location_code=loc_code,
        chat_type=data.chat_type,
        status="PENDING"
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    # Attempt dynamic auto-join via global ingestor if running
    from src.api.app import ingestor
    if ingestor:
        success, title, error = await ingestor.join_channel(canonical_target)
        if success:
            channel.status = "JOINED"
            if title:
                channel.title = title
            channel.error_message = None
        else:
            channel.status = "FAILED"
            channel.error_message = error
        await db.commit()
        await db.refresh(channel)

    return {
        "status": "added",
        "message": f"Канал {canonical_target} успешно добавлен!",
        "channel_id": channel.id,
        "channel_status": channel.status,
        "title": channel.title,
        "error": channel.error_message
    }

@router.delete("/channels/{channel_id}")
async def delete_monitored_channel(channel_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
    channel = (await db.execute(stmt)).scalar_one_or_none()
    if not channel:
        return {"status": "error", "message": "Channel not found"}
    
    ch_title = channel.title
    clean_user = channel.username_or_link.replace("@", "").replace("https://t.me/", "")

    # Delete non-lead activity logs associated with this channel
    from sqlalchemy import delete
    if ch_title:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{ch_title}%")))
    if clean_user:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{clean_user}%")))

    await db.delete(channel)
    await db.commit()
    return {"status": "deleted", "channel_id": channel_id}

@router.patch("/channels/{channel_id}")
@router.put("/channels/{channel_id}")
async def update_monitored_channel(channel_id: str, data: UpdateChannelSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
    channel = (await db.execute(stmt)).scalar_one_or_none()
    if not channel:
        return {"status": "error", "message": "Channel not found"}
    
    if data.location_code is not None:
        channel.location_code = data.location_code
    if data.niche_code is not None:
        channel.niche_code = data.niche_code
        
    await db.commit()
    await db.refresh(channel)
    return {
        "status": "updated",
        "channel_id": channel.id,
        "location_code": channel.location_code,
        "niche_code": channel.niche_code
    }

@router.get("/live-stream")
async def get_live_activity_stream(limit: int = 35, db: AsyncSession = Depends(get_db)):
    """Returns recent activity logs for live-stream userbot parsing monitor."""
    stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(limit)
    res = await db.execute(stmt)
    logs = list(res.scalars().all())

    ch_stmt = select(MonitoredChannel)
    ch_res = await db.execute(ch_stmt)
    channels = list(ch_res.scalars().all())
    ch_map = {c.title: c.username_or_link for c in channels if c.title}

    items = []
    for log in logs:
        # Check if author had qualified lead
        lead_stmt = select(Lead).where(Lead.user_id == log.user_id).order_by(Lead.created_at.desc()).limit(1)
        lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()

        tg_link = ch_map.get(log.chat_title, None)
        if not tg_link and log.chat_title and log.chat_title.startswith("@"):
            tg_link = log.chat_title

        items.append({
            "id": log.id,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "time_str": log.timestamp.strftime("%H:%M:%S") if log.timestamp else "",
            "chat_title": log.chat_title or "Групповой чат",
            "channel_link": tg_link,
            "user_id": log.user_id,
            "message_text": log.message_text,
            "is_lead": lead_obj is not None,
            "niche_code": lead_obj.niche_code if lead_obj else None,
            "temperature": lead_obj.temperature if lead_obj else None
        })

    return items

@router.get("/rubrics")
async def list_rubrics(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Rubric).order_by(Rubric.name.asc()))
    db_rubrics = list(res.scalars().all())
    db_dict = {r.code: {"code": r.code, "name": r.name, "icon": r.icon, "is_custom": r.is_custom} for r in db_rubrics}

    # Merge with memory NICHE_NAMES
    all_items = []
    seen_codes = set()

    for code, name in NICHE_NAMES.items():
        seen_codes.add(code)
        if code in db_dict:
            all_items.append(db_dict[code])
        else:
            all_items.append({"code": code, "name": name, "icon": "🏷️", "is_custom": False})

    for code, item in db_dict.items():
        if code not in seen_codes:
            all_items.append(item)

    return all_items

@router.post("/rubrics")
async def create_rubric(data: AddRubricSchema, db: AsyncSession = Depends(get_db)):
    code_clean = data.code.strip().lower().replace(" ", "_")
    stmt = select(Rubric).where(Rubric.code == code_clean)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return {"status": "exists", "rubric": existing.code}

    rub = Rubric(code=code_clean, name=data.name.strip(), icon=data.icon, is_custom=True)
    db.add(rub)
    await db.commit()
    
    register_dynamic_rubric(code_clean, data.name.strip())
    return {"status": "created", "code": code_clean, "name": data.name}

@router.put("/rubrics/{code}")
async def update_rubric(code: str, data: UpdateRubricSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Rubric).where(Rubric.code == code)
    rub = (await db.execute(stmt)).scalar_one_or_none()
    
    if not rub:
        rub = Rubric(code=code, name=data.name.strip(), icon=data.icon, is_custom=True)
        db.add(rub)
    else:
        rub.name = data.name.strip()
        rub.icon = data.icon

    await db.commit()
    register_dynamic_rubric(code, data.name.strip())
    return {"status": "updated", "code": code, "name": data.name}

@router.delete("/rubrics/{code}")
async def delete_rubric(code: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Rubric).where(Rubric.code == code)
    rub = (await db.execute(stmt)).scalar_one_or_none()
    if rub:
        await db.delete(rub)
        await db.commit()

    if code in NICHE_NAMES:
        del NICHE_NAMES[code]

    return {"status": "deleted", "code": code}

@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "Intent Hunter CDP API"}

@router.get("/stats")
async def get_platform_stats(db: AsyncSession = Depends(get_db)):
    users_count = (await db.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
    logs_count = (await db.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
    leads_count = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
    sold_leads_count = (await db.execute(select(func.count(Lead.id)).where(Lead.status == "SOLD"))).scalar() or 0
    partners_count = (await db.execute(select(func.count(Partner.id)))).scalar() or 0
    channels_count = (await db.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0

    return {
        "user_profiles": users_count,
        "activity_logs": logs_count,
        "total_leads": leads_count,
        "sold_leads": sold_leads_count,
        "b2b_partners": partners_count,
        "monitored_channels": channels_count
    }

@router.get("/leads")
async def list_leads(niche: str = None, limit: int = 50, db: AsyncSession = Depends(get_db)):
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit)
    if niche and niche != "all":
        stmt = stmt.where(Lead.niche_code == niche)
    
    res = await db.execute(stmt)
    leads = list(res.scalars().all())
    return [
        {
            "id": l.id,
            "user_id": l.user_id,
            "niche_code": l.niche_code,
            "rubric_name": NICHE_NAMES.get(l.niche_code, "Прочее"),
            "temperature": l.temperature,
            "confidence_score": l.confidence_score,
            "intent_summary": l.intent_summary,
            "sales_hook": l.sales_hook,
            "status": l.status,
            "price": float(l.price),
            "created_at": l.created_at.isoformat() if l.created_at else None
        }
        for l in leads
    ]

class UpdatePartnerPrioritySchema(BaseModel):
    niche_code: str = Field(..., example="auto_kasko")
    priority: int = Field(..., example=1) # 1=VIP 0s, 2=High 30s, 3=Standard 60s

@router.get("/partners")
async def list_partners(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Partner).order_by(Partner.created_at.desc()))
    partners = list(res.scalars().all())
    
    partner_list = []
    for p in partners:
        # Fetch detailed purchases for this partner with timestamp
        p_stmt = select(LeadPurchase, Lead).join(Lead, LeadPurchase.lead_id == Lead.id).where(LeadPurchase.partner_id == p.id).order_by(LeadPurchase.purchased_at.desc())
        p_res = await db.execute(p_stmt)
        purchases_data = []
        total_spent = 0.0

        for pur, lead_obj in p_res.all():
            price_val = float(pur.price_paid)
            total_spent += price_val
            purchases_data.append({
                "purchase_id": pur.id,
                "lead_id": pur.lead_id,
                "price_paid": price_val,
                "purchased_at": pur.purchased_at.isoformat() if pur.purchased_at else None,
                "purchased_at_fmt": pur.purchased_at.strftime("%Y-%m-%d %H:%M:%S") if pur.purchased_at else "",
                "niche_code": lead_obj.niche_code,
                "rubric_name": NICHE_NAMES.get(lead_obj.niche_code, "Прочее"),
                "intent_summary": lead_obj.intent_summary
            })

        partner_list.append({
            "id": p.id,
            "telegram_id": p.telegram_id,
            "company_name": p.company_name,
            "role": p.role,
            "moderation_status": p.moderation_status,
            "balance": float(p.balance),
            "subscribed_niches": p.subscribed_niches,
            "niche_priorities": p.niche_priorities or {},
            "total_purchases_count": len(purchases_data),
            "total_spent": total_spent,
            "purchases": purchases_data,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "created_at_fmt": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else ""
        })

    return partner_list

@router.put("/partners/{partner_id}/priority")
async def update_partner_priority(partner_id: str, data: UpdatePartnerPrioritySchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Partner not found"}

    priorities = dict(partner.niche_priorities or {})
    priorities[data.niche_code] = data.priority
    partner.niche_priorities = priorities

    await db.commit()
    await db.refresh(partner)

    return {
        "status": "updated",
        "partner_id": partner.id,
        "niche_priorities": partner.niche_priorities
    }

class BuyLeadSchema(BaseModel):
    telegram_id: int = Field(..., example=8866001783)

@router.post("/leads/{lead_id}/buy")
async def buy_lead_api(lead_id: str, data: BuyLeadSchema, db: AsyncSession = Depends(get_db)):
    lead_stmt = select(Lead).where(Lead.id == lead_id)
    lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()
    if not lead_obj:
        return {"status": "error", "message": "Лид не найден"}
    if lead_obj.status == "SOLD":
        return {"status": "error", "message": "Этот лид уже выкуплен другим партнером"}

    partner_stmt = select(Partner).where(Partner.telegram_id == data.telegram_id)
    partner = (await db.execute(partner_stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Партнер не найден"}

    price = float(lead_obj.price or 1.00)
    if float(partner.balance) < price:
        return {"status": "error", "message": f"Недостаточно средств на балансе. Требуется: ${price:.2f} USD"}

    partner.balance = float(partner.balance) - price
    lead_obj.status = "SOLD"

    purchase = LeadPurchase(
        lead_id=lead_obj.id,
        partner_id=partner.id,
        price_paid=price
    )
    db.add(purchase)
    await db.commit()
    await db.refresh(purchase)

    from src.services.referral_engine import process_lead_purchase_referral_accrual
    await process_lead_purchase_referral_accrual(purchase.id, db)

    return {
        "status": "ok",
        "message": "Лид успешно выкуплен!",
        "purchase_id": purchase.id,
        "new_balance": float(partner.balance)
    }

class ReferralWithdrawRequestSchema(BaseModel):
    telegram_id: int = Field(..., example=8866001783)
    payment_details: str = Field(..., example="USDT TRC20 TKhg9...")

@router.get("/referrals/stats")
async def get_referral_stats(telegram_id: int = 8866001783, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(Partner.telegram_id == telegram_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        stmt = select(Partner).where(Partner.role == "SUPERADMIN")
        partner = (await db.execute(stmt)).scalars().first()

    if not partner:
        return {"status": "error", "message": "Partner not found"}

    ref_count_stmt = select(func.count(Partner.id)).where(Partner.referred_by_id == partner.id)
    invited_count = (await db.execute(ref_count_stmt)).scalar() or 0

    ref_link = f"https://t.me/intenthunter_bot?start=ref_{partner.telegram_id}"
    from src.services.referral_engine import generate_referral_qr_base64
    qr_b64 = generate_referral_qr_base64(ref_link)

    from src.db.models import ReferralAccrual
    acc_stmt = select(ReferralAccrual).where(ReferralAccrual.referrer_id == partner.id).order_by(ReferralAccrual.created_at.desc())
    acc_res = await db.execute(acc_stmt)
    accruals = list(acc_res.scalars().all())

    accruals_data = [
        {
            "id": a.id,
            "payment_amount": float(a.payment_amount),
            "accrual_amount": float(a.accrual_amount),
            "created_at_fmt": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else ""
        }
        for a in accruals
    ]

    return {
        "partner_id": partner.id,
        "telegram_id": partner.telegram_id,
        "referral_link": ref_link,
        "qr_code_base64": qr_b64,
        "invited_count": invited_count,
        "referral_balance": float(partner.referral_balance or 0.0),
        "total_referral_earned": float(partner.total_referral_earned or 0.0),
        "can_withdraw": float(partner.referral_balance or 0.0) >= 50.0,
        "accruals": accruals_data
    }

@router.post("/referrals/withdraw")
async def create_referral_withdrawal(data: ReferralWithdrawRequestSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(Partner.telegram_id == data.telegram_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Пользователь не найден"}

    bal = float(partner.referral_balance or 0.0)
    if bal < 50.0:
        return {"status": "error", "message": f"Минимальная сумма вывода составляет $50.00 USD. Ваш текущий реферальный баланс: ${bal:.2f} USD"}

    details = data.payment_details.strip()
    if not details:
        return {"status": "error", "message": "Укажите реквизиты для получения вывода (USDT / TON / Карта)"}

    from src.db.models import WithdrawalRequest
    req = WithdrawalRequest(
        partner_id=partner.id,
        amount=bal,
        payment_details=details,
        status="PENDING"
    )
    db.add(req)
    partner.referral_balance = 0.00
    await db.commit()

    # Notify Superadmins
    sa_stmt = select(Partner).where(Partner.role == "SUPERADMIN")
    superadmins = list((await db.execute(sa_stmt)).scalars().all())
    from src.bot.alert_bot import bot
    if bot:
        admin_msg = (
            f"🚨 <b>НОВАЯ ЗАЯВКА НА ВЫВОД РЕФЕРАЛЬНЫХ НАЧИСЛЕНИЙ (WEB)!</b>\n\n"
            f"<b>Партнер:</b> {partner.company_name}\n"
            f"<b>Telegram ID:</b> <code>{partner.telegram_id}</code>\n"
            f"<b>Сумма к выплате:</b> <b>${bal:.2f} USD</b>\n"
            f"<b>Реквизиты:</b> <code>{details}</code>"
        )
        for sa in superadmins:
            try:
                await bot.send_message(sa.telegram_id, admin_msg, parse_mode="HTML")
            except Exception as e:
                pass

    return {
        "status": "ok",
        "message": f"Заявка на вывод ${bal:.2f} USD успешно создана!",
        "amount": bal
    }

class QualifyManualSchema(BaseModel):
    message_text: str = Field(..., example="Нужен байк на месяц в Нячанге")
    chat_title: str = Field(default="Пользовательский чат")
    user_id: Optional[int] = None
    username: Optional[str] = None
    niche_code: Optional[str] = None

@router.post("/leads/qualify-manual")
async def qualify_lead_manually(data: QualifyManualSchema, db: AsyncSession = Depends(get_db)):
    from src.ai.scorer import analyze_message
    res = await analyze_message(data.message_text, data.chat_title)

    user_id = data.user_id or (700000 + abs(hash(data.message_text)) % 200000)

    u_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    up = (await db.execute(u_stmt)).scalar_one_or_none()
    if not up:
        up = UserProfile(
            user_id=user_id,
            username=data.username or "telegram_user",
            first_name="Пользователь Telegram",
            behavior_summary="Клиент с ручной квалификацией лида"
        )
        db.add(up)
        await db.flush()

    final_niche = data.niche_code or (res.niche_code if res and res.is_lead else "services_visa")
    final_summary = res.intent_summary if res and res.is_lead else data.message_text[:120]
    final_hook = res.sales_hook if res and res.is_lead else "Горячий покупательский запрос из чата"

    lead = Lead(
        user_id=user_id,
        niche_code=final_niche,
        temperature="HOT",
        confidence_score=0.98,
        intent_summary=final_summary,
        sales_hook=final_hook,
        status="AVAILABLE",
        price=1.00
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    return {
        "status": "ok",
        "message": "Лид успешно квалифицирован и помещен в Маркетплейс!",
        "lead_id": lead.id,
        "niche_code": lead.niche_code,
        "intent_summary": lead.intent_summary
    }
