import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import UserActivityLog, UserProfile, Lead, MonitoredChannel
from src.ai.schemas import LeadScoringResult

logger = logging.getLogger("intent_hunter.ai")

# Concurrency semaphore to throttle concurrent LLM API calls & eliminate peak load bursts
# Set to 1 because all user keys likely share the same Google Project (15-20 RPM limit)
_ai_scoring_semaphore = asyncio.Semaphore(1)

SYSTEM_PROMPT = """# ROLE
You are a high-precision AI agent (Lead Scorer) for the LeadRadar system. Your goal is to accurately classify messages into 4 categories: BUYER, SELLER (B2B Partner), HR_HIRING, and JOB_SEEKER.

# CLASSIFICATION CATEGORIES (CRITICAL)

## 1. BUYER (is_lead: true, is_vendor: false)
A person with REAL intent to BUY, RENT, LEASE, or ORDER a service/product.
- "Сниму квартиру", "Ищу аренду", "Куплю авто", "Нужна виза", "Кто обменяет рубли на USDT?", "Ищу юриста"
- "Looking for rent", "Need transfer", "Where to buy crypto?"

## 2. SELLER / B2B PARTNER (is_lead: false, is_vendor: true)
A business, freelancer, or contractor OFFERING, ADVERTISING, or SELLING their services/products. This is extremely important for B2B targeting.
- "Предлагаем услуги по оформлению виз", "Сдаю виллу", "Обмен валют / крипты по лучшему курсу. Пишите в ЛС"
- "Продам USDT", "Продаю квартиру", "Наша юридическая компания поможет...", "Стоматологические услуги"
- "We offer visa runs", "Currency exchange available", "Company registration services"

## 3. HR_HIRING / VACANCY (is_lead: false, is_vacancy: true)
An employer or company looking to hire staff (offering a job).
- "Ищем сотрудника", "Требуется менеджер", "Открыта вакансия", "Hiring a developer"

## 4. JOB_SEEKER (is_lead: true, is_job_seeker: true, intent_type: "JOB_SEEKING")
A person looking for a job or offering themselves as a candidate.
- "Ищу работу", "Рассмотрю вакансии", "Looking for a job"

## NOISE / TRASH (All flags false)
Chatter, news, greetings, flood without commercial intent.
- "Всем привет", "Какая сегодня погода?", "Спасибо"

# NICHE CLASSIFICATION RULES
1. Base niches: [REAL_ESTATE, LEGAL_SERVICES, VISA_RUN, CAR_RENTAL, BEAUTY, TRANSFER, CLEANING, IT_WEB, FINANCE_CRYPTO, HEALTH, HR_HIRING]
2. If a base niche fits - use it.
3. If not - create a new one (UPPER_SNAKE_CASE, e.g., YACHT_RENTAL, DENTAL_CARE).

# OUTPUT FORMAT
Return STRICTLY valid JSON (no markdown). Fields: is_lead, is_vendor, is_vacancy, is_job_seeker, intent_type, niche, is_new_niche, lead_summary, urgency, estimated_budget, reasoning.

## FEW-SHOT EXAMPLES:
Input: "Snimu kvartiru na mesyac na Dubai Marine"
Output: {"is_lead": true, "is_vendor": false, "is_vacancy": false, "niche": "REAL_ESTATE", "intent_type": "RENT", "lead_summary": "Looking to rent on Dubai Marina", "urgency": "HIGH", "reasoning": "Person is actively looking to rent - BUYER."}

Input: "КУПЛЮ / ПРОДАМ ЮСДТ по хорошему курсу. Личная встреча, расчёт на месте. Пишите в ЛС!"
Output: {"is_lead": false, "is_vendor": true, "is_vacancy": false, "niche": "FINANCE_CRYPTO", "reasoning": "Person/business advertising currency exchange services - SELLER/B2B PARTNER."}

Input: "Куплю ЮСД(трц20) - нал/безнал. Пишите в ЛС!"
Output: {"is_lead": true, "is_vendor": false, "is_vacancy": false, "niche": "FINANCE_CRYPTO", "intent_type": "BUY", "lead_summary": "Wants to buy USDT", "reasoning": "Person wants to buy crypto - BUYER."}

Input: "Оформление виз в ОАЭ, продление тур виз, пишите"
Output: {"is_lead": false, "is_vendor": true, "is_vacancy": false, "niche": "VISA_RUN", "reasoning": "Advertising visa services - SELLER/B2B PARTNER."}

Input: "Требуется бариста в кафе на Марине"
Output: {"is_lead": false, "is_vendor": false, "is_vacancy": true, "niche": "HR_HIRING", "reasoning": "Employer looking for staff - HR_HIRING."}
"""

