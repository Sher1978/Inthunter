import os

with open('src/ingestion/telegram.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Inject _send_admin_alert at the top
if "_send_admin_alert" not in content:
    old_import = "from src.core.config import settings"
    new_import = '''from src.core.config import settings

def _send_admin_alert(msg: str):
    try:
        import asyncio
        from src.bot.alert_bot import notify_superadmins_system_alert
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notify_superadmins_system_alert(msg))
        except RuntimeError:
            pass # No running loop
    except Exception:
        pass
'''
    # Replace only the first occurrence
    content = content.replace(old_import, new_import, 1)


# 2. Patch proxy parsing failure
old_proxy_err = '''                            logger.warning(f"Failed to parse proxy {node.proxy_url} for node {node.db_id}: {e}")'''
new_proxy_err = '''                            logger.warning(f"Failed to parse proxy {node.proxy_url} for node {node.db_id}: {e}")
                            _send_admin_alert(f"⚠️ <b>ОШИБКА ПАРСИНГА ПРОКСИ</b>\\nНода: #{node.db_id}\\nПрокси: <code>{node.proxy_url}</code>\\nОшибка: {e}")'''
if old_proxy_err in content:
    content = content.replace(old_proxy_err, new_proxy_err)

# 3. Patch connection error during MTProto join
old_conn_err = '''                        logger.warning(f"Notice auto-reconnecting node #{node.db_id}: {conn_err}")
                        rejection_reasons.append(f"#{node.db_id}: Connect failed ({conn_err})")'''
new_conn_err = '''                        logger.warning(f"Notice auto-reconnecting node #{node.db_id}: {conn_err}")
                        _send_admin_alert(f"⚠️ <b>ОШИБКА ПОДКЛЮЧЕНИЯ ЮЗЕРБОТА</b>\\nНода: #{node.db_id}\\nОшибка: <code>{conn_err}</code>")
                        rejection_reasons.append(f"#{node.db_id}: Connect failed ({conn_err})")'''
if old_conn_err in content:
    content = content.replace(old_conn_err, new_conn_err)

# 4. Patch Anti-Ban Pacing defer
old_defer = '''        if not available_node:
            logger.info(f"🛡️ Anti-Ban Rate Limiter: Deferring MTProto join for {clean_target}. Node rejection reasons: {', '.join(rejection_reasons)}")
            return False, title or clean_target, "Anti-Ban Pacing: Deferred join"'''
new_defer = '''        if not available_node:
            reason_str = ', '.join(rejection_reasons)
            logger.info(f"🛡️ Anti-Ban Rate Limiter: Deferring MTProto join for {clean_target}. Node rejection reasons: {reason_str}")
            _send_admin_alert(f"⚠️ <b>ОШИБКА ВСТУПЛЕНИЯ ЮЗЕРБОТА</b>\\nКанал: {clean_target}\\n\\nНи один юзербот не смог вступить! Причины отказа:\\n<code>{reason_str}</code>")
            return False, title or clean_target, "Anti-Ban Pacing: Deferred join"'''
if old_defer in content:
    content = content.replace(old_defer, new_defer)


# Write changes back
with open('src/ingestion/telegram.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched telegram.py successfully with admin alerts.")
