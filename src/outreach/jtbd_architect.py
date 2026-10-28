import json
import logging
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal
from src.db.models import (
    OutreachProject, B2BProspect, OutreachLead, Lead, OutreachTask
)
from src.ai.rotator_engine import AIRotatorEngine

logger = logging.getLogger("intent_hunter.jtbd_architect")
ai_rotator = AIRotatorEngine()


async def scrape_website_text(url: str, max_chars: int = 10000) -> str:
    """
    Scrapes clean human-readable text from website URL using Playwright (Chromium)
    with graceful HTTP fallback.
    """
    if not url or not url.strip().startswith("http"):
        return ""

    target_url = url.strip()
    text_content = ""

    # 1. Try Playwright Chromium (renders JavaScript, SPAs, Tilda, Webflow)
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            try:
                await page.goto(target_url, timeout=15000, wait_until="domcontentloaded")
                # Wait briefly for dynamic elements
                await page.wait_for_timeout(1000)
                
                # Extract clean inner text from body, ignoring script/style
                text_content = await page.evaluate("""() => {
                    const removeTags = ['script', 'style', 'nav', 'footer', 'noscript', 'svg'];
                    removeTags.forEach(tag => {
                        document.querySelectorAll(tag).forEach(el => el.remove());
                    });
                    return document.body ? document.body.innerText : '';
                }""")
            finally:
                await browser.close()
    except Exception as pw_err:
        logger.warning(f"Playwright scrape notice for {target_url}: {pw_err}. Falling back to httpx...")

    # 2. Fallback to httpx if Playwright didn't return text
    if not text_content or len(text_content.strip()) < 50:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(target_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    clean_html = re.sub(r'<script.*?</script>', '', resp.text, flags=re.DOTALL | re.IGNORECASE)
                    clean_html = re.sub(r'<style.*?</style>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
                    clean_html = re.sub(r'<[^>]+>', ' ', clean_html)
                    text_content = ' '.join(clean_html.split())
        except Exception as http_err:
            logger.warning(f"HTTP fallback scrape failed for {target_url}: {http_err}")

    # Clean whitespace and truncate
    cleaned = re.sub(r'\n\s*\n', '\n\n', text_content or "").strip()
    return cleaned[:max_chars]


# ── ЭТАП 1: Сырой сбор данных и первичная JTBD-гипотеза ───────────────────────

STAGE_1_SYSTEM_PROMPT = """
Ты — старший Growth-стратег и методолог по Jobs to be Done (JTBD) в B2B и B2C аутриче в Telegram.
Твоя задача: изучить предоставленные сырые данные о продукте (текст сайта + документ) и извлечь твердую фактуру.

ВАЖНЕЙШИЕ ПРАВИЛА:
1. НИКАКОГО ИНФОБИЗНЕС-СПАМА: запрещены штампы "инновационный", "уникальный", "лидеры рынка".
2. ТОЛЬКО ТВЕРДЫЕ ФАКТЫ: выписывай реальные цифры, цены, сроки, гарантии, условия из текста. Не придумывай того, чего нет!
3. JTBD-ФОРМУЛА:
   - B2C Flow: Какую задачу (Job) решает конечный клиент (BUYER)?
   - B2B Flow: Какую задачу решает партнер/исполнитель (SELLER / PARTNER), если мы привлекаем его к сотрудничеству?
4. СЛЕПЫЕ ЗОНЫ: Сформулируй 3-4 острых, конкретных вопроса для интервью с основателем и маркетологом проекта, чтобы прояснить главные рычаги конверсии, закрытие возражений и формат первого шага.

ВЕРНИ ТОЛЬКО ЧИСТЫЙ JSON по следующей схеме:
{
  "product_name": "Название продукта/компании",
  "core_effect": "Главный осязаемый эффект от использования продукта в 1-2 предложениях",
  "target_action_detected": "Конечная цель коммуникации (созвон на 15 мин, запрос КП/каталога, триал/доступ, партнерская регистрация)",
  "hard_facts": [
    "Факт 1: Цена / Сроки / Гарантия",
    "Факт 2: Статистика / Цифры"
  ],
  "b2c_flow": {
    "target_audience": "Кто конечные клиенты",
    "job_to_be_done": "Какую задачу они решают (Когда я..., я хочу..., чтобы...)",
    "proposed_offer": "Короткий B2C оффер в 1-2 строки без воды"
  },
  "b2b_flow": {
    "target_partners": "Кто партнеры / исполнители / агентства",
    "job_to_be_done": "Какую задачу решает партнер (заработок комиссии, готовый поток лидов, автоматизация)",
    "proposed_offer": "Короткий B2B партнерский оффер в 1-2 строки"
  },
  "interview_questions": [
    "Вопрос 1 к основателю/маркетологу...",
    "Вопрос 2 к основателю/маркетологу...",
    "Вопрос 3 к основателю/маркетологу..."
  ]
}
"""

async def ingest_project_raw_data(project_id: int, website_url: Optional[str], raw_document: str) -> Dict[str, Any]:
    """
    Executes Stage 1: Scrapes website via Playwright + parses raw document.
    Synthesizes initial JTBD hypothesis and generates 3-4 interview questions for Stage 2.
    """
    site_text = ""
    if website_url:
        site_text = await scrape_website_text(website_url)

    combined_input = f"""
ВЕБ-САЙТ ПРОЕКТА: {website_url or 'Не указан'}
КОНТЕНТ С САЙТА:
{site_text or 'Не удалось спарсить или сайт не указан'}

ДОКУМЕНТ О ПРОЕКТЕ (КОПИПАСТ):
{raw_document}
"""

    summary_json = await ai_rotator.generate_json(
        system_prompt=STAGE_1_SYSTEM_PROMPT,
        user_prompt=combined_input,
        temperature=0.2,
        timeout=25.0
    )

    if not summary_json:
        # Fallback dictionary if AI provider had timeout
        summary_json = {
            "product_name": "Новый Продукт",
            "core_effect": "Быстрое и гарантированное решение ключевой задачи рынка",
            "target_action_detected": "Обсуждение условий и старт сотрудничества",
            "hard_facts": ["Прямой доступ от разработчиков", "Гибкие тарифные условия"],
            "b2c_flow": {
                "target_audience": "Конечные потребители",
                "job_to_be_done": "Быстро решить текущую проблему без переплаты",
                "proposed_offer": "Получите готовое решение вашей задачи уже сегодня"
            },
            "b2b_flow": {
                "target_partners": "Исполнители и агентства",
                "job_to_be_done": "Получить дополнительный поток клиентов и комиссию",
                "proposed_offer": "Предлагаем готовый поток целевых лидов за фиксированный процент"
            },
            "interview_questions": [
                "Какой главный оффер или условие дает максимальную конверсию при личном общении?",
                "Какой формат целевого действия вы считаете самым легким (созвон 10 мин / PDF-презентация / тест)?",
                "Какие 2 главных возражения обычно называют клиенты при первом контакте?"
            ]
        }

    # Format initial greeting for the Stage 2 Interview
    questions_formatted = "\n".join([f"{i+1}. {q}" for i, q in enumerate(summary_json.get("interview_questions", []))])
    initial_assistant_message = (
        f"👋 **Привет! Я изучил данные по вашему проекту «{summary_json.get('product_name', 'Проект')}».**\n\n"
        f"🎯 **Вот как я вижу главный эффект:**\n{summary_json.get('core_effect', '')}\n\n"
        f"📌 **B2C Вектор:** {summary_json.get('b2c_flow', {}).get('proposed_offer', '')}\n"
        f"🤝 **B2B Вектор:** {summary_json.get('b2b_flow', {}).get('proposed_offer', '')}\n\n"
        f"Чтобы докрутить стратегию до максимума и заложить в ИИ ваши реальные инсайты продаж, ответьте, пожалуйста, на несколько вопросов:\n\n"
        f"{questions_formatted}\n\n"
        f"*(Вы можете ответить на всё сразу или написать любые свои мысли и корректировки в свободной форме!)*"
    )

    initial_history = [
        {"role": "assistant", "content": initial_assistant_message, "timestamp": datetime.now(timezone.utc).isoformat()}
    ]

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(OutreachProject)
            .where(OutreachProject.id == project_id)
            .values(
                website_url=website_url,
                raw_document=raw_document,
                raw_ingestion_summary=summary_json,
                interview_history=initial_history,
                strategy_status="INTERVIEWING"
            )
        )
        await session.commit()

    return {
        "status": "INTERVIEW_READY",
        "raw_summary": summary_json,
        "initial_message": initial_assistant_message
    }


# ── ЭТАП 2: Интерактивное интервью со стратегом (Founder/Marketer Briefing) ───

STAGE_2_INTERVIEW_SYSTEM_PROMPT = """
Ты — старший партнер по стратегии и JTBD-маркетингу. Ты проводишь живую рабочую сессию (интервью) с основателем проекта и его маркетологом.
Твоя цель: откалибровать понимание продукта, принять их реальные инсайты продаж, скорректировать B2C и B2B офферы и зафиксировать финальные правила короткой воронки (1-2 касания до цели).

Ты общаешься уважительно, четко, без лишней "воды", как умный коллега на планерке.

ТЫ ПОЛУЧАЕШЬ:
- Сырую выжимку из сайта/документа.
- Историю предыдущей переписки в интервью.
- Свежий ответ основателя/маркетолога.

ОТВЕТЬ В СТРОГОМ JSON:
{
  "assistant_reply": "Твой живой ответ основателю. Подтверди, что ты понял из его слов (резюмируй инсайт), дай короткий фидбек и, если нужно, задай 1 уточняющий вопрос. Если всё уже предельно ясно — предложи зафиксировать стратегию.",
  "is_strategy_ready": true/false (поставь true, если ключевые триггеры, B2C/B2B офферы и целевое действие ясны),
  "calibrated_strategy": {
    "product_name": "Имя продукта",
    "target_action": "Четкое целевое действие (куда ведем: созвон / презентация / триал / партнерство)",
    "tone_of_voice": "Тональность общения в ТГ (напр. 'Деловой, дружелюбный, на равных, без панибратства')",
    "b2c_flow": {
      "target_job": "Какую задачу закрываем клиенту",
      "pain_trigger": "Главная боль, на которую давим в первом касании",
      "opener_formula": "Шаблон/смысл первого касания (макс 2 предложения)",
      "cta_angle": "Как выдаем ценность и закрываем на цель при согласии"
    },
    "b2b_flow": {
      "target_job": "Какую задачу закрываем партнеру/бизнесу (выгода)",
      "pain_trigger": "Главный партнерский триггер",
      "opener_formula": "Шаблон/смысл первого касания партнеру",
      "cta_angle": "Как закрываем на партнерское целевое действие"
    },
    "objection_handlers": {
      "дорого": "Аргумент из документа/интервью",
      "скиньте_сайт": "Аргумент почему лучше короткий созвон/тест",
      "уже_есть_подрядчик": "Аргумент в пользу теста нашего решения"
    }
  }
}
"""

async def process_interview_turn(project_id: int, user_message: str) -> Dict[str, Any]:
    """
    Processes a turn in the Strategy Interview: incorporates user's feedback,
    calibrates the JTBD blueprint, and replies conversationally.
    """
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(OutreachProject).where(OutreachProject.id == project_id))
        proj = res.scalars().first()
        if not proj:
            return {"error": "Project not found"}

        history = proj.interview_history or []
        history.append({
            "role": "user",
            "content": user_message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        raw_summary = proj.raw_ingestion_summary or {}

    context_prompt = f"""
ПАСПОРТ ПРОЕКТА (СЫРЫЕ ДАННЫЕ):
{json.dumps(raw_summary, ensure_ascii=False, indent=2)}

ИСТОРИЯ ДИАЛОГА В ИНТЕРВЬЮ:
{json.dumps(history[-6:], ensure_ascii=False, indent=2)}

НОВОЕ СООБЩЕНИЕ ОСНОВАТЕЛЯ / МАРКЕТОЛОГА:
"{user_message}"
"""

    ai_reply_json = await ai_rotator.generate_json(
        system_prompt=STAGE_2_INTERVIEW_SYSTEM_PROMPT,
        user_prompt=context_prompt,
        temperature=0.2,
        timeout=25.0
    )

    if not ai_reply_json:
        ai_reply_json = {
            "assistant_reply": "Спасибо за уточнение! Я учел ваши корректировки в B2B и B2C стратегиях. Если всё устраивает — мы готовы зафиксировать стратегию и переходить к генерации сообщений по базе.",
            "is_strategy_ready": True,
            "calibrated_strategy": raw_summary
        }

    assistant_text = ai_reply_json.get("assistant_reply", "Принято, обновляю стратегию.")
    history.append({
        "role": "assistant",
        "content": assistant_text,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(OutreachProject)
            .where(OutreachProject.id == project_id)
            .values(
                interview_history=history,
                locked_strategy=ai_reply_json.get("calibrated_strategy", {})
            )
        )
        await session.commit()

    return {
        "assistant_reply": assistant_text,
        "is_strategy_ready": ai_reply_json.get("is_strategy_ready", False),
        "calibrated_strategy": ai_reply_json.get("calibrated_strategy", {})
    }


async def finalize_and_lock_strategy(project_id: int, final_notes: Optional[str] = None) -> Dict[str, Any]:
    """
    Locks the calibrated JTBD strategy blueprint into the project,
    generates system_prompt & knowledge_base, and sets status to 'LOCKED'.
    """
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(OutreachProject).where(OutreachProject.id == project_id))
        proj = res.scalars().first()
        if not proj:
            return {"error": "Project not found"}

        strategy = proj.locked_strategy or proj.raw_ingestion_summary or {}
        if final_notes:
            strategy["founder_final_notes"] = final_notes

        b2c = strategy.get("b2c_flow", {})
        b2b = strategy.get("b2b_flow", {})
        target_cta = strategy.get("target_action", "завершение сделки / созвон")

        # Compile concise authoritative system prompt for dialogue execution
        compiled_system_prompt = f"""
Ты — персональный AI-менеджер проекта «{strategy.get('product_name', proj.name)}».
Твоя главная задача по Jobs to be Done: в 1-2 реплики довести клиента до ЦЕЛЕВОГО ДЕЙСТВИЯ: {target_cta}.

ТЫ РАБОТАЕШЬ ПО ДВУМ FLOW:
1. FLOW B2C (Клиенты):
   - Задача клиента: {b2c.get('target_job', '')}
   - Главная боль/триггер: {b2c.get('pain_trigger', '')}
   - Угол закрытия: {b2c.get('cta_angle', '')}

2. FLOW B2B (Партнеры/Исполнители):
   - Задача партнера: {b2b.get('target_job', '')}
   - Главная выгода: {b2b.get('pain_trigger', '')}
   - Угол закрытия: {b2b.get('cta_angle', '')}

ПРАВИЛА ОБЩЕНИЯ В TELEGRAM:
- Отвечай коротко (максимум 2-3 предложения, никаких простыней текста).
- Тональность: {strategy.get('tone_of_voice', 'деловой, дружелюбный, на равных')}.
- Не спорь с клиентом. Если клиент задает вопрос — отвечай по факту и сразу предлагай сделать целевой шаг ({target_cta}).
"""

        compiled_kb = f"""
БАЗА ЗНАНИЙ ПРОЕКТА «{strategy.get('product_name', proj.name)}»
ЦЕЛЕВОЕ ДЕЙСТВИЕ: {target_cta}

ОФФЕР ДЛЯ B2C:
{b2c.get('opener_formula', '')}

ОФФЕР ДЛЯ B2B:
{b2b.get('opener_formula', '')}

ОТРАБОТКА ВОЗРАЖЕНИЙ:
{json.dumps(strategy.get('objection_handlers', {}), ensure_ascii=False, indent=2)}

ТВЕРДЫЕ ФАКТЫ И ЦИФРЫ:
{json.dumps(proj.raw_ingestion_summary.get('hard_facts', []), ensure_ascii=False, indent=2) if proj.raw_ingestion_summary else ''}
"""

        await session.execute(
            update(OutreachProject)
            .where(OutreachProject.id == project_id)
            .values(
                locked_strategy=strategy,
                system_prompt=compiled_system_prompt.strip(),
                knowledge_base=compiled_kb.strip(),
                strategy_status="LOCKED"
            )
        )
        await session.commit()

    return {
        "status": "STRATEGY_LOCKED",
        "project_id": project_id,
        "strategy": strategy
    }


# ── ЭТАП 3: Генерация Индивидуальной Коммуникации по базе (B2B + B2C) ─────────

LEAD_COMMUNICATION_PROMPT = """
Ты — старший копирайтер прямого отклика для Telegram.
Перед тобой ЛИД, спаршенный из Telegram-чатов целевого рынка, и УТВЕРЖДЕННАЯ JTBD-СТРАТЕГИЯ проекта.

ТВОЯ ЗАДАЧА:
1. Определить FLOW лида:
   - "B2C" — если человек ищет услугу/товар для себя (BUYER).
   - "B2B" — если человек предлагает свои услуги, представляет компанию или агентство (SELLER/PARTNER).
2. Выявить его конкретную задачу (JTBD Job) и острую боль/триггер из его текста.
3. Сгенерировать СВЕРХКОРОТКУЮ цепочку касаний:
   - `msg_1_opener`: Первое сообщение (максимум 2 коротких предложения, до 180 символов!). Обязательно сослаться на его сообщение/контекст из чата + назвать понятный эффект продукта + легкий вопрос (без ссылок!).
   - `msg_2_followup`: Мягкий пинг через 24 часа под другим углом (если прочитал и не ответил).
   - `target_cta_rule`: Четкая инструкция, что отправить/предложить, когда он ответит утвердительно (выдача ссылки/теста/материала).

ВЕРНИ ТОЛЬКО ЧИСТЫЙ JSON:
{
  "flow_type": "B2C" или "B2B",
  "jtbd_job": "Какую задачу решает этот конкретный человек",
  "jtbd_trigger": "Острая боль или триггер из его текста",
  "msg_1_opener": "Текст первого сообщения (до 180 символов, без ссылок)",
  "msg_2_followup": "Текст фоллоу-апа через 24 часа",
  "target_cta_rule": "Инструкция для адаптивного ответа при согласии",
  "probable_objection_solution": "Возможное возражение и контраргумент"
}
"""

async def generate_leads_communication(
    project_id: int,
    limit: int = 50,
    flow_filter: Optional[str] = None,
    location_code: Optional[str] = None,
    niche_code: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Pulls candidate leads from both B2C (Lead) and B2B (OutreachLead) pools filtered by GEO and Niche.
    Synthesizes individualized communication according to the locked JTBD strategy,
    and inserts rows into b2b_prospects with approval_status = 'DRAFT' and linked task_id.
    """
    from src.core.taxonomy import normalize_niche
    norm_niche = normalize_niche(niche_code) if niche_code and niche_code not in ("all", "any") else None

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(OutreachProject).where(OutreachProject.id == project_id))
        proj = res.scalars().first()
        if not proj or not proj.locked_strategy:
            return []

        strategy = proj.locked_strategy

        # Ensure OutreachTask exists for this project so prospects are directly bound
        task_res = await session.execute(
            select(OutreachTask).where(OutreachTask.project_id == project_id)
        )
        task = task_res.scalars().first()
        if not task:
            task = OutreachTask(
                project_id=project_id,
                name=f"Кампания: {proj.name}",
                location_code=location_code or "global",
                niche_code=niche_code or "all",
                status="ACTIVE",
                working_hours_start="09:00",
                working_hours_end="21:00",
                timezone="Asia/Kolkata" if location_code == "goa" else "Asia/Dubai"
            )
            session.add(task)
            await session.flush()
        assigned_task_id = task.id

        # Gather B2B candidates
        b2b_candidates = []
        if flow_filter in (None, "ALL", "B2B"):
            stmt_b2b = select(OutreachLead).where(
                OutreachLead.status.in_(["READY_FOR_OUTREACH", "AVAILABLE", "SYNCED"])
            )
            if location_code and location_code not in ("all", "global"):
                stmt_b2b = stmt_b2b.where(OutreachLead.location_code == location_code)
            if norm_niche and norm_niche != "general":
                stmt_b2b = stmt_b2b.where(OutreachLead.niche_code == norm_niche)
            stmt_b2b = stmt_b2b.limit(limit)
            b2b_candidates = list((await session.execute(stmt_b2b)).scalars().all())

        # Gather B2C candidates
        b2c_candidates = []
        if flow_filter in (None, "ALL", "B2C"):
            stmt_b2c = select(Lead).options(selectinload(Lead.user)).where(
                Lead.status.in_(["AVAILABLE", "NEW"])
            )
            if location_code and location_code not in ("all", "global"):
                stmt_b2c = stmt_b2c.where(Lead.location_code == location_code)
            if norm_niche and norm_niche != "general":
                stmt_b2c = stmt_b2c.where(Lead.niche == norm_niche)
            stmt_b2c = stmt_b2c.limit(limit)
            b2c_candidates = list((await session.execute(stmt_b2c)).scalars().all())


    generated_results = []
    
    # Process up to limit total candidates
    candidates = []
    for item in b2b_candidates:
        candidates.append({"type": "B2B", "obj": item})
    for item in b2c_candidates:
        candidates.append({"type": "B2C", "obj": item})

    candidates = candidates[:limit]

    for item in candidates:
        lead_obj = item["obj"]
        lead_type = item["type"]

        if lead_type == "B2B":
            username = getattr(lead_obj, "author_username", None)
            first_name = getattr(lead_obj, "author_first_name", "партнер")
            tg_id = getattr(lead_obj, "telegram_id", None)
            raw_text = getattr(lead_obj, "raw_ad_text", "")
            niche = getattr(lead_obj, "niche_code", "business")
            loc = getattr(lead_obj, "location_code", "global")
            source_id = str(lead_obj.id)
            source_type = "outreach_lead"
        else:
            u_rel = getattr(lead_obj, "user", None)
            username = getattr(u_rel, "username", None) if u_rel else getattr(lead_obj, "source_chat_username", None)
            first_name = getattr(u_rel, "first_name", "клиент") if u_rel and u_rel.first_name else "клиент"
            tg_id = getattr(lead_obj, "user_id", None)
            raw_text = getattr(lead_obj, "intent_summary", "") or getattr(lead_obj, "sales_hook", "") or getattr(lead_obj, "reasoning", "")
            niche = getattr(lead_obj, "niche_code", "services")
            loc = getattr(lead_obj, "location_code", "global")
            source_id = str(lead_obj.id)
            source_type = "b2c_lead"

        # Check if prospect already exists for this project and lead
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(B2BProspect).where(
                    B2BProspect.project_id == project_id,
                    (B2BProspect.source_lead_id == source_id) | 
                    ((B2BProspect.telegram_id == tg_id) if tg_id else (B2BProspect.username == username))
                )
            )
            if existing.scalars().first():
                continue

        prompt = f"""
СТРАТЕГИЯ ПРОЕКТА:
{json.dumps(strategy, ensure_ascii=False, indent=2)}

ИНФОРМАЦИЯ О ЛИДЕ:
- Тип базы источника: {lead_type}
- Ниша: {niche} | Локация: {loc}
- Имя/Юзернейм: {first_name} (@{username or 'нет'})
- Сообщение лида из чата:
"{raw_text[:500]}"
"""

        comm_json = await ai_rotator.generate_json(
            system_prompt=LEAD_COMMUNICATION_PROMPT,
            user_prompt=prompt,
            temperature=0.3,
            timeout=15.0
        )

        if not comm_json:
            # Fallback generation
            flow = "B2B" if lead_type == "B2B" else "B2C"
            comm_json = {
                "flow_type": flow,
                "jtbd_job": f"Решение задачи в нише {niche}",
                "jtbd_trigger": "Интерес к оптимизации и быстрому результату",
                "msg_1_opener": f"Привет, {first_name}! Увидел ваше сообщение по {niche}. У нас есть решение, которое дает результат без лишней рутины. Вам актуально сейчас?",
                "msg_2_followup": f"Привет! Хотел уточнить, удалось ли решить вопрос по {niche} или предложение еще актуально?",
                "target_cta_rule": f"При согласии предоставить детали и перевести на {strategy.get('target_action', 'созвон')}",
                "probable_objection_solution": "Показать конкретный кейс и гарантии"
            }

        async with AsyncSessionLocal() as session:
            new_prospect = B2BProspect(
                project_id=project_id,
                task_id=assigned_task_id,
                flow_type=comm_json.get("flow_type", lead_type),

                source_lead_type=source_type,
                source_lead_id=source_id,
                telegram_id=tg_id,
                username=username,
                niche=niche,
                location_code=loc,
                raw_ad_text=raw_text[:1000] if raw_text else "Лид из базы рынка",
                sales_hook=comm_json.get("jtbd_trigger", "")[:250],
                jtbd_job=comm_json.get("jtbd_job", ""),
                jtbd_trigger=comm_json.get("jtbd_trigger", ""),
                msg_1_opener=comm_json.get("msg_1_opener", ""),
                msg_2_followup=comm_json.get("msg_2_followup", ""),
                target_cta_rule=comm_json.get("target_cta_rule", ""),
                probable_objection_solution=comm_json.get("probable_objection_solution", ""),
                generated_message=comm_json.get("msg_1_opener", ""),
                approval_status="DRAFT",
                status="PENDING_APPROVAL",
                touch_stage=0
            )
            session.add(new_prospect)
            await session.commit()
            await session.refresh(new_prospect)

            generated_results.append({
                "id": new_prospect.id,
                "flow_type": new_prospect.flow_type,
                "username": new_prospect.username,
                "niche": new_prospect.niche,
                "msg_1_opener": new_prospect.msg_1_opener,
                "msg_2_followup": new_prospect.msg_2_followup,
                "approval_status": new_prospect.approval_status
            })

    return generated_results


async def refine_prospects_with_deep_prompt(project_id: int, prospect_ids: List[int], deep_prompt: str) -> List[Dict[str, Any]]:
    """
    Applies the founder's/marketer's deep custom prompt to a batch of prospects
    in the table to rewrite openers, follow-ups and CTA rules.
    """
    async with AsyncSessionLocal() as session:
        res_proj = await session.execute(select(OutreachProject).where(OutreachProject.id == project_id))
        proj = res_proj.scalars().first()
        strategy = proj.locked_strategy if proj else {}

        stmt = select(B2BProspect).where(
            B2BProspect.project_id == project_id,
            B2BProspect.id.in_(prospect_ids)
        )
        prospects = list((await session.execute(stmt)).scalars().all())

    updated = []
    for p in prospects:
        refine_system = f"""
Ты — топовый копирайтер аутрича в Telegram.
Основатель/маркетолог дал указание переписать коммуникацию для выбранного контакта.

УКАЗАНИЕ МАРКЕТОЛОГА:
"{deep_prompt}"

СТРАТЕГИЯ ПРОЕКТА:
{json.dumps(strategy, ensure_ascii=False, indent=2)}

ТЕКУЩЕЕ СООБЩЕНИЕ ЛИДА В ТАБЛИЦЕ:
- Flow: {p.flow_type} | @{p.username or 'user'} | Ниша: {p.niche}
- Исходный заход: {p.msg_1_opener}
- Исходный фоллоу-ап: {p.msg_2_followup}
- Исходное сообщение из чата: {p.raw_ad_text[:300]}

ВЕРНИ ОБНОВЛЕННЫЙ JSON:
{{
  "msg_1_opener": "Новый текст первого сообщения с учетом указания маркетолога (до 180 символов)",
  "msg_2_followup": "Новый фоллоу-ап с учетом указания",
  "target_cta_rule": "Новое правило закрытия на целевое действие"
}}
"""
        new_data = await ai_rotator.generate_json(
            system_prompt=refine_system,
            user_prompt="Перепиши коммуникацию строго по указанию.",
            temperature=0.3,
            timeout=15.0
        )

        if new_data:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(B2BProspect)
                    .where(B2BProspect.id == p.id)
                    .values(
                        msg_1_opener=new_data.get("msg_1_opener", p.msg_1_opener),
                        msg_2_followup=new_data.get("msg_2_followup", p.msg_2_followup),
                        target_cta_rule=new_data.get("target_cta_rule", p.target_cta_rule),
                        generated_message=new_data.get("msg_1_opener", p.msg_1_opener)
                    )
                )
                await session.commit()

            updated.append({
                "id": p.id,
                "msg_1_opener": new_data.get("msg_1_opener", p.msg_1_opener),
                "msg_2_followup": new_data.get("msg_2_followup", p.msg_2_followup)
            })

    return updated