async def build_dynamic_system_prompt(session: AsyncSession, target_niche: str = None) -> str:
    """
    Builds SYSTEM_PROMPT dynamically using a balanced budget limit:
    3 latest positive exemplars + 3 latest Hard Negatives for the target niche.
    Also injects summarized rules from DynamicNicheRule if available.
    """
    prompt = SYSTEM_PROMPT
    try:
        from src.db.models import AIStudyExemplar, DynamicNicheRule
        
        # 1. Load Summarized Rules
        if target_niche:
            res_rules = await session.execute(select(DynamicNicheRule).where(DynamicNicheRule.niche_code == target_niche))
            rule_entry = res_rules.scalars().first()
            if rule_entry:
                prompt += f"""\n\n### ВАЖНЫЕ ПРАВИЛА ДЛЯ НИШИ {target_niche.upper()}:\n{rule_entry.summarized_rules}\n"""

        # 2. Load Exemplars
        stmt_pos = select(AIStudyExemplar).where(AIStudyExemplar.is_lead == True)
        stmt_neg = select(AIStudyExemplar).where(AIStudyExemplar.is_lead == False)
        
        if target_niche:
            stmt_pos = stmt_pos.where(AIStudyExemplar.niche_code == target_niche)
            stmt_neg = stmt_neg.where(AIStudyExemplar.niche_code == target_niche)
            
        res_pos = await session.execute(stmt_pos.order_by(AIStudyExemplar.created_at.desc()).limit(3))
        pos_exemplars = list(res_pos.scalars().all())

        res_neg = await session.execute(stmt_neg.order_by(AIStudyExemplar.created_at.desc()).limit(3))
        neg_exemplars = list(res_neg.scalars().all())

        all_exemplars = pos_exemplars + neg_exemplars
        all_exemplars.sort(key=lambda x: x.created_at, reverse=True)

        if all_exemplars:
            extra_lines = ["\n\n### 5. DYNAMIC FEW-SHOT EXAMPLES (BALANCED CAP FOR RLHF):"]
            for idx, ex in enumerate(all_exemplars, 1):
                val_check = {
                    "is_author_seeking_service": ex.is_lead,
                    "is_author_offering_service": not ex.is_lead,
                    "is_time_relevant": True
                }
                out_obj = {
                    "reasoning": ex.intent_summary or "Обученный пример из базы знаний.",
                    "validation_check": val_check,
                    "is_lead": ex.is_lead,
                    "niche_code": ex.niche_code,
                    "temperature": ex.temperature,
                    "confidence_score": 0.98 if ex.is_lead else 0.0,
                    "intent_summary": ex.intent_summary,
                    "sales_hook": ex.sales_hook
                }
                type_tag = "[POSITIVE LEAD]" if ex.is_lead else "[HARD NEGATIVE / SPAM]"
                extra_lines.append(f"Learned Example {idx} {type_tag} (Input): [TARGET_USER] User: {json.dumps(ex.raw_message_text, ensure_ascii=False)}")
                extra_lines.append(f"Learned Example {idx} (Output): {json.dumps(out_obj, ensure_ascii=False)}\n")
            
            prompt += "\n".join(extra_lines)
    except Exception as e:
        logger.warning(f"Error appending dynamic /study exemplars: {e}")
    return prompt


def infer_location_code(text: str) -> str:
    combined = (text or "").lower()
    if any(k in combined for k in ["moscow", "москва", "мск", "подмосковье", "руб", "рублей", "сити", "арбат", "тверская"]):
        return "moscow"
    elif any(k in combined for k in ["dubai", "дубай", "оаэ", "uae", "jbr", "marina", "downtown", "jvc", "дирхам", "aed", "creek harbour", "пальм"]):
        return "dubai"
    elif any(k in combined for k in ["nhatrang", "нячанг", "камрань", "cam ranh", "северный пляж", "вьетнам", "vietnam", "дананг", "danang", "фукуок", "муйне", "донги", "vnd"]):
        return "nhatrang"
    elif any(k in combined for k in ["phuket", "пхукет", "таиланд", "thailand", "паттайя", "pattaya", "бангкок", "bangkok", "бат", "thb"]):
        return "phuket"
    elif any(k in combined for k in ["bali", "бали", "индонезия", "indonesia", "рупия", "idr", "убуд", "семиньяк", "чангу", "кута"]):
        return "bali"
    return "global"


async def _determine_message_niche(text: str) -> str:
    """Level 1 Memory Routing: Fast LLM classification of the message niche."""
    try:
        from src.ai.rotator_engine import ai_rotator
        sys_prompt = '''You are a fast router. Determine the niche of the message.
Available niches: real_estate, bike_rent, currency_exchange, legal_services, hr_hiring, marketing_smm, other_b2b, community.
If unsure, return "community".
Output strictly JSON: {"niche_code": "..."}'''
        
        res = await ai_rotator.generate_json(
            system_prompt=sys_prompt,
            user_prompt=text,
            temperature=0.0,
            timeout=5.0
        )
        if res and "niche_code" in res:
            return res["niche_code"]
    except Exception as e:
        logger.warning(f"Error in fast niche routing: {e}")
    return "community"

def build_timeline_string(messages: List[UserActivityLog]) -> str:
    """Helper to format user messages into a readable timeline for AI."""
    lines = []
    for m in messages:
        if m.message_text:
            ts = m.timestamp.strftime('%H:%M:%S') if getattr(m, 'timestamp', None) else ""
            lines.append(f"[{ts}] {m.message_text}")
    return "\n".join(lines)

