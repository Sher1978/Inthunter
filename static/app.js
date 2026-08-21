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

let lastScanTimestamp = new Date();

document.addEventListener('DOMContentLoaded', () => {
  checkAdminAuth();
  initNavigation();
  initFormHandlers();
  initMobileAndAuth();
  fetchRubrics();
  fetchAllData();

  // Polling for live stream, AI logs, and stats in real time
  setInterval(fetchLiveStream, 3000);
  setInterval(fetchAIEvaluationLogs, 4000);
  setInterval(fetchStats, 6000);
  setInterval(updateScanTicker, 1000);

  const btnRefresh = document.getElementById('btn-refresh-data');
  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => {
      fetchAllData();
    });
  }
});

function updateScanTicker() {
  const mainTicker = document.getElementById('main-header-scan-ticker');
  const tabTicker = document.getElementById('tab-scan-ticker');
  if (!mainTicker && !tabTicker) return;

  const now = new Date();
  const diffSec = Math.max(0, Math.floor((now - lastScanTimestamp) / 1000));

  let text = '⏱ Сканирование: только что';
  if (diffSec >= 5 && diffSec < 60) {
    text = `⏱ Сканирование: ${diffSec} сек назад`;
  } else if (diffSec >= 60) {
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    text = `⏱ Сканирование: ${mins} мин ${secs} сек назад`;
  }

  if (mainTicker) mainTicker.textContent = text;
  if (tabTicker) tabTicker.textContent = text;
}

function checkAdminAuth() {
  const overlay = document.getElementById('admin-auth-overlay');
  const authForm = document.getElementById('form-admin-auth');
  const passcodeInp = document.getElementById('input-admin-passcode');
  const errorMsg = document.getElementById('auth-error-msg');

  // Check URL query parameters for ?pin=260669 or ?passcode=260669
  const urlParams = new URLSearchParams(window.location.search);
  const pinParam = urlParams.get('pin') || urlParams.get('passcode');
  if (pinParam === '260669' || pinParam === '260669598') {
    localStorage.setItem('radar_admin_authed', 'true');
  }

  // Check if opened inside Telegram WebApp or has authenticated session
  const isTelegramWebApp = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData;
  const isAuthed = localStorage.getItem('radar_admin_authed') === 'true';

  if (isAuthed || isTelegramWebApp) {
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
    rubrics: { title: 'Управление рубриками', sub: 'Управление стандартными и автоматически созданными ИИ категорями' },
    ailogs: { title: 'Логи ИИ-Анализатора', sub: 'Пошаговая логика и комментарии ИИ по каждому отсканированному сообщению' }
  };

  if (titles[tabName]) {
    document.getElementById('page-title').textContent = titles[tabName].title;
    document.getElementById('page-subtitle').textContent = titles[tabName].sub;
  }

  if (tabName === 'livestream') fetchLiveStream();
  if (tabName === 'channels') loadChannels();
  if (tabName === 'rubrics') fetchRubrics();
  if (tabName === 'partners') fetchPartners();
  if (tabName === 'ailogs') fetchAIEvaluationLogs();
}

let currentAILogFilter = 'all';

function setAILogFilter(filter) {
  currentAILogFilter = filter;
  document.querySelectorAll('#ailogs-filter-bar .filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-filter') === filter);
  });
  fetchAIEvaluationLogs();
}

