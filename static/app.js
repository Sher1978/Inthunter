// Intent Hunter CDP - Superadmin Web Dashboard App Logic

let NICHE_LABELS = {
  real_estate: '🏠 Недвижимость',
  bike_rent: '🛵 Аренда байков',
  currency_exchange: '💱 Обмен валюты',
  services_visa: '🛂 Визы & Услуги',
  auto_kasko: '🚗 Автострахование',
  community: '💬 Сообщество'
};

let currentNicheFilter = 'all';
let partnersDataCache = [];

document.addEventListener('DOMContentLoaded', () => {
  checkAdminAuth();
  initNavigation();
  initFormHandlers();
  initMobileAndAuth();
  fetchRubrics();
  fetchAllData();

  // Polling for live stream and stats every 3 seconds
  setInterval(fetchLiveStream, 3000);
  setInterval(fetchStats, 6000);

  const btnRefresh = document.getElementById('btn-refresh-data');
  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => {
      fetchAllData();
    });
  }
});

function checkAdminAuth() {
  const overlay = document.getElementById('admin-auth-overlay');
  const authForm = document.getElementById('form-admin-auth');
  const passcodeInp = document.getElementById('input-admin-passcode');
  const errorMsg = document.getElementById('auth-error-msg');

  // Check if opened inside Telegram WebApp or has authenticated session
  const isTelegramWebApp = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData;
  const isAuthed = localStorage.getItem('radar_admin_authed') === 'true';

  if (isTelegramWebApp || isAuthed) {
    if (overlay) overlay.style.display = 'none';
    return;
  }

  if (overlay) overlay.style.display = 'flex';

  if (authForm) {
    authForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const code = passcodeInp.value.trim();
      if (!code) return;

      try {
        const res = await fetch('/api/auth/verify-passcode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ passcode: code })
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
          localStorage.setItem('radar_admin_authed', 'true');
          overlay.style.display = 'none';
        } else {
          if (errorMsg) {
            errorMsg.textContent = `❌ ${data.message || 'Неверный PIN-код!'}`;
            errorMsg.style.display = 'block';
          }
        }
      } catch (err) {
        if (errorMsg) {
          errorMsg.textContent = '❌ Ошибка сети при авторизации';
          errorMsg.style.display = 'block';
        }
      }
    });
  }
}

