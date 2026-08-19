// Intent Hunter CDP - Light Theme Web Dashboard App Logic

const NICHE_LABELS = {
  auto_kasko: '🚗 Автострахование',
  real_estate: '🏠 Недвижимость',
  auto_broker: '🏎️ Автоброкер'
};

let currentNicheFilter = 'all';

document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initFormHandlers();
  fetchAllData();

  // Auto-refresh polling every 10 seconds
  setInterval(fetchAllData, 10000);

  document.getElementById('btn-refresh-data').addEventListener('click', () => {
    fetchAllData();
  });
});

// Tab Navigation Logic
function initNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const tabName = item.getAttribute('data-tab');
      switchTab(tabName);
    });
  });

  // Filter Buttons
  const filterBtns = document.querySelectorAll('.filter-btn');
  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      filterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentNicheFilter = btn.getAttribute('data-niche');
      fetchLeads();
    });
  });
}

function switchTab(tabName) {
  // Update Nav
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-tab') === tabName);
  });

  // Update Tab Views
  document.querySelectorAll('.tab-view').forEach(view => {
    view.classList.toggle('active', view.id === `tab-${tabName}`);
  });

  // Update Page Title
  const titles = {
    overview: { title: 'Обзор платформы', sub: 'Мониторинг лидов и активность ИИ-прослушки в реальном времени' },
    leads: { title: 'Маркетплейс лидов', sub: 'База квалифицированных горячих контактов с AI Sales Hooks' },
    channels: { title: 'Каналы прослушки', sub: 'Управление отслеживаемыми Telegram чатами и группами' },
    partners: { title: 'B2B Партнеры', sub: 'Зарегистрированные покупатели и балансы' }
  };

  if (titles[tabName]) {
    document.getElementById('page-title').textContent = titles[tabName].title;
    document.getElementById('page-subtitle').textContent = titles[tabName].sub;
  }
}

// Data Fetching Central Manager
async function fetchAllData() {
  await Promise.all([
    fetchStats(),
    fetchLeads(),
    fetchChannels(),
    fetchPartners()
  ]);
}

// 1. Fetch Stats
async function fetchStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const stats = await res.json();

    document.getElementById('stat-total-leads').textContent = stats.total_leads || 0;
    document.getElementById('stat-sold-leads').textContent = stats.sold_leads || 0;
    document.getElementById('stat-b2b-partners').textContent = stats.b2b_partners || 0;
  } catch (err) {
    console.error('Error fetching stats:', err);
  }
}

// 2. Fetch Leads
async function fetchLeads() {
  try {
    let url = '/api/leads?limit=50';
    if (currentNicheFilter !== 'all') {
      url += `&niche=${currentNicheFilter}`;
    }

    const res = await fetch(url);
    if (!res.ok) return;
    const leads = await res.json();

    renderLeadsGrid('leads-marketplace-grid', leads);
    renderLeadsGrid('overview-leads-grid', leads.slice(0, 6));
  } catch (err) {
    console.error('Error fetching leads:', err);
  }
}

function renderLeadsGrid(containerId, leads) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!leads || leads.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="icon">🔍</div>
        <p>Квалифицированных лидов пока нет в системе.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = leads.map(lead => {
    const nicheLabel = NICHE_LABELS[lead.niche_code] || lead.niche_code;
    const confidencePct = Math.round((lead.confidence_score || 0.85) * 100);

    return `
      <div class="lead-item-card">
        <div>
          <div class="lead-header">
            <span class="niche-badge">${nicheLabel}</span>
            <span class="temp-badge ${lead.temperature}">${lead.temperature === 'HOT' ? '🔥 HOT' : '⚡ WARM'}</span>
          </div>

          <div class="lead-summary">
            "${escapeHtml(lead.intent_summary)}"
          </div>

          <div class="sales-hook-box">
            💡 <strong>Sales Hook:</strong> «${escapeHtml(lead.sales_hook)}»
          </div>
        </div>

        <div class="lead-footer">
          <div class="confidence-bar-wrapper">
            <div class="confidence-bar-bg">
              <div class="confidence-bar-fill" style="width: ${confidencePct}%"></div>
            </div>
            <span class="confidence-text">${confidencePct}%</span>
          </div>

          <span class="lead-status-pill ${lead.status}">
            ${lead.status === 'SOLD' ? 'ВЫКУПЛЕН' : lead.price + ' ₽'}
          </span>
        </div>
      </div>
    `;
  }).join('');
}

