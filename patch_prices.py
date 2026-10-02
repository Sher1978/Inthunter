with open('src/bot/alert_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_price_block = '''    # Base Price in AED
    base_price = 50.00
    exclusive_price = 200.00
    
    from datetime import datetime, timezone, timedelta
    ts_detected = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M")

    alert_text = ('''

new_price_block = '''    # Determine Location and Price
    loc = "global"
    if messages:
        msg_loc = getattr(messages[-1], "location_code", None)
        if msg_loc and msg_loc != "global":
            loc = msg_loc
        else:
            from src.ingestion.telegram import guess_loc
            loc = guess_loc(getattr(messages[-1], "chat_title", None)) or "global"

    from src.services.purchase_engine import get_lead_pricing_by_location
    base_price, exclusive_price = get_lead_pricing_by_location(loc)
    
    from datetime import datetime, timezone, timedelta
    ts_detected = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M")

    alert_text = ('''

if old_price_block in content:
    content = content.replace(old_price_block, new_price_block)
else:
    print('Failed to find price block 1')

old_msg_block = '''        f"🧠 <b>Анализ ИИ:</b>\\n"
        f"<i>{html.quote(reasoning)}</i>\\n\\n"
        f"💰 <b>Стоимость контакта:</b> <b>{int(base_price)} AED</b>\\n"
        f"👑 <b>Эксклюзив (выкуп):</b> <b>{int(exclusive_price)} AED</b>\\n"
        f"───────────────────────────"'''

new_msg_block = '''        f"🧠 <b>Анализ ИИ:</b>\\n"
        f"<i>{html.quote(reasoning)}</i>\\n\\n"
        f"💰 <b>Стоимость контакта:</b> <b>${base_price:.2f} USD</b>\\n"
        f"👑 <b>Эксклюзив (выкуп):</b> <b>${exclusive_price:.2f} USD</b>\\n"
        f"───────────────────────────"'''

if old_msg_block in content:
    content = content.replace(old_msg_block, new_msg_block)
else:
    print('Failed to find msg block 2')


old_lead_block = '''            new_lead = Lead(
                user_id=user_id,
                niche_code=niche,
                temperature="HOT",
                confidence_score=getattr(lead_result, "confidence_score", 0.5),
                intent_summary=reasoning,
                sales_hook="",
                price=base_price,
                status="AVAILABLE"
            )'''

new_lead_block = '''            new_lead = Lead(
                user_id=user_id,
                niche_code=niche,
                location_code=loc,
                temperature="HOT",
                confidence_score=getattr(lead_result, "confidence_score", 0.5),
                intent_summary=reasoning,
                sales_hook="",
                price=base_price,
                status="AVAILABLE"
            )'''

if old_lead_block in content:
    content = content.replace(old_lead_block, new_lead_block)
else:
    print('Failed to find lead block 3')


with open('src/bot/alert_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Patched alert_bot.py successfully')