function initMobileAndAuth() {
  const hamburgerBtn = document.getElementById('btn-hamburger');
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.getElementById('sidebar-overlay');

  if (hamburgerBtn && sidebar && overlay) {
    hamburgerBtn.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      overlay.classList.toggle('active');
    });

    overlay.addEventListener('click', () => {
      sidebar.classList.remove('open');
      overlay.classList.remove('active');
    });
  }

  const navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      if (window.innerWidth <= 768 && sidebar && overlay) {
        sidebar.classList.remove('open');
        overlay.classList.remove('active');
      }
    });
  });

  if (window.Telegram && window.Telegram.WebApp) {
    const webApp = window.Telegram.WebApp;
    webApp.ready();
    webApp.expand();
    const user = webApp.initDataUnsafe ? webApp.initDataUnsafe.user : null;

    if (user) {
      const pName = document.getElementById('profile-display-name');
      const pUname = document.getElementById('profile-username');
      const pId = document.getElementById('profile-tg-id');

      if (pName) pName.textContent = `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'Администратор Telegram';
      if (pUname) pUname.textContent = user.username ? `@${user.username}` : `ID: ${user.id}`;
      if (pId) pId.textContent = user.id;
    }
  }
}

// Tab Navigation Logic
function initNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const tabName = item.getAttribute('data-tab');
      switchTab(tabName);
    });
  });

  const filterBtns = document.querySelectorAll('#leads-rubrics-filter-bar .filter-btn');
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
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-tab') === tabName);
  });

  document.querySelectorAll('.tab-view').forEach(view => {
    view.classList.toggle('active', view.id === `tab-${tabName}`);
  });

  const titles = {
    overview: { title: 'Обзор платформы', sub: 'Мониторинг лидов и активность ИИ-прослушки в реальном времени' },
    leads: { title: 'Маркетплейс лидов', sub: 'База квалифицированных горячих контактов с AI Sales Hooks и рубриками' },
    livestream: { title: 'Онлайн Мониторинг Прослушки', sub: 'Живой поток сообщений из подключенных чатов и парсинг ИИ' },
    channels: { title: 'Каналы и Чаты', sub: 'Управление отслеживаемыми Telegram-сообществами с фильтром по локациям' },
    partners: { title: 'Пользователи & Статистика', sub: 'B2B Партнеры, депозиты и подробный таймлайн выкупов лидов' },
    rubrics: { title: 'Управление рубриками', sub: 'Управление стандартными и автоматически созданными ИИ категорями' }
  };

  if (titles[tabName]) {
    document.getElementById('page-title').textContent = titles[tabName].title;
    document.getElementById('page-subtitle').textContent = titles[tabName].sub;
  }

  if (tabName === 'livestream') fetchLiveStream();
  if (tabName === 'channels') loadChannels();
  if (tabName === 'rubrics') fetchRubrics();
  if (tabName === 'partners') fetchPartners();
}

// Data Fetching Central Manager
async function fetchAllData() {
  await Promise.all([
    fetchStats(),
    fetchLeads(),
    loadChannels(),
    fetchPartners(),
    fetchLiveStream(),
    fetchRubrics()
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
    document.getElementById('stat-active-channels').textContent = stats.monitored_channels !== undefined ? stats.monitored_channels : (stats.activity_logs || 0);
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
    const rubricLabel = lead.rubric_name || NICHE_LABELS[lead.niche_code] || lead.niche_code;
    const confidencePct = Math.round((lead.confidence_score || 0.85) * 100);

    return `
      <div class="lead-item-card">
        <div>
          <div class="lead-header">
            <span class="niche-badge">🏷️ ${escapeHtml(rubricLabel)}</span>
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
            ${lead.status === 'SOLD' ? 'ВЫКУПЛЕН' : '$' + lead.price.toFixed(2) + ' USD'}
          </span>
        </div>
      </div>
    `;
  }).join('');
}

// 3. Fetch Live Stream Scanner Activity
async function fetchLiveStream() {
  const container = document.getElementById('livestream-feed-container');
  if (!container) return;

  try {
    const res = await fetch('/api/live-stream?limit=35');
    if (!res.ok) return;
    const items = await res.json();

    if (!items || items.length === 0) {
      container.innerHTML = `<div style="padding: 24px; color: #6B7280; text-align: center;">Ожидание поступивших сообщений из чатов...</div>`;
      return;
    }

    container.innerHTML = items.map(item => {
      const isLead = item.is_lead;
      const statusBadge = isLead
        ? `<span class="status-badge JOINED">🔥 ГОРЯЧИЙ ЛИД [${escapeHtml(item.niche_code)}]</span>`
        : `<span class="status-badge PENDING">🟢 Просканировано</span>`;

      let tgUrl = item.channel_link || '';
      if (tgUrl && !tgUrl.startsWith('http')) {
        const cleanUser = tgUrl.replace('@', '').trim();
        tgUrl = `https://t.me/${cleanUser}`;
      }

      return `
        <div style="border: 1px solid ${isLead ? '#10B981' : '#E5E7EB'}; background: ${isLead ? 'rgba(16, 185, 129, 0.05)' : '#FFF'}; border-radius: 10px; padding: 12px 16px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
          <div style="flex: 1;">
            <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 4px; flex-wrap: wrap;">
              <span style="font-size: 12px; font-weight: 700; color: #6B7280;">⏱ ${item.time_str}</span>
              <div style="display: flex; align-items: center; gap: 4px;">
                <strong style="color: #1F2937;">📍 ${escapeHtml(item.chat_title)}</strong>
                ${tgUrl ? `
                  <a href="${escapeHtml(tgUrl)}" target="_blank" rel="noopener noreferrer" title="Открыть в Telegram" style="text-decoration: none; color: #3B82F6; font-size: 13px; opacity: 0.85;" aria-label="Открыть чат в Telegram">
                    ↗️
                  </a>
                ` : ''}
              </div>
              ${statusBadge}
            </div>
            <div style="font-size: 14px; color: #374151; line-height: 1.4;">
              💬 <i>"${escapeHtml(item.message_text)}"</i>
            </div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching live stream:', err);
  }
}

// 4. Fetch Monitored Channels with Location & Niche Filters
async function loadChannels() {
  const locSel = document.getElementById('filter-channel-location');
  const nicheSel = document.getElementById('filter-channel-niche');
  const queryInp = document.getElementById('filter-channel-query');

  const locVal = locSel ? locSel.value : 'all';
  const nicheVal = nicheSel ? nicheSel.value : 'all';
  const queryVal = queryInp ? queryInp.value.trim() : '';

  try {
    let url = `/api/channels?location=${locVal}&niche=${nicheVal}`;
    if (queryVal) url += `&query=${encodeURIComponent(queryVal)}`;

    const res = await fetch(url);
    if (!res.ok) return;
    const channels = await res.json();

    renderChannelsTable(channels);
  } catch (err) {
    console.error('Error loading channels:', err);
  }
}

function renderChannelsTable(channels) {
  const tbody = document.getElementById('channels-table-body');
  if (!tbody) return;

  if (!channels || channels.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">
          По заданным фильтрам чатов не найдено.
        </td>
      </tr>
    `;
    return;
  }

  const statusBadges = {
    JOINED: '<span class="status-badge JOINED">🟢 Подключен</span>',
    PENDING: '<span class="status-badge PENDING">⏳ Подключение...</span>',
    FAILED: '<span class="status-badge FAILED">🔴 Ошибка</span>'
  };

  const locLabels = {
    nhatrang: '🇻🇳 Нячанг',
    dubai: '🇦🇪 Дубай',
    global: '🌍 Глобал / РФ'
  };

  tbody.innerHTML = channels.map(ch => {
    const rubricLabel = NICHE_LABELS[ch.niche_code] || ch.niche_code;
    const locLabel = locLabels[ch.location_code] || ch.location_code;
    const dateStr = ch.created_at ? new Date(ch.created_at).toLocaleDateString('ru-RU') : '—';
    const badge = statusBadges[ch.status] || ch.status;

    let tgUrl = ch.username_or_link || '';
    if (tgUrl && !tgUrl.startsWith('http')) {
      const cleanUser = tgUrl.replace('@', '').trim();
      tgUrl = `https://t.me/${cleanUser}`;
    }

    return `
      <tr>
        <td>
          <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
            <strong>${escapeHtml(ch.title || ch.username_or_link)}</strong>
            ${tgUrl ? `
              <a href="${escapeHtml(tgUrl)}" target="_blank" rel="noopener noreferrer" title="Открыть в Telegram" style="text-decoration: none; color: #3B82F6; font-size: 13px; display: inline-flex; align-items: center; opacity: 0.85; transition: transform 0.15s ease;" onmouseover="this.style.transform='scale(1.2)';" onmouseout="this.style.transform='scale(1)';" aria-label="Открыть канал в Telegram">
                ↗️
              </a>
            ` : ''}
          </div>
          ${ch.title ? `<small style="color: var(--text-muted); display: block; margin-top: 2px;">${escapeHtml(ch.username_or_link)}</small>` : ''}
          ${ch.error_message ? `<small style="color: #DC2626; display: block; margin-top: 2px;">└ ${escapeHtml(ch.error_message)}</small>` : ''}
        </td>
        <td>
          <select class="form-select-sm" 
                  style="padding: 4px 8px; font-size: 13px; border-radius: 6px; border: 1px solid #D1D5DB; background: #F9FAFB; cursor: pointer; color: #1F2937; font-weight: 500;"
                  onchange="updateChannelLocation('${ch.id}', this.value)"
                  title="Изменить локацию канала">
            <option value="nhatrang" ${ch.location_code === 'nhatrang' ? 'selected' : ''}>🇻🇳 Нячанг</option>
            <option value="danang" ${ch.location_code === 'danang' ? 'selected' : ''}>🇻🇳 Дананг</option>
            <option value="phuket" ${ch.location_code === 'phuket' ? 'selected' : ''}>🇹🇭 Пхукет</option>
            <option value="bali" ${ch.location_code === 'bali' ? 'selected' : ''}>🇮🇩 Бали</option>
            <option value="dubai" ${ch.location_code === 'dubai' ? 'selected' : ''}>🇦🇪 Дубай</option>
            <option value="tbilisi" ${ch.location_code === 'tbilisi' ? 'selected' : ''}>🇬🇪 Тбилиси</option>
            <option value="global" ${ch.location_code === 'global' ? 'selected' : ''}>🌐 Глобал / РФ</option>
          </select>
        </td>
        <td>${escapeHtml(rubricLabel)}</td>
        <td>${badge}</td>
        <td>${dateStr}</td>
        <td>
          <button class="btn-danger-sm" onclick="deleteChannel('${ch.id}')">Удалить</button>
        </td>
      </tr>
    `;
  }).join('');
}