// 3. Fetch Monitored Channels
async function fetchChannels() {
  try {
    const res = await fetch('/api/channels');
    if (!res.ok) return;
    const channels = await res.json();

    document.getElementById('stat-active-channels').textContent = channels.length || 0;
    renderChannelsTable(channels);
  } catch (err) {
    console.error('Error fetching channels:', err);
  }
}

function renderChannelsTable(channels) {
  const tbody = document.getElementById('channels-table-body');
  if (!tbody) return;

  if (!channels || channels.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 32px;">
          Нет отслеживаемых чатов. Добавьте первый чат выше!
        </td>
      </tr>
    `;
    return;
  }

  const statusBadges = {
    JOINED: '<span class="status-badge JOINED">🟢 Подключен</span>',
    PENDING: '<span class="status-badge PENDING">⏳ В процессе...</span>',
    FAILED: '<span class="status-badge FAILED">🔴 Ошибка</span>'
  };

  tbody.innerHTML = channels.map(ch => {
    const nicheLabel = NICHE_LABELS[ch.niche_code] || ch.niche_code;
    const dateStr = ch.created_at ? new Date(ch.created_at).toLocaleDateString('ru-RU') : '—';
    const badge = statusBadges[ch.status] || ch.status;

    return `
      <tr>
        <td>
          <strong>${escapeHtml(ch.title || ch.username_or_link)}</strong>
          ${ch.title ? `<br><small style="color: var(--text-muted);">${escapeHtml(ch.username_or_link)}</small>` : ''}
          ${ch.error_message ? `<br><small style="color: #DC2626;">└ ${escapeHtml(ch.error_message)}</small>` : ''}
        </td>
        <td>${nicheLabel}</td>
        <td>${badge}</td>
        <td>${dateStr}</td>
        <td>
          <button class="btn-danger-sm" onclick="deleteChannel('${ch.id}')">Удалить</button>
        </td>
      </tr>
    `;
  }).join('');
}

// 4. Fetch Partners
async function fetchPartners() {
  try {
    const res = await fetch('/api/partners');
    if (!res.ok) return;
    const partners = await res.json();

    renderPartnersTable(partners);
  } catch (err) {
    console.error('Error fetching partners:', err);
  }
}

function renderPartnersTable(partners) {
  const tbody = document.getElementById('partners-table-body');
  if (!tbody) return;

  if (!partners || partners.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">
          Нет зарегистрированных B2B партнеров.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = partners.map(p => {
    const dateStr = p.created_at ? new Date(p.created_at).toLocaleDateString('ru-RU') : '—';
    const niches = p.subscribed_niches || [];
    const nichesStr = niches.map(n => NICHE_LABELS[n] || n).join(', ') || '—';
    const priorities = p.niche_priorities || {};

    const prioritySelectors = niches.map(niche => {
      const currentP = priorities[niche] || 3;
      return `
        <div style="margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
          <small style="font-weight: 600;">${NICHE_LABELS[niche] || niche}:</small>
          <select class="form-select" style="padding: 4px 8px; font-size: 12px;" onchange="updatePriority('${p.id}', '${niche}', this.value)">
            <option value="1" ${currentP == 1 ? 'selected' : ''}>⭐ Priority 1 (VIP - 0s)</option>
            <option value="2" ${currentP == 2 ? 'selected' : ''}>🔥 Priority 2 (High - 30s)</option>
            <option value="3" ${currentP == 3 ? 'selected' : ''}>⚡ Priority 3 (Standard - 60s)</option>
          </select>
        </div>
      `;
    }).join('') || '<small style="color: var(--text-muted);">—</small>';

    return `
      <tr>
        <td><strong>${escapeHtml(p.company_name)}</strong></td>
        <td><code>${p.telegram_id}</code></td>
        <td><strong>${p.balance.toFixed(2)} ₽</strong></td>
        <td>${nichesStr}</td>
        <td>${prioritySelectors}</td>
        <td>${dateStr}</td>
      </tr>
    `;
  }).join('');
}

async function updatePriority(partnerId, nicheCode, priorityValue) {
  try {
    const res = await fetch(`/api/partners/${partnerId}/priority`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        niche_code: nicheCode,
        priority: parseInt(priorityValue)
      })
    });
    if (res.ok) {
      fetchPartners();
    }
  } catch (err) {
    console.error('Error updating priority:', err);
  }
}

let grokChatHistory = [];

async function sendGrokChatMessage(userInput) {
  const chatHistoryDiv = document.getElementById('grok-chat-history');
  const resultsContainer = document.getElementById('grok-results-container');
  const btnSend = document.getElementById('btn-grok-send');
  const selectNiche = document.getElementById('select-grok-niche');

  if (!userInput) return;

  // Render user message bubble
  chatHistoryDiv.innerHTML += `
    <div style="display: flex; gap: 10px; justify-content: flex-end; align-items: flex-start;">
      <div style="background: #4F46E5; color: #FFF; padding: 10px 16px; border-radius: 12px; font-size: 14px; line-height: 1.5; max-width: 80%;">
        ${escapeHtml(userInput)}
      </div>
      <div style="font-size: 24px; background: #EEF2FF; padding: 6px; border-radius: 50%;">👤</div>
    </div>
  `;
  chatHistoryDiv.scrollTop = chatHistoryDiv.scrollHeight;

  btnSend.disabled = true;
  btnSend.textContent = '⏳ Grok думает...';

  try {
    const res = await fetch('/api/grok/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_input: userInput,
        history: grokChatHistory,
        niche_code: selectNiche ? selectNiche.value : 'general'
      })
    });

    if (res.ok) {
      const data = await res.json();
      const responseObj = data.response || {};
      const replyText = responseObj.reply_text || 'Готово! Вот найденные каналы:';
      const candidates = responseObj.candidates || [];

      // Update client history
      grokChatHistory.push({ role: 'user', content: userInput });
      grokChatHistory.push({ role: 'assistant', content: replyText });

      // Render assistant bubble
      chatHistoryDiv.innerHTML += `
        <div style="display: flex; gap: 10px; align-items: flex-start;">
          <div style="font-size: 24px; background: #EEF2FF; padding: 6px; border-radius: 50%;">🤖</div>
          <div style="background: #F3F4F6; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.5; color: #1F2937; max-width: 85%;">
            <strong>Grok AI Assistant:</strong><br>
            ${escapeHtml(replyText).replace(/\n/g, '<br>')}
          </div>
        </div>
      `;
      chatHistoryDiv.scrollTop = chatHistoryDiv.scrollHeight;

      if (candidates.length > 0) {
        resultsContainer.style.display = 'block';
        renderGrokResults(candidates);
      }
    }
  } catch (err) {
    console.error('Error sending Grok chat:', err);
  } finally {
    btnSend.disabled = false;
    btnSend.textContent = '💬 Отправить Grok';
  }
}

function sendGrokQuickPrompt(text) {
  const input = document.getElementById('input-grok-chat-msg');
  if (input) {
    input.value = text;
    sendGrokChatMessage(text);
    input.value = '';
  }
}

// Form Handlers
function initFormHandlers() {
  const grokForm = document.getElementById('form-grok-chat');
  if (grokForm) {
    grokForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const input = document.getElementById('input-grok-chat-msg');
      const val = input.value.trim();
      if (val) {
        sendGrokChatMessage(val);
        input.value = '';
      }
    });
  }

  const addForm = document.getElementById('form-add-channel');
  if (addForm) {
    addForm.addEventListener('submit', async (e) => {
      e.preventDefault();

      const inputTarget = document.getElementById('input-channel-target');
      const selectNiche = document.getElementById('select-channel-niche');

      const payload = {
        username_or_link: inputTarget.value.trim(),
        niche_code: selectNiche.value
      };

      try {
        const res = await fetch('/api/channels', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (res.ok) {
          inputTarget.value = '';
          fetchChannels();
        } else {
          alert('Ошибка при добавлении канала');
        }
      } catch (err) {
        console.error('Error adding channel:', err);
      }
    });
  }
}

function renderGrokResults(candidates) {
  const container = document.getElementById('grok-results-container');
  if (!container) return;

  if (!candidates || candidates.length === 0) {
    container.innerHTML = '<div style="padding: 16px; color: var(--text-muted);">К сожалению, подходящих каналов или чатов не найдено.</div>';
    return;
  }

  container.innerHTML = `
    <h4 style="margin-bottom: 12px; font-weight: 700;">🎯 Найдено ${candidates.length} релевантных чатов от Grok:</h4>
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px;">
      ${candidates.map((c, idx) => {
        const isGroup = c.chat_type === 'group';
        const typeBadge = isGroup
          ? '<span style="background: #E0E7FF; color: #3730A3; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 700;">👥 ГРУППА (ЧАТ)</span>'
          : '<span style="background: #FEF3C7; color: #92400E; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 700;">📢 КАНАЛ</span>';

        return `
          <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px; background: #FFF; display: flex; flex-direction: column; justify-content: space-between;">
            <div>
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <strong>${escapeHtml(c.title)}</strong>
                ${typeBadge}
              </div>
              <div style="font-size: 13px; color: #4F46E5; font-weight: 600; margin-bottom: 6px;">${escapeHtml(c.username)}</div>
              <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 8px;">👥 ${escapeHtml(c.estimated_members)} участников</div>
              <div style="font-size: 12px; color: var(--text-color); margin-bottom: 12px;"><i>"${escapeHtml(c.description)}"</i></div>
            </div>
            <button class="btn-primary" style="font-size: 12px; padding: 6px 12px;" onclick="approveGrokCandidate('${escapeHtml(c.username)}', '${escapeHtml(c.title)}', '${c.chat_type}', '${c.niche_code}', this)">
              ✅ Утвердить и подключить
            </button>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

async function approveGrokCandidate(username, title, chatType, nicheCode, btnElement) {
  btnElement.disabled = true;
  btnElement.textContent = '⏳ Сохраняем...';

  try {
    const res = await fetch('/api/channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username_or_link: username,
        title: title,
        chat_type: chatType,
        niche_code: nicheCode
      })
    });

    if (res.ok) {
      btnElement.textContent = '✅ Подключен!';
      btnElement.style.background = '#059669';
      fetchChannels();
    } else {
      btnElement.textContent = '❌ Ошибка';
      btnElement.disabled = false;
    }
  } catch (err) {
    console.error('Error approving candidate:', err);
    btnElement.disabled = false;
  }
}

// Delete Channel Helper
async function deleteChannel(channelId) {
  if (!confirm('Вы уверены, что хотите удалить этот чат из прослушки?')) return;

  try {
    const res = await fetch(`/api/channels/${channelId}`, {
      method: 'DELETE'
    });

    if (res.ok) {
      fetchChannels();
    }
  } catch (err) {
    console.error('Error deleting channel:', err);
  }
}

// Utility Escaper
function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
