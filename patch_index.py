with open('static/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_block = '''              <div style="flex: 1 1 160px; min-width: 140px;">
                <label style="font-size: 12px; font-weight: 600; color: #4B5563;">Статус:</label>
                <select id="filter-channel-status" class="form-select" style="width: 100%;" onchange="loadChannels()">
                  <option value="all">Все статусы</option>
                  <option value="JOINED">Слушаем (Активно)</option>
                  <option value="PENDING">В очереди</option>
                  <option value="FAILED">Ошибки (FAILED)</option>
                </select>
              </div>\n'''

with open('static/index.html', 'w', encoding='utf-8') as f:
    for line in lines:
        if '<div style="flex: 2 1 220px; min-width: 180px;">' in line:
            f.write(new_block)
        f.write(line)
print('Done!')