async def evaluate_user_timeline(
    user_id: int,
    session: AsyncSession,
    messages: Optional[List[UserActivityLog]] = None
) -> Optional[LeadScoringResult]:
    """
    Fetches user's message timeline and calls Groq / Gemini AI to score intent.
    If is_lead is True, saves lead to database and registers dynamic rubrics.
    """
    if messages is None:
        result = await session.execute(
            select(UserActivityLog)
            .where(UserActivityLog.user_id == user_id)
            .order_by(UserActivityLog.timestamp.desc())
            .limit(10)
        )
        messages = list(result.scalars().all())

    if not messages:
        logger.info(f"No messages found for user {user_id}")
        return None

    timeline_str = build_timeline_string(messages)
    latest_msg_text = messages[-1].message_text if messages else ""

    # LEVEL 1 MEMORY ROUTING
    target_niche = await _determine_message_niche(latest_msg_text)

    # Build dynamic prompt with /study exemplars (LEVEL 2 MEMORY)
    active_system_prompt = await build_dynamic_system_prompt(session, target_niche)

    scoring_result: Optional[LeadScoringResult] = None
    from src.ai.rotator_engine import _extract_keys
    has_gemini_key = bool(_extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy"))

    # Acquire concurrency semaphore to ensure maximum 2 parallel LLM API evaluations across all workers
    async with _ai_scoring_semaphore:
        # Key pacing (4.5s) is automatically enforced per key in rotator_engine
        await asyncio.sleep(0.5)

        # ── PRIMARY: AIRotatorEngine Multi-Provider Cascade (SambaNova -> Cerebras -> Groq Pool -> Gemini -> OpenRouter)
        from src.ai.rotator_engine import ai_rotator
        json_schema = LeadScoringResult.model_json_schema()
        sys_p = active_system_prompt or SYSTEM_PROMPT
        
        prompt_sys = (
            f"{sys_p}\n\n"
            f"Please output your evaluation in valid json format adhering strictly to this JSON schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )
        user_p = f"User Messages Timeline:\n{timeline_str}"

        raw_json_dict = await ai_rotator.generate_json(
            system_prompt=prompt_sys,
            user_prompt=user_p,
            temperature=0.1,
            timeout=12.0
        )

        if raw_json_dict:
            try:
                scoring_result = LeadScoringResult(**raw_json_dict)
            except Exception as parse_err:
                logger.warning(f"Error parsing LeadScoringResult from AIRotator Engine dict: {parse_err}")
                scoring_result = None

        # ── ATTEMPT 2: Silent Cooldown Wait & Retry (No Telegram Alert Spam) ─────────────────────
        if scoring_result is None:
            logger.warning(f"⏳ All LLM APIs are cooling or rate-limited for user {user_id}. Retrying after 30s...")
            await asyncio.sleep(30)
            
            raw_json_dict = await ai_rotator.generate_json(
                system_prompt=prompt_sys,
                user_prompt=user_p,
                temperature=0.1,
                timeout=12.0
            )

            if raw_json_dict:
                try:
                    scoring_result = LeadScoringResult(**raw_json_dict)
                except Exception as parse_err:
                    logger.warning(f"Error parsing LeadScoringResult from AIRotator Engine dict on retry: {parse_err}")
                    scoring_result = None

    if scoring_result is None:
        logger.warning(f"Notice: All LLM models temporarily cooling down for user {user_id}. Skipping LLM scoring.")
        return None


    # ── DETERMINISTIC HARD GUARD FOR REAL ESTATE LISTINGS ─────────────────
    if scoring_result:
        raw_text_check = (timeline_str or "").lower()
        prop_listing_patterns = [
            "for sale", "exclusive villa", "villa for sale", "apartment for sale", "flat for sale", "unit for sale",
            "resale unit", "handover in", "plot size", "selling @", "ask - aed",
            "aed 1.", "aed 2.", "aed 3.", "aed 4.", "aed 5.", "aed 6.",
            "продам квартиру", "продам виллу", "продается вилла", "продается квартира", "сдается квартира"
        ]
        buyer_keywords = ["сниму", "ищу", "купим", "хочу купить", "нужен подбор", "looking to buy", "looking for rent", "looking to rent", "want to buy", "want to rent", "need apartment", "need villa"]

        has_listing_pattern = any(p in raw_text_check for p in prop_listing_patterns)
        has_buyer_pattern = any(b in raw_text_check for b in buyer_keywords)

        if has_listing_pattern and not has_buyer_pattern:
            logger.info(f"🚫 HARD GUARD TRIPPED: Real estate sale/rent listing detected for user {user_id}. Forcing is_lead=False & Blacklisting Spammer User.")
            scoring_result.is_lead = False
            scoring_result.is_vendor = True
            try:
                from src.db.models import BlacklistedUser
                ex_b = (await session.execute(select(BlacklistedUser).where(BlacklistedUser.user_id == user_id))).scalar_one_or_none()
                if not ex_b:
                    session.add(BlacklistedUser(user_id=user_id, reason="Авто-черный список: рекламный листинг / спам-бот"))
                    await session.commit()
                # Sync in-memory Gatekeeper set
                import sys
                app_module = sys.modules.get("src.api.app")
                ingestor = getattr(app_module, "ingestor", None) if app_module else None
                if ingestor and hasattr(ingestor, "banned_spammer_user_ids"):
                    ingestor.banned_spammer_user_ids.add(user_id)
            except Exception as blk_u_err:
                logger.debug(f"Notice blacklisting spammer user {user_id}: {blk_u_err}")

    # ── B2B SELLER & HR VACANCY OUTREACH LEAD TRACK ─────────────────────────
    if scoring_result and (scoring_result.is_vendor or scoring_result.is_vacancy):
        # Localize niche for DB
        niche = (scoring_result.niche or "other_b2b").lower().strip()
        invalid_b2b_niches = {"unknown", "none", "", "прочее", "other"}
        conf = 85.0 # Use fixed high confidence for vendors if they passed the LLM check
        
        last_m = messages[-1] if messages else None
        raw_text = getattr(last_m, "message_text", "").lower()
        raw_text_orig = getattr(last_m, "message_text", "")

        prop_keywords = ["for sale", "1bhk", "2bhk", "3bhk", "ask - aed", "aed ", "villa for sale", "handover in", "plot size", "selling @", "exclusive villa", "apartment for sale"]
        is_prop_listing = any(k in raw_text for k in prop_keywords)

        # Multi-Tier location code determination for seller
        seller_loc = "global"
        chat_titles = list(set([getattr(m, "chat_title", "") for m in messages if getattr(m, "chat_title", None)]))
        if chat_titles:
            from src.db.models import MonitoredChannel
            for ct in chat_titles:
                ch_rec = (await session.execute(
                    select(MonitoredChannel).where(MonitoredChannel.title.ilike(f"%{ct}%"))
                )).scalars().first()
                if ch_rec and ch_rec.location_code and ch_rec.location_code != "global":
                    seller_loc = ch_rec.location_code
                    break

        if seller_loc == "global":
            for m in reversed(messages):
                m_loc = getattr(m, "location_code", None)
                if m_loc and m_loc != "global":
                    seller_loc = m_loc
                    break
            if seller_loc == "global":
                for m in messages:
                    ch_title = getattr(m, "chat_title", "") or ""
                    m_txt = getattr(m, "message_text", "") or ""
                    seller_loc = infer_location_code(ch_title + " " + m_txt)
                    if seller_loc != "global":
                        break

        author_uname = getattr(last_m, "username", None)
        author_fname = getattr(last_m, "first_name", None) or f"User_{user_id}"

        if scoring_result.is_vacancy or niche == "hr_hiring" or "вакансия" in raw_text or "ищем сотрудника" in raw_text or "требуется " in raw_text:
            # Route to B2C HR-Radar System!
            try:
                from src.db.models import HRVacancy, UserProfile
                from src.bot.hr_bot import route_new_vacancy

                v_title = (scoring_result.lead_summary or raw_text_orig[:80]).strip()
                # UPSERT UserProfile for B2B Vendor CRM
                p_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
                user_prof = (await session.execute(p_stmt)).scalar_one_or_none()
                if user_prof:
                    user_prof.is_b2b_vendor = True
                    user_prof.vendor_niche = niche
                    user_prof.vendor_quality_score = max(user_prof.vendor_quality_score or 0, 75)
                    user_prof.messages_seen_count = (user_prof.messages_seen_count or 0) + 1
                    user_prof.vendor_sales_hook = v_title
                    await session.commit()

                new_vac = HRVacancy(
                    title=v_title[:250],
                    company_name=author_fname,
                    location_code=seller_loc,
                    niche_code=niche or "hr_hiring",
                    salary_text="По договоренности",
                    description=raw_text_orig,
                    hr_contact=f"@{author_uname}" if author_uname else f"ID: {user_id}",
                    author_username=author_uname,
                    author_telegram_id=user_id,
                    status="PUBLISHED"
                )
                session.add(new_vac)
                await session.commit()
                await session.refresh(new_vac)

                logger.info(f"💼 HR-RADAR B2C Vacancy Created! ID={new_vac.id}, Title='{new_vac.title}'")
                asyncio.create_task(route_new_vacancy(new_vac))
            except Exception as hr_err:
                logger.warning(f"Notice routing HR vacancy: {hr_err}")

        elif niche in invalid_b2b_niches or is_prop_listing:
            logger.info(f"🚫 DISCARDING B2B Seller lead for user {user_id}: niche='{niche}', is_prop={is_prop_listing}.")
        else:
            action = "AUTO_SAVE"
            
            # Build message history array
            history_items = []
            for m in messages:
                history_items.append({
                    "chat_title": getattr(m, "chat_title", "Chat"),
                    "message_text": getattr(m, "message_text", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

            from src.db.models import OutreachLead
            outreach_status = "READY_FOR_OUTREACH"
            
            # Check duplicate / existing B2B lead for this author
            dup_stmt = select(OutreachLead).where(
                (OutreachLead.telegram_id == user_id) |
                (OutreachLead.author_username == author_uname)
            ) if author_uname else select(OutreachLead).where(OutreachLead.telegram_id == user_id)
            existing_outreach = (await session.execute(dup_stmt)).scalars().first()
            
            if existing_outreach:
                # Update existing seller card history
                cur_hist = existing_outreach.messages_history or []
                cur_hist.extend(history_items)
                existing_outreach.messages_history = cur_hist
                existing_outreach.raw_ad_text = raw_text_orig[:500]
                await session.commit()
                logger.info(f"Updated existing B2B SELLER timeline history for @{author_uname} ({len(cur_hist)} messages)")
            else:
                s_hook = f"Предложите поставщику услуг в нише '{niche}' готовый поток целевых клиентов через LeadRadar.win"

                new_outreach = OutreachLead(
                    author_username=author_uname,
                    author_first_name=author_fname,
                    telegram_id=user_id,
                    niche_code=niche,
                    location_code=seller_loc,
                    confidence_score=conf,
                    status=outreach_status,
                    raw_ad_text=raw_text_orig[:500],
                    sales_hook=s_hook,
                    chat_title=getattr(last_m, "chat_title", "Telegram Chat"),
                    messages_history=history_items
                )
                session.add(new_outreach)
                await session.commit()
                await session.refresh(new_outreach)
                
                logger.info(f"🎯 NEW B2B SELLER Lead created! @{author_uname}, GEO: {seller_loc}, Niche: {niche}, Status: {outreach_status}")
                
                # Notify Superadmins
                try:
                    from src.bot.alert_bot import bot, notify_superadmins_system_alert
                    from src.db.models import Partner
                    import html
                    from src.bot.keyboards import get_outreach_approval_keyboard
                    
                    loc_flag = {"dubai": "🇦🇪 Дубай", "nhatrang": "🇻🇳 Вьетнам", "phuket": "🇹🇭 Таиланд"}.get(seller_loc, "🌐 Глобал")
                    card_txt = (
                        f"💼 <b>ОБНАРУЖЕН B2B-ПРОДАВЕЦ (КАНДИДАТ В АУТРИЧ)!</b>\n"
                        f"───────────────────────────\n\n"
                        f"📍 <b>ГЕО:</b> {loc_flag}\n"
                        f"🏷️ <b>Ниша продавца:</b> {niche}\n"
                        f"👤 <b>Автор:</b> @{author_uname or 'без_юзернейма'} ({html.escape(author_fname)})\n"
                        f"💬 <b>Текст предложения:</b> «{html.escape(raw_text_orig[:200])}»\n"
                        f"⚡ <b>Статус:</b> {outreach_status}\n\n"
                        f"ℹ️ <i>Продавец целевых услуг. Будет направлен в авто-аутрич.</i>"
                    )
                    kb = get_outreach_approval_keyboard(new_outreach.id)
                    superadmins_res = await session.execute(select(Partner).where((Partner.role == "SUPERADMIN") | (Partner.role == "ADMIN")))
                    for sa in superadmins_res.scalars().all():
                        try:
                            if bot:
                                await bot.send_message(chat_id=sa.telegram_id, text=card_txt, parse_mode="HTML", reply_markup=kb)
                        except Exception:
                            pass
                except Exception as b2b_alert_err:
                    logger.warning(f"B2B Seller Lead Superadmin notification notice: {b2b_alert_err}")


    if scoring_result and scoring_result.is_lead:
        niche_code_db = (scoring_result.niche or "other").lower().strip()
        logger.info(f"🔥 HOT/WARM Lead detected for user {user_id} in niche {niche_code_db}")
        
        # Multi-Tier Geolocation Determination Hierarchy:
        loc_code = "global"
        for m in reversed(messages):
            m_loc = getattr(m, "location_code", None)
            if m_loc and m_loc != "global":
                loc_code = m_loc
                break

        if loc_code == "global":
            chat_titles = list(set([getattr(m, "chat_title", "") for m in messages if getattr(m, "chat_title", None)]))
            if chat_titles:
                from src.db.models import MonitoredChannel
                for ct in chat_titles:
                    ch_rec = (await session.execute(
                        select(MonitoredChannel).where(MonitoredChannel.title.ilike(f"%{ct}%"))
                    )).scalars().first()
                    if ch_rec and ch_rec.location_code and ch_rec.location_code != "global":
                        loc_code = ch_rec.location_code
                        break

        if loc_code == "global":
            for m in messages:
                ch_name = getattr(m, "chat_title", "") or ""
                msg_txt = getattr(m, "message_text", "") or ""
                inferred = infer_location_code(ch_name + " " + msg_txt)
                if inferred != "global":
                    loc_code = inferred
                    break

        # Lock to protect against concurrent lead creation race conditions
        global _lead_creation_lock
        if '_lead_creation_lock' not in globals():
            _lead_creation_lock = asyncio.Lock()

        async with _lead_creation_lock:
            from src.db.models import Lead
            
            existing_lead_stmt = select(Lead).where(
                Lead.user_id == user_id,
                Lead.niche_code == niche_code_db
            )
            existing_lead = (await session.execute(existing_lead_stmt)).scalars().first()

            if not existing_lead and scoring_result.lead_summary:
                summary_stmt = select(Lead).where(Lead.intent_summary == scoring_result.lead_summary)
                existing_lead = (await session.execute(summary_stmt)).scalars().first()

            if existing_lead:
                logger.info(f"Lead already exists for user {user_id} in niche {niche_code_db} (ID: {existing_lead.id}). Skipping duplicate creation.")
                scoring_result.is_lead = False  # Mark as non-new lead to suppress duplicate alerts
            else:
                client_quote = None
                for m in reversed(messages):
                    if getattr(m, "user_id", None) == user_id:
                        raw_txt = (getattr(m, "message_text", "") or "").strip()
                        if raw_txt:
                            client_quote = raw_txt
                            break

                final_summary = client_quote if (client_quote and len(client_quote.strip()) > 0) else (scoring_result.lead_summary or "").strip()
                scoring_result.lead_summary = final_summary


                # Urgency mapping
                temp_map = {"HIGH": "HOT", "MEDIUM": "WARM", "LOW": "WARM"}
                lead_temp = temp_map.get(str(scoring_result.urgency).upper(), "WARM")
                c_score = 0.98 if lead_temp == "HOT" else 0.85

                # Save lead to Database
                lead = Lead(
                    user_id=user_id,
                    niche_code=niche_code_db,
                    location_code=loc_code,
                    temperature=lead_temp,
                    confidence_score=c_score,
                    intent_summary=final_summary,
                    intent_type=scoring_result.intent_type,
                    estimated_budget=scoring_result.estimated_budget,
                    sales_hook="Требуется обработка (автосгенерировано)",
                    reasoning=scoring_result.reasoning,
                    status="AVAILABLE",
                    price=1.00
                )
                session.add(lead)
                await session.commit()
                await session.refresh(lead)

                # NOTIFY SUBSCRIBERS
                try:
                    from src.bot.alert_bot import notify_subscribers_new_lead
                    import asyncio
                    asyncio.create_task(notify_subscribers_new_lead(lead, session))
                except Exception as notify_err:
                    logger.error(f"Error triggering notify_subscribers_new_lead: {notify_err}")

                try:
                    c_title = (messages[-1].chat_title or "").strip() if messages else ""
                    if c_title:
                        m_ch = (await session.execute(
                            select(MonitoredChannel).where(
                                (MonitoredChannel.title.ilike(c_title)) |
                                (MonitoredChannel.username_or_link.ilike(f"%{c_title}%"))
                            )
                        )).scalars().first()
                        if m_ch:
                            m_ch.leads_count = (m_ch.leads_count or 0) + 1
                            m_ch.last_lead_at = datetime.now(timezone.utc)
                            await session.commit()
                except Exception:
                    pass


        # Dynamic Niche Check
        if scoring_result.is_new_niche:
            from src.db.models import Rubric
            rub_stmt = select(Rubric).where(Rubric.code == niche_code_db)
            existing_rubric = (await session.execute(rub_stmt)).scalar_one_or_none()

            if not existing_rubric:
                new_rub = Rubric(
                    code=niche_code_db,
                    name=scoring_result.niche,
                    icon="🏷️",
                    is_custom=True
                )
                session.add(new_rub)
                await session.commit()
                logger.info(f"✨ Создана новая динамическая ниша: {niche_code_db} ({scoring_result.niche})")


    # Log AI Scorer verdict
    try:
        from src.services.process_logger import process_logger
        if scoring_result:
            if scoring_result.is_lead:
                process_logger.add_log(
                    category="AI_SCORER",
                    level="lead",
                    title=f"🔥 ГОРЯЧИЙ ЛИД ОБНАРУЖЕН! Ниша: {scoring_result.niche}",
                    details=f"Запрос: \"{scoring_result.lead_summary}\" | Бюджет: {scoring_result.estimated_budget or 'Не указан'}"
                )
            elif scoring_result.is_vendor:
                process_logger.add_log(
                    category="AI_SCORER",
                    level="info",
                    title=f"💼 Найден B2B Вендор (Ниша: {scoring_result.niche})",
                    details=f"Передан в Outreach"
                )
            else:
                reason = scoring_result.reasoning or "Не лид и не вендор"
                process_logger.add_log(
                    category="AI_SCORER",
                    level="noise",
                    title=f"🛑 Квалификация завершена — ШУМ",
                    details=f"Причина: {reason[:150]}"
                )
    except Exception as log_err:
        logger.debug(f"AI Scorer process logger notice: {log_err}")

    # Record AI Evaluation Log
    try:
        from src.db.models import AIEvaluationLog
        last_m = messages[-1] if messages else None
        if last_m and scoring_result:
            u_name = getattr(last_m, "username", None) or f"user_{user_id}"
            f_name = getattr(last_m, "first_name", None) or f"Пользователь {user_id}"
            cot_reasoning = (scoring_result.reasoning or "").strip() or "Квалификация ИИ завершена."

            eval_log = AIEvaluationLog(
                user_id=user_id,
                username=u_name,
                first_name=f_name,
                chat_title=last_m.chat_title,
                message_text=last_m.message_text,
                is_lead=scoring_result.is_lead,
                reasoning=cot_reasoning,
                niche_code=(scoring_result.niche or "other").lower(),
                temperature=str(scoring_result.urgency),
                confidence_score=0.95 if scoring_result.is_lead else 0.0,
                location_code=getattr(last_m, "location_code", "global") or "global"
            )
            session.add(eval_log)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to record AIEvaluationLog: {e}")

    return scoring_result


import re

def clean_json_text(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

_groq_key_cooldowns = {}

async def _eval_with_groq(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Groq Cloud API with 45-second key cooldown management and multi-model fallback chain."""
    global _groq_key_cooldowns
    try:
        from groq import AsyncGroq
        import time
        
        # Build API Keys Pool from GROQ_API_KEYS and GROQ_API_KEY
        raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
        key_pool = [k.strip() for k in re.split(r'[,\s\n]+', raw_keys) if k.strip().startswith("gsk_")]
        key_pool = list(dict.fromkeys(key_pool)) # Unique keys list

        if not key_pool:
            logger.warning("No valid Groq API keys starting with 'gsk_' found in settings.")
            return None

        now = time.time()
        # Filter keys not currently on cooldown
        ready_keys = [k for k in key_pool if _groq_key_cooldowns.get(k, 0) <= now]
        if not ready_keys:
            min_cooldown_end = min(_groq_key_cooldowns.values()) if _groq_key_cooldowns else now + 30
            wait_s = max(1.0, min_cooldown_end - now)
            if wait_s <= 30.0:
                logger.info(f"⏳ All {len(key_pool)} Groq API keys are on 30s cooldown. Waiting {wait_s:.1f}s for reset...")
                await asyncio.sleep(wait_s)
                _groq_key_cooldowns.clear()
                ready_keys = key_pool
            else:
                _groq_key_cooldowns.clear()
                ready_keys = key_pool

        json_schema = LeadScoringResult.model_json_schema()
        sys_p = active_prompt or SYSTEM_PROMPT
        
        prompt_sys = (
            f"{sys_p}\n\n"
            f"Please output your evaluation in valid json format adhering strictly to this JSON schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )

        candidate_models = []
        official_models = [
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
            "groq/compound",
            "groq/compound-mini"
        ]
        if settings.GROQ_MODEL and settings.GROQ_MODEL in official_models:
            candidate_models.append(settings.GROQ_MODEL)
        for m in official_models:
            if m not in candidate_models:
                candidate_models.append(m)

        for api_key in ready_keys:
            key_suffix = api_key[-4:]
            client = AsyncGroq(api_key=api_key, max_retries=0, timeout=10.0)

            for model_name in candidate_models:
                try:
                    completion = await client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": prompt_sys},
                            {"role": "user", "content": f"User Messages Timeline:\n{timeline_str}"}
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1
                    )
                    
                    content = completion.choices[0].message.content
                    if content:
                        cleaned = clean_json_text(content)
                        logger.info(f"Successfully evaluated intent via Groq Key (...{key_suffix}) Model ({model_name})")
                        _groq_key_cooldowns.pop(api_key, None)
                        return LeadScoringResult(**json.loads(cleaned))
                except Exception as model_err:
                    err_str = str(model_err)
                    is_rate_limit = (getattr(model_err, "status_code", None) == 429) or ("rate_limit_exceeded" in err_str.lower())
                    
                    if is_rate_limit:
                        logger.warning(f"⚠️ Groq API Rate Limit (429) on Key ...{key_suffix} / Model {model_name}. Setting 5m cooldown...")
                        _groq_key_cooldowns[api_key] = time.time() + 300.0
                        from src.ai.budget_guard import ai_budget_guard
                        asyncio.create_task(ai_budget_guard.record_429_error("Groq", key_suffix))
                        continue
                    else:
                        logger.warning(f"Groq model {model_name} on Key ...{key_suffix} notice: {err_str[:120]}. Trying next model...")

    except Exception as e:
        logger.error(f"Error in Groq Multi-Key Pool evaluation: {e}")
    
    return None


_gemini_cooldown_until = 0.0

async def _eval_with_gemini(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Google Gemini API (via google-genai SDK or httpx REST)."""
    global _gemini_cooldown_until
    if time.time() < _gemini_cooldown_until:
        logger.debug(f"⏳ Gemini API is on 5-minute cooldown ({int(_gemini_cooldown_until - time.time())}s remaining). Bypassing Gemini call.")
        return None

    from src.ai.rotator_engine import _extract_keys
    from src.ai.budget_guard import ai_budget_guard
    
    gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    if not gemini_keys:
        return None
    sys_p = active_prompt or SYSTEM_PROMPT
    prompt = f"{sys_p}\n\nUser Messages Timeline:\n{timeline_str}"

    # 1. Try official google-genai SDK across available keys
    try:
        from google import genai
        from google.genai import types

        for key in gemini_keys:
            key_sfx = key[-4:] if len(key) >= 4 else key
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=LeadScoringResult,
                        temperature=0.1
                    ),
                )
                if response and response.text:
                    cleaned = clean_json_text(response.text)
                    logger.info(f"Successfully evaluated intent via Gemini SDK ({settings.GEMINI_MODEL}) Key=...{key_sfx}")
                    return LeadScoringResult(**json.loads(cleaned))
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower() or "resource" in err_str.lower():
                    logger.info(f"⏳ Gemini SDK Key ...{key_sfx} hit rate limit (429). Setting 5m cooldown...")
                    _gemini_cooldown_until = time.time() + 300.0
                    asyncio.create_task(ai_budget_guard.record_429_error("Gemini_SDK", key_sfx))
                else:
                    logger.debug(f"Gemini SDK call on key ...{key_sfx} notice: {e}")

    except Exception as e:
        logger.debug(f"Gemini SDK call error: {e}. Trying httpx REST fallback...")

    # 2. Try direct HTTP REST API to Gemini across available keys
    try:
        import httpx
        json_schema = LeadScoringResult.model_json_schema()
        prompt_sys = f"{sys_p}\nRespond ONLY with valid JSON matching:\n{json.dumps(json_schema, ensure_ascii=False)}\n\nTimeline:\n{timeline_str}"
        payload = {
            "contents": [{"parts": [{"text": prompt_sys}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
        }

        for key in gemini_keys:
            key_sfx = key[-4:] if len(key) >= 4 else key
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={key}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(url, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                        cleaned = clean_json_text(text)
                        logger.info(f"Successfully evaluated intent via Gemini REST ({settings.GEMINI_MODEL}) Key=...{key_sfx}")
                        return LeadScoringResult(**json.loads(cleaned))
                    elif res.status_code in (403, 429):
                        _gemini_cooldown_until = time.time() + 300.0
                        asyncio.create_task(ai_budget_guard.record_429_error("Gemini_REST", key_sfx))
                        logger.warning(f"Gemini REST API Key ...{key_sfx} returned HTTP {res.status_code}. Setting 5m cooldown...")
                    else:
                        logger.warning(f"Gemini REST API Key ...{key_sfx} returned HTTP {res.status_code}: {res.text[:100]}")
            except Exception as e:
                logger.error(f"Error calling Gemini REST API on Key ...{key_sfx}: {e}")

    except Exception as e:
        logger.error(f"Error in Gemini REST API fallback: {e}")

    return None





async def extract_stopwords_background(text: str, niche_code: str):
    """Extracts stop words from a false-positive (spam) message using LLM to automatically update the Gatekeeper."""
    try:
        from src.ai.rotator_engine import ai_rotator
        from src.db.session import AsyncSessionLocal
        from src.db.models import DynamicStopword
        
        sys_p = '''Extract 1 to 3 highly specific spam keywords or phrases from the text that indicate it is an ad or spam (e.g. "VPN", "залив", "прокси"). Output JSON: {"keywords": ["word1", "word2"]}'''
        res = await ai_rotator.generate_json(system_prompt=sys_p, user_prompt=text, temperature=0.0, timeout=10.0)
        
        if res and "keywords" in res and isinstance(res["keywords"], list):
            async with AsyncSessionLocal() as session:
                for kw in res["keywords"]:
                    kw_clean = kw.strip().lower()
                    if len(kw_clean) < 3: continue
                    # check if exists
                    existing = await session.execute(select(DynamicStopword).where(DynamicStopword.keyword == kw_clean))
                    if not existing.scalars().first():
                        session.add(DynamicStopword(keyword=kw_clean, niche_code=niche_code))
                await session.commit()
    except Exception as e:
        logger.error(f"Error extracting stopwords: {e}")


async def extract_keywords_and_update_rules_background(message_text: str, category: str, niche_code: str = "community"):
    """
    Background AI learning task invoked on manual reclassification.
    Calls LLM to extract key phrases and generate concise classifier guidelines for category,
    then updates DynamicNicheRule table in DB for online self-learning.
    """
    try:
        from src.db.session import AsyncSessionLocal
        from src.db.models import DynamicNicheRule
        from src.ai.rotator_engine import ai_rotator
        from src.services.process_logger import process_logger

        sys_prompt = f"""Ты — ИИ-методист системы LeadRadar.win.
Администратор вручную переклассифицировал следующее сообщение как категорию: {category}.
Твоя задача — извлечь из сообщения ключевые флективные маркеры (словосочетания, сигнатуры, эмейлы/ссылки) и сформулировать 1-2 лаконичных правила для классификатора ИИ.

Отвечай СТРОГО в формате JSON:
{{
  "keywords": ["маркер1", "маркер2", "маркер3"],
  "rule_summary": "Лаконичное правило (1-2 предложения), как квалифицировать сообщения категории {category}."
}}"""

        res_dict = await ai_rotator.generate_json(
            system_prompt=sys_prompt,
            user_prompt=message_text,
            temperature=0.1,
            timeout=10.0
        )

        if not res_dict or "rule_summary" not in res_dict:
            return

        kw_list = res_dict.get("keywords", [])
        new_rule = res_dict.get("rule_summary", "").strip()
        target_niche = niche_code or "community"

        async with AsyncSessionLocal() as session:
            stmt = select(DynamicNicheRule).where(DynamicNicheRule.niche_code == target_niche)
            existing_rule = (await session.execute(stmt)).scalar_one_or_none()

            kw_str = ", ".join(kw_list) if isinstance(kw_list, list) else str(kw_list)
            rule_text = f"• [Маркеры {category}]: {kw_str}. {new_rule}"

            if existing_rule:
                cur_text = existing_rule.summarized_rules or ""
                lines = [l.strip() for l in cur_text.split("\n") if l.strip()]
                lines.insert(0, rule_text)
                existing_rule.summarized_rules = "\n".join(lines[:5])
                existing_rule.last_updated_at = datetime.now(timezone.utc)
            else:
                session.add(DynamicNicheRule(
                    niche_code=target_niche,
                    summarized_rules=rule_text
                ))
            await session.commit()

            try:
                process_logger.add_log(
                    category="AI_SCORER",
                    level="success",
                    title=f"⚡ САМООБУЧЕНИЕ ИИ: Сообщение изучено для ниши [{target_niche.upper()}] ({category})",
                    details=f"Извлечены ключевые маркеры: {kw_str} | Правило: {new_rule}"
                )
            except Exception:
                pass

            logger.info(f"✅ AI Self-Learning Engine: Updated DynamicNicheRule for {target_niche} ({category}). Keywords: {kw_str}")
    except Exception as e:
        logger.warning(f"Error in background AI self-learning task: {e}")

