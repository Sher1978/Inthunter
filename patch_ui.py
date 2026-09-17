import sys
with open('static/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

target = '''<div style="flex: 2 1 220px; min-width: 180px;">
                <label style="font-size: 12px; font-weight: 600; color: #4B5563;">Поиск по названию / ссылке:</label>'''

new_block = '''<div style="flex: 1 1 160px; min-width: 140px;">
                <label style="font-size: 12px; font-weight: 600; color: #4B5563;">Статус:</label>
                <select id="filter-channel-status" class="form-select" style="width: 100%;" onchange="loadChannels()">
                  <option value="all">Все статусы</option>
                  <option value="JOINED">Слушаем (Активно)</option>
                  <option value="PENDING">В очереди</option>
                  <option value="FAILED">Ошибки (FAILED)</option>
                </select>
              </div>
              '''

if target in text:
    text = text.replace(target, new_block + target)
    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(text)
    print('index.html updated successfully')
else:
    print('target not found in index.html')

with open('static/app.js', 'r', encoding='utf-8') as f:
    app_text = f.read()

target_reset = '''  if (queryInp) queryInp.value = '';
  loadChannels();
  showToast'''

new_reset = '''  const statusSel = document.getElementById('filter-channel-status');
  if (queryInp) queryInp.value = '';
  if (statusSel) statusSel.value = 'all';
  loadChannels();
  showToast'''

if target_reset in app_text:
    app_text = app_text.replace(target_reset, new_reset)
    with open('static/app.js', 'w', encoding='utf-8') as f:
        f.write(app_text)
    print('app.js updated successfully')
else:
    print('target_reset not found in app.js')
