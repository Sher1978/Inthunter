with open('static/app.js', 'r', encoding='utf-8') as f:
    text = f.read()

target = '''<button class="btn-secondary-sm" onclick="openChannelPostsModal('${ch.id}', '${escapeHtml(ch.title || ch.username_or_link)}')" style="font-size:11px; padding:3px 8px; border-radius:6px; background:#EEF2FF; color:#4F46E5; border:1px solid #C7D2FE; font-weight:700; cursor:pointer; white-space:nowrap;" title="Просмотр постов">👀 Посты</button>
            <button class="btn-danger-sm" style="font-size:11px; padding:3px 8px; font-weight:700; white-space:nowrap;" onclick="deleteChannelFromLog('${ch.id}', '${escapeHtml(ch.title)}', '${escapeHtml(ch.username_or_link)}', this)">🗑️ Удал.</button>'''

replacement = '''<button class="btn-secondary-sm" onclick="openChannelPostsModal('${ch.id}', '${escapeHtml(ch.title || ch.username_or_link)}')" style="font-size:11px; padding:3px 8px; border-radius:6px; background:#EEF2FF; color:#4F46E5; border:1px solid #C7D2FE; font-weight:700; cursor:pointer; white-space:nowrap;" title="Просмотр постов">👀 Посты</button>
            ${ch.status === 'FAILED' ? `
              <button class="btn-primary" style="font-size:11px; padding:3px 8px; border-radius:6px; background:#10B981; color:white; border:none; font-weight:700; cursor:pointer; white-space:nowrap;" onclick="event.stopPropagation(); markChannelAsJoined('${ch.id}', this)" title="Отметить как добавленный вручную">👋 Добавлен вручную</button>
            ` : ''}
            <button class="btn-danger-sm" style="font-size:11px; padding:3px 8px; font-weight:700; white-space:nowrap;" onclick="deleteChannelFromLog('${ch.id}', '${escapeHtml(ch.title)}', '${escapeHtml(ch.username_or_link)}', this)">🗑️ Удал.</button>'''

if target in text:
    text = text.replace(target, replacement)
    with open('static/app.js', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Done")
else:
    print("Target not found")