// Handler for manual inline channel location update
async function updateChannelLocation(channelId, newLocation) {
  try {
    const res = await fetch(`/api/channels/${channelId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ location_code: newLocation })
    });
    const data = await res.json();
    if (res.ok && data.status === 'updated') {
      showToast('✅ Локация канала успешно обновлена!', 'success');
      loadChannels();
    } else {
      showToast('❌ Ошибка при изменении локации канала', 'error');
    }
  } catch (err) {
    console.error('Error updating channel location:', err);
    showToast('❌ Ошибка сети при смене локации', 'error');
  }
}

// 5. Fetch Partners & Detailed Timestamped Purchases History
async function fetchPartners() {
  try {
    const res = await fetch('/api/partners');
    if (!res.ok) return;
    const partners = await res.json();
    partnersDataCache = partners;

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
        <td colspan="7" style="text-align: center; color: var(--text-muted); padding: 32px;">
          Зарегистрированных пользователей пока нет.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = partners.map(p => {
    const roleBadge = `<span class="badge" style="background: #E0E7FF; color: #3730A3;">${p.role}</span>`;

    return `
      <tr>
        <td><strong>${escapeHtml(p.company_name)}</strong></td>
        <td><code>${p.telegram_id}</code></td>
        <td>${roleBadge}</td>
        <td><strong style="color: #059669;">$${p.balance.toFixed(2)} USD</strong></td>
        <td><strong>${p.total_purchases_count}</strong> шт.</td>
        <td><strong>$${p.total_spent.toFixed(2)} USD</strong></td>
        <td>
          <button class="btn-primary" style="padding: 4px 10px; font-size: 12px;" onclick="openPurchasesModal('${p.id}')">
            📜 Таймлайн (${p.total_purchases_count})
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function openPurchasesModal(partnerId) {
  const partner = partnersDataCache.find(p => p.id === partnerId);
  if (!partner) return;

  document.getElementById('modal-partner-title').textContent = `📜 Таймлайн выкупов лидов: ${partner.company_name} (ID ${partner.telegram_id})`;
  const tbody = document.getElementById('modal-purchases-body');

  const purchases = partner.purchases || [];
  if (purchases.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: #6B7280; padding: 24px;">История выкупов пуста. Partner пока не выкупал лиды.</td></tr>`;
  } else {
    tbody.innerHTML = purchases.map(pur => `
      <tr>
        <td><code>${pur.purchased_at_fmt || pur.purchased_at}</code></td>
        <td><span class="niche-badge">🏷️ ${escapeHtml(pur.rubric_name)}</span></td>
        <td><i>"${escapeHtml(pur.intent_summary)}"</i></td>
        <td><strong style="color: #059669;">$${pur.price_paid.toFixed(2)}</strong></td>
      </tr>
    `).join('');
  }

  document.getElementById('purchases-modal').style.display = 'flex';
}

function closePurchasesModal() {
  document.getElementById('purchases-modal').style.display = 'none';
}

// 6. Fetch & Manage Dynamic Rubrics
async function fetchRubrics() {
  try {
    const res = await fetch('/api/rubrics');
    if (!res.ok) return;
    const rubrics = await res.json();

    // Update memory NICHE_LABELS cache
    rubrics.forEach(r => {
      NICHE_LABELS[r.code] = `${r.icon || '🏷️'} ${r.name}`;
    });

    renderRubricsTable(rubrics);
    populateRubricSelects(rubrics);
  } catch (err) {
    console.error('Error fetching rubrics:', err);
  }
}

function renderRubricsTable(rubrics) {
  const tbody = document.getElementById('rubrics-table-body');
  if (!tbody) return;

  tbody.innerHTML = rubrics.map(r => `
    <tr>
      <td style="font-size: 20px;">${r.icon || '🏷️'}</td>
      <td>
        <input type="text" class="form-input" style="padding: 4px 8px; font-size: 13px;" value="${escapeHtml(r.name)}" id="rubric-name-inp-${r.code}">
      </td>
      <td><code>${escapeHtml(r.code)}</code></td>
      <td>${r.is_custom ? '<span class="badge" style="background: #FEF3C7; color: #92400E;">🤖 ИИ Сгенерирована</span>' : '<span class="badge" style="background: #E0E7FF; color: #3730A3;">Системная</span>'}</td>
      <td>
        <div style="display: flex; gap: 6px;">
          <button class="btn-primary" style="padding: 4px 8px; font-size: 12px;" onclick="saveRubricEdit('${r.code}')">Сохранить</button>
          <button class="btn-danger-sm" onclick="deleteRubric('${r.code}')">Удалить</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function populateRubricSelects(rubrics) {
  const filterSel = document.getElementById('filter-channel-niche');
  const addSel = document.getElementById('select-channel-niche');

  if (filterSel) {
    const currentVal = filterSel.value;
    filterSel.innerHTML = `<option value="all">Все рубрики</option>` + rubrics.map(r => `
      <option value="${r.code}">${r.icon || '🏷️'} ${escapeHtml(r.name)}</option>
    `).join('');
    filterSel.value = currentVal || 'all';
  }

  if (addSel) {
    const currentVal = addSel.value;
    addSel.innerHTML = rubrics.map(r => `
      <option value="${r.code}">${r.icon || '🏷️'} ${escapeHtml(r.name)}</option>
    `).join('');
    if (currentVal) addSel.value = currentVal;
  }
}

async function saveRubricEdit(code) {
  const inp = document.getElementById(`rubric-name-inp-${code}`);
  if (!inp) return;

  const newName = inp.value.trim();
  if (!newName) return;

  try {
    const res = await fetch(`/api/rubrics/${code}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName, icon: '🏷️' })
    });
    if (res.ok) {
      fetchRubrics();
    }
  } catch (err) {
    console.error('Error updating rubric:', err);
  }
}