async function fetchAIEvaluationLogs() {
  const container = document.getElementById('ailogs-feed-container');
  if (!container) return;

  try {
    const res = await fetch(`/api/ai-evaluation-logs?filter_type=${currentAILogFilter}`);
    if (!res.ok) {
      container.innerHTML = `<div style="text-align:center; padding: 40px; color:#EF4444;">⚠️ Ошибка загрузки логов (HTTP ${res.status}). Попробуйте позже.</div>`;
      return;
    }
    const logs = await res.json();

    if (!logs || logs.length === 0) {
      container.innerHTML = '<div style="text-align:center; padding: 40px; color:#94A3B8;">Логи работы ИИ-Анализатора пока пусты. Ожидание сканирования сообщений...</div>';
      return;
    }

    container.innerHTML = logs.map(log => {
      const isLead = log.is_lead;
      const statusBadge = isLead
        ? `<span class="temp-badge HOT" style="background:#DCFCE7; color:#15803D; border:1px solid #86EFAC;">🔥 ЛИД (${Math.round((log.confidence_score || 0.95)*100)}%)</span>`
        : `<span class="temp-badge WARM" style="background:#F1F5F9; color:#64748B; border:1px solid #CBD5E1;">❌ НЕ ЛИД</span>`;

      return `
        <div style="background:#FFF; border:1px solid #E2E8F0; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-weight:700; font-size:14px; color:#1E293B;">👤 ${escapeHtml(log.first_name)} (${escapeHtml(log.username)})</span>
              <span style="font-size:12px; color:#64748B; background:#F8FAFC; padding:2px 8px; border-radius:6px; border:1px solid #E2E8F0;">📍 ${escapeHtml(log.chat_title)}</span>
            </div>
            <div style="display:flex; align-items:center; gap:10px;">
              <span style="font-size:12px; color:#94A3B8;">⏱ ${escapeHtml(log.created_at)}</span>
              ${statusBadge}
            </div>
          </div>

          <div style="background:#F8FAFC; border-left:3px solid ${isLead ? '#10B981' : '#94A3B8'}; padding:10px 14px; border-radius:6px; font-size:14px; color:#1E293B; margin-bottom:10px;">
            💬 <i>"${escapeHtml(log.message_text)}"</i>
          </div>

          <div style="background:#EEF2FF; border:1px solid #C7D2FE; border-radius:8px; padding:10px 14px; font-size:13px; color:#3730A3;">
            💡 <strong>Аргументация ИИ (Chain-of-Thought):</strong> ${escapeHtml(log.reasoning)}
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching AI logs:', err);
  }
}

// Data Fetching Central Manager
async function fetchAllData() {
  await Promise.all([
    fetchStats(),
    fetchLeads(),
    loadChannels(),
    fetchPartners(),
    fetchLiveStream(),
    fetchRubrics(),
    fetchReferralStats(),
    fetchAIEvaluationLogs()
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
    const s1h = document.getElementById('stat-scanned-1h');
    if (s1h) {
      const h1 = stats.scanned_1h !== undefined ? stats.scanned_1h : 0;
      const pass = stats.scanned_pass !== undefined ? stats.scanned_pass : 0;
      s1h.textContent = `${h1} - ${pass}`;
    }
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
    renderLeadsGrid('overview-leads-grid', leads.slice(0, 12));
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
    const locBadge = lead.location_name || (lead.location_code === 'dubai' ? '🇦🇪 Дубай' : '🇻🇳 Нячанг');

    return `
      <div class="lead-item-card">
        <div>
          <div class="lead-header">
            <span class="niche-badge">🏷️ ${escapeHtml(rubricLabel)}</span>
            <span class="location-badge" style="background: #F3F4F6; color: #374151; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 6px; border: 1px solid #E5E7EB; white-space: nowrap;">📍 ${escapeHtml(locBadge)}</span>
            <span class="temp-badge ${lead.temperature}">${lead.temperature === 'HOT' ? '🔥 HOT' : '⚡ WARM'}</span>
          </div>

          <div class="lead-summary">
            "${escapeHtml(lead.intent_summary)}"
          </div>

          <div style="font-size: 13px; color: #4B5563; margin-top: 10px; padding: 8px 12px; background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
            <span>💬 Всего сообщений пользователя в системе: <strong>${lead.user_message_count || 1}</strong></span>
            <button class="btn-primary" style="padding: 4px 10px; font-size: 11px;" onclick="openDecryptModal(${lead.user_id})">🔍 РАСШИФРОВКА</button>
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

async function openDecryptModal(userId) {
  let modal = document.getElementById('decrypt-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'decrypt-modal';
    modal.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:9999; display:none; align-items:center; justify-content:center; padding:20px;';
    modal.innerHTML = `
      <div style="background:#FFF; border-radius:16px; width:100%; max-width:620px; max-height:80vh; display:flex; flex-direction:column; padding:24px; box-shadow:0 10px 25px rgba(0,0,0,0.2);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; border-bottom:1px solid #E5E7EB; padding-bottom:12px;">
          <h3 style="margin:0; font-size:18px; color:#1F2937;">🔍 Расшифровка сообщений пользователя</h3>
          <button onclick="document.getElementById('decrypt-modal').style.display='none'" style="background:none; border:none; font-size:22px; cursor:pointer; color:#6B7280;">✕</button>
        </div>
        <div id="decrypt-modal-body" style="overflow-y:auto; flex:1; padding-right:6px;"></div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  const body = document.getElementById('decrypt-modal-body');
  body.innerHTML = '<div style="text-align:center; padding: 30px; color:#6B7280;">⏳ Загрузка расшифровки сообщений...</div>';
  modal.style.display = 'flex';

  try {
    const res = await fetch(`/api/user/${userId}/messages`);
    const logs = await res.json();
    if (!logs || logs.length === 0) {
      body.innerHTML = '<div style="text-align:center; color:#94A3B8; padding: 30px;">Сообщения пользователя не найдены</div>';
      return;
    }

    body.innerHTML = logs.map((log, i) => `
      <div style="border-bottom: 1px solid #F3F4F6; padding: 12px 0;">
        <div style="font-size: 12px; font-weight: 700; color: #6B7280; margin-bottom: 4px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:4px;">
          <span>
            ${i+1}. 📍 <strong style="color:#374151;">${escapeHtml(log.chat_title)}</strong>
            ${log.author_name ? `&nbsp;·&nbsp;<span style="color:#0F766E; font-weight:600;">👤 ${escapeHtml(log.author_name)}</span>` : ''}
          </span>
          <span style="color:#9CA3AF;">⏱ ${escapeHtml(log.timestamp)}</span>
        </div>
        <div style="font-size: 14px; color: #1F2937; line-height: 1.5; background:#F9FAFB; padding:8px 12px; border-radius:8px; border-left:3px solid ${log.author_name ? '#0D9488' : '#6366F1'};">
          💬 <i>"${escapeHtml(log.message_text)}"</i>
        </div>
      </div>
    `).join('');
  } catch (err) {
    body.innerHTML = `<div style="color:#EF4444; padding: 20px;">❌ Ошибка загрузки: ${err.message}</div>`;
  }
}

// 3. Fetch Live Stream Scanner Activity
async function fetchLiveStream() {
  const container = document.getElementById('livestream-feed-container');
  if (!container) return;

  try {
    const res = await fetch('/api/live-stream?limit=35');
    if (!res.ok) return;
    const items = await res.json();

    if (items && items.length > 0) {
      lastScanTimestamp = new Date();
      updateScanTicker();
    }

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

async function qualifyMessageAsLead(btn) {
  const text = btn.getAttribute('data-msg');
  const chatTitle = btn.getAttribute('data-chat');
  if (!text) return;

  btn.disabled = true;
  btn.textContent = '⏳ Обработка...';

  try {
    const res = await fetch('/api/leads/qualify-manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_text: text, chat_title: chatTitle })
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      alert(`✅ Лид успешно создан и помещен в Маркетплейс!\n\nНиша: ${data.niche_code}\nСуть: ${data.intent_summary}`);
      fetchAllData();
    } else {
      alert(`❌ ${data.message || 'Ошибка квалификации лида'}`);
      btn.disabled = false;
      btn.textContent = '⚡ Пометить как Лид';
    }
  } catch (err) {
    console.error('Error qualifying lead:', err);
    alert('❌ Ошибка при отправке');
    btn.disabled = false;
    btn.textContent = '⚡ Пометить как Лид';
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

    // Dynamically update Profile Balance card
    const superadmin = partners.find(p => p.role === 'SUPERADMIN') || partners[0];
    if (superadmin) {
      const balEl = document.getElementById('profile-balance-value');
      if (balEl) {
        balEl.textContent = superadmin.balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      }
    }

    renderPartnersTable(partners);
  } catch (err) {
    console.error('Error fetching partners:', err);
  }
}

function showToast(msg, type = 'info') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed; bottom:20px; right:20px; z-index:99999; display:flex; flex-direction:column; gap:10px; pointer-events:none;';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.style.cssText = `background:${type === 'success' ? '#10B981' : (type === 'error' ? '#EF4444' : '#4F46E5')}; color:#FFF; padding:12px 18px; border-radius:10px; font-size:13px; font-weight:600; box-shadow:0 4px 14px rgba(0,0,0,0.15); pointer-events:auto; transition:all 0.3s ease; opacity:0; transform:translateY(10px);`;
  toast.textContent = msg;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '1';
    toast.style.transform = 'translateY(0)';
  }, 10);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
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
    return `
      <tr>
        <td><strong>${escapeHtml(p.company_name)}</strong></td>
        <td><code>${p.telegram_id}</code></td>
        <td>
          <select class="form-select-sm" 
                  style="padding: 6px 10px; font-size: 13px; border-radius: 8px; border: 1px solid #C7D2FE; background: #EEF2FF; color: #3730A3; font-weight: 700; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,0.05);"
                  onchange="updatePartnerRole('${p.id}', this.value)"
                  title="Изменить роль пользователя в системе">
            <option value="DEMO" ${p.role === 'DEMO' ? 'selected' : ''}>🆕 DEMO (Демо)</option>
            <option value="REGULAR" ${p.role === 'REGULAR' ? 'selected' : ''}>🔵 REGULAR (Регулярный)</option>
            <option value="VIP" ${p.role === 'VIP' ? 'selected' : ''}>⭐ VIP (ВИП)</option>
            <option value="ADMIN" ${p.role === 'ADMIN' ? 'selected' : ''}>🔑 ADMIN (Администратор)</option>
            <option value="SUPERADMIN" ${p.role === 'SUPERADMIN' ? 'selected' : ''}>👑 SUPERADMIN (Суперадмин)</option>
          </select>
        </td>
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

async function updatePartnerRole(partnerId, newRole) {
  try {
    const res = await fetch(`/api/partners/${partnerId}/role`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: newRole })
    });
    const data = await res.json();
    if (res.ok && data.status === 'updated') {
      showToast(`✅ Роль пользователя успешно изменена на ${newRole}!`, 'success');
      fetchPartners();
    } else {
      showToast(`❌ ${data.message || 'Ошибка изменения роли'}`, 'error');
    }
  } catch (err) {
    console.error('Error updating partner role:', err);
    showToast('❌ Ошибка сети при изменении роли', 'error');
  }
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

async function fetchReferralStats() {
  try {
    const res = await fetch('/api/referrals/stats?telegram_id=8866001783');
    if (!res.ok) return;
    const data = await res.json();

    const refLinkInp = document.getElementById('input-referral-link');
    const qrImg = document.getElementById('profile-referral-qr');
    const invitedEl = document.getElementById('ref-invited-count');
    const earnedEl = document.getElementById('ref-total-earned');
    const balEl = document.getElementById('ref-balance-available');
    const btnWithdraw = document.getElementById('btn-request-withdraw');
    const noticeEl = document.getElementById('withdraw-notice');

    if (refLinkInp && data.referral_link) refLinkInp.value = data.referral_link;
    if (qrImg && data.qr_code_base64) qrImg.src = data.qr_code_base64;
    if (invitedEl) invitedEl.textContent = `${data.invited_count || 0} чел`;
    if (earnedEl) earnedEl.textContent = `$${(data.total_referral_earned || 0).toFixed(2)}`;
    if (balEl) balEl.textContent = `$${(data.referral_balance || 0).toFixed(2)}`;

    if (btnWithdraw && noticeEl) {
      if (data.can_withdraw) {
        btnWithdraw.disabled = false;
        btnWithdraw.style.opacity = '1';
        noticeEl.textContent = '✅ Ваш реферальный баланс составляет $50.00+ USD. Вы можете оформить заявку на вывод!';
        noticeEl.style.color = '#059669';
      } else {
        btnWithdraw.disabled = true;
        btnWithdraw.style.opacity = '0.5';
        noticeEl.textContent = `ℹ️ Накопите от $50.00 USD для вывода средств. Текущий баланс: $${(data.referral_balance || 0).toFixed(2)} USD`;
        noticeEl.style.color = '#64748B';
      }
    }
  } catch (err) {
    console.error('Error fetching referral stats:', err);
  }
}

function copyReferralLink() {
  const inp = document.getElementById('input-referral-link');
  if (inp) {
    inp.select();
    navigator.clipboard.writeText(inp.value);
    alert('📋 Реферальная ссылка скопирована в буфер обмена!');
  }
}

async function submitWithdrawalRequest() {
  const detailsInp = document.getElementById('input-withdraw-details');
  if (!detailsInp) return;
  const val = detailsInp.value.trim();
  if (!val) {
    alert('⚠️ Пожалуйста, укажите реквизиты (USDT TRC20 / TON / Карта) для вывода средств!');
    return;
  }

  try {
    const res = await fetch('/api/referrals/withdraw', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telegram_id: 8866001783, payment_details: val })
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      alert(`✅ ${data.message}`);
      detailsInp.value = '';
      fetchReferralStats();
    } else {
      alert(`❌ ${data.message || 'Ошибка создания заявки на вывод'}`);
    }
  } catch (err) {
    console.error('Error submitting withdrawal:', err);
    alert('❌ Ошибка сети при отправке заявки');
  }
}

// ── Channel Effectiveness Heatmap ──────────────────────────────────────────
async function loadChannelEffectiveness() {
  const tbody = document.getElementById('eff-table-body');
  const summaryBar = document.getElementById('eff-summary-bar');
  if (!tbody) return;

  tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 24px;"><span class="loading-spinner" style="width:20px;height:20px;border-width:2px;display:inline-block;"></span> Загрузка данных эффективности...</td></tr>';

  try {
    const res = await fetch('/api/channels/effectiveness');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const channels = await res.json();

    if (!channels || channels.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#94A3B8;padding:24px;">Каналы не найдены</td></tr>';
      return;
    }

    // Summary bar counts
    const total = channels.length;
    const dead = channels.filter(c => c.is_dead).length;
    const active = channels.filter(c => c.days_idle === 0).length;

    if (summaryBar) {
      summaryBar.innerHTML = `
        <span class="eff-sum-pill">📡 Всего каналов: <strong>${total}</strong></span>
        <span class="eff-sum-pill" style="color:#059669;">🟢 Активных: <strong>${active}</strong></span>
        <span class="eff-sum-pill" style="color:#9F1239;">💀 Мёртвых (7д+): <strong>${dead}</strong></span>
        <span class="eff-sum-pill" style="color:#B45309;">⚠️ Требуют внимания: <strong>${channels.filter(c => !c.is_dead && c.days_idle >= 3).length}</strong></span>
      `;
    }

    tbody.innerHTML = channels.map(ch => {
      const rowClass = `eff-row-${ch.color_class.replace('eff-', '')}`;
      const tgLink = ch.username_or_link
        ? (ch.username_or_link.startsWith('@')
            ? `https://t.me/${ch.username_or_link.slice(1)}`
            : ch.username_or_link)
        : '#';
      const idleLabel = ch.days_idle !== null ? `${ch.days_idle} дн.` : '—';
      const deleteBtn = ch.is_dead
        ? `<button class="btn-delete-dead" onclick="deleteDeadChannel('${ch.id}', '${(ch.title || ch.username_or_link).replace(/'/g, '')}')">🗑 Удалить</button>`
        : '—';

      return `
        <tr class="${rowClass}">
          <td>
            <span class="eff-badge ${ch.color_class}">
              ${ch.color_emoji} ${ch.color_label}
            </span>
          </td>
          <td>
            <a href="${tgLink}" target="_blank" rel="noopener" style="color: var(--primary); font-weight:600; text-decoration:none;">
              ${ch.title || ch.username_or_link}
            </a>
            <div style="font-size:11px;color:#94A3B8;">${ch.username_or_link}</div>
          </td>
          <td><span style="font-size:13px;">${ch.location_name}</span></td>
          <td><span style="font-size:13px;">${ch.niche_name}</span></td>
          <td style="text-align:center; font-weight:700; color: ${ch.msgs_7d > 0 ? 'var(--primary)' : '#94A3B8'};">${ch.msgs_7d}</td>
          <td style="text-align:center; font-weight:700; color: ${ch.leads_7d > 0 ? '#059669' : '#94A3B8'};">${ch.leads_7d}</td>
          <td style="text-align:center; font-weight:600;">${ch.leads_total}</td>
          <td style="font-size:12px; color:#64748B;">${ch.last_activity_at}</td>
          <td>${deleteBtn}</td>
        </tr>`;
    }).join('');

  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:#EF4444;padding:24px;">❌ Ошибка загрузки: ${err.message}</td></tr>`;
    console.error('Channel effectiveness load error:', err);
  }
}

async function deleteDeadChannel(channelId, channelName) {
  if (!confirm(`🗑 Удалить канал «${channelName}» из мониторинга?\n\nЭто действие необратимо. Канал перестанет сканироваться.`)) return;
  try {
    const res = await fetch(`/api/channels/${channelId}/dead`, { method: 'DELETE' });
    const data = await res.json();
    if (data.status === 'deleted') {
      alert(`✅ Канал «${channelName}» удалён из пула мониторинга.`);
      loadChannelEffectiveness(); // Refresh table
      loadChannels();             // Refresh main channels table too
    } else {
      alert(`❌ ${data.message || 'Ошибка удаления'}`);
    }
  } catch (err) {
    alert('❌ Ошибка сети при удалении канала');
  }
}

