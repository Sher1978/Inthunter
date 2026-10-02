import os

with open('src/api/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_str = '''    items = []
    for log in logs:
        # Match Lead specifically for this exact message text
        lead_obj = None
        try:
            if log.message_text:
                lead_stmt = select(Lead).where(
                    Lead.user_id == log.user_id,
                    Lead.intent_summary.ilike(f"%{log.message_text[:20]}%")
                ).order_by(Lead.created_at.desc()).limit(1)
                lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()
        except Exception:
            await db.rollback()
            pass

        eval_stmt = select(AIEvaluationLog).where(
            AIEvaluationLog.user_id == log.user_id,
            AIEvaluationLog.message_text == log.message_text
        ).order_by(AIEvaluationLog.created_at.desc()).limit(1)
        eval_obj = (await db.execute(eval_stmt)).scalar_one_or_none()

        is_lead = (lead_obj is not None) or (eval_obj is not None and eval_obj.is_lead)'''

new_str = '''    user_ids = list({log.user_id for log in logs if log.user_id})
    all_leads = []
    all_evals = []
    if user_ids:
        all_leads = (await db.execute(select(Lead).where(Lead.user_id.in_(user_ids)))).scalars().all()
        all_evals = (await db.execute(select(AIEvaluationLog).where(AIEvaluationLog.user_id.in_(user_ids)))).scalars().all()

    items = []
    for log in logs:
        lead_obj = None
        if log.message_text:
            text_prefix = log.message_text[:20].lower()
            for ld in all_leads:
                if ld.user_id == log.user_id and ld.intent_summary and text_prefix in ld.intent_summary.lower():
                    lead_obj = ld
                    break
        
        eval_obj = None
        for ev in all_evals:
            if ev.user_id == log.user_id and ev.message_text == log.message_text:
                eval_obj = ev
                break

        is_lead = (lead_obj is not None) or (eval_obj is not None and eval_obj.is_lead)'''

if old_str in content:
    content = content.replace(old_str, new_str)
    with open('src/api/routes.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Patched routes.py successfully')
else:
    print('String not found!')