async function deleteRubric(code) {
  if (!confirm(`Удалить рубрику [${code}] из системы?`)) return;

  try {
    const res = await fetch(`/api/rubrics/${code}`, { method: 'DELETE' });
    if (res.ok) {
      fetchRubrics();
    }
  } catch (err) {
    console.error('Error deleting rubric:', err);
  }
}

// Form Handlers
function initFormHandlers() {
  const addChannelForm = document.getElementById('form-add-channel');
  if (addChannelForm) {
    addChannelForm.addEventListener('submit', async (e) => {
      e.preventDefault();

      const inputTarget = document.getElementById('input-channel-target');
      const selectLocation = document.getElementById('select-channel-location');
      const selectNiche = document.getElementById('select-channel-niche');
      const filterLoc = document.getElementById('filter-channel-location');

      const val = inputTarget.value.trim();
      if (!val) return;

      const locVal = selectLocation ? selectLocation.value : 'nhatrang';

      const payload = {
        username_or_link: val,
        location_code: locVal,
        niche_code: selectNiche ? selectNiche.value : 'community'
      };

      try {
        const res = await fetch('/api/channels', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (res.ok && data) {
          if (data.status === 'exists') {
            alert(data.message || 'ℹ️ Этот чат или канал уже есть в списке прослушки!');
          } else if (data.status === 'added') {
            alert(data.message || '✅ Чат/канал успешно добавлен в прослушку!');
            inputTarget.value = '';
            // Auto-switch filter location to 'all' or added location so new channel is never hidden
            if (filterLoc && filterLoc.value !== 'all' && filterLoc.value !== locVal) {
              filterLoc.value = 'all';
            }
          } else if (data.status === 'error') {
            alert(`❌ ${data.message || 'Ошибка добавления'}`);
          } else {
            inputTarget.value = '';
          }
          fetchStats();
          loadChannels();
        } else {
          alert('❌ Ошибка при добавлении канала');
        }
      } catch (err) {
        console.error('Error adding channel:', err);
        alert('❌ Ошибка сети при добавлении канала');
      }
    });
  }

  const addRubricForm = document.getElementById('form-add-rubric');
  if (addRubricForm) {
    addRubricForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const codeInp = document.getElementById('input-rubric-code');
      const nameInp = document.getElementById('input-rubric-name');
      const iconInp = document.getElementById('input-rubric-icon');

      try {
        const res = await fetch('/api/rubrics', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: codeInp.value.trim(),
            name: nameInp.value.trim(),
            icon: iconInp.value.trim() || '🏷️'
          })
        });

        if (res.ok) {
          codeInp.value = '';
          nameInp.value = '';
          fetchRubrics();
        }
      } catch (err) {
        console.error('Error adding rubric:', err);
      }
    });
  }
}

async function deleteChannel(channelId) {
  if (!confirm('Вы уверены, что хотите удалить этот чат из прослушки?')) return;

  try {
    const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
    if (res.ok) {
      loadChannels();
    }
  } catch (err) {
    console.error('Error deleting channel:', err);
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
