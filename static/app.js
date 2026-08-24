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

  // Polling for live stream, AI logs, stats, and collector logs in real time
  setInterval(fetchLiveStream, 3000);
  setInterval(fetchAIEvaluationLogs, 4000);
  setInterval(fetchStats, 6000);
  setInterval(fetchCollectorLogs, 5000);
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
    webApp.enableClosingConfirmation();
    if (typeof webApp.disableVerticalSwipes === 'function') {
      webApp.disableVerticalSwipes();
    }
    webApp.isVerticalSwipesEnabled = false;
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

  const mobilePills = document.querySelectorAll('.mobile-nav-pill');
  mobilePills.forEach(pill => {
    pill.addEventListener('click', () => {
      const tabName = pill.getAttribute('data-tab');
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

  document.querySelectorAll('.mobile-nav-pill').forEach(pill => {
    const isActive = pill.getAttribute('data-tab') === tabName;
    pill.classList.toggle('active', isActive);
    if (isActive && typeof pill.scrollIntoView === 'function') {
      try {
        pill.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      } catch (e) {}
    }
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
    b2b_outreach: { title: '🚀 B2B Аутрич Аудитория', sub: 'База потенциальных B2B-клиентов с историями объявлений для собственного аутрича' },
    ailogs: { title: 'Логи ИИ-Анализатора', sub: 'Пошаговая логика и комментарии ИИ по каждому отсканированному сообщению' }
  };

  if (titles[tabName]) {
    document.getElementById('page-title').textContent = titles[tabName].title;
    document.getElementById('page-subtitle').textContent = titles[tabName].sub;
  }

  if (tabName === 'livestream') fetchLiveStream();
  if (tabName === 'channels') { loadChannels(); loadChannelCandidates(); }
  if (tabName === 'rubrics') fetchRubrics();
  if (tabName === 'partners') fetchPartners();
  if (tabName === 'b2b_outreach') { loadB2BOutreachLeads(); loadOutreachEmployees(); loadB2BDialogues(); }
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
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
              <span style="font-weight:700; font-size:14px; color:#1E293B;">👤 ${escapeHtml(log.first_name)} (${escapeHtml(log.username)})</span>
              <button onclick="navigateToChannel('${escapeHtml(log.chat_title)}')" 
                      title="Нажмите, чтобы перейти к этому каналу в списке чатов"
                      style="font-size:12px; color:#2563EB; background:#EFF6FF; padding:3px 10px; border-radius:6px; border:1px solid #BFDBFE; cursor:pointer; font-weight:600; display:inline-flex; align-items:center; gap:4px; transition:all 0.15s ease;"
                      onmouseover="this.style.background='#DBEAFE';" onmouseout="this.style.background='#EFF6FF';">
                📍 ${escapeHtml(log.chat_title)} ↗️
              </button>
              <button class="btn-danger-sm" 
                      onclick="deleteChannelFromLog('${log.channel_id || ''}', '${escapeHtml(log.chat_title)}', '', this)" 
                      title="Удалить этот канал из прослушки"
                      style="padding:2px 8px; font-size:11px; margin-left:4px; border-radius:6px;">
                🗑️ Удалить канал
              </button>
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
    fetchAIEvaluationLogs(),
    fetchCollectorLogs()
  ]);
}

function scrollToCollectorLogs() {
  switchTab('overview');
  const sec = document.getElementById('collector-telemetry-section');
  if (sec) {
    sec.scrollIntoView({ behavior: 'smooth' });
  }
}

async function fetchCollectorLogs() {
  const container = document.getElementById('collector-feed-container');
  if (!container) return;

  try {
    const res = await fetch('/api/collector-logs?limit=100');
    if (!res.ok) return;
    const data = await res.json();

    const sumChecks = document.getElementById('collector-summary-checks');
    const sumSeen = document.getElementById('collector-summary-seen');
    const sumMsgs = document.getElementById('collector-summary-msgs');
    const sumLeads = document.getElementById('collector-summary-leads');

    if (sumChecks && data.summary) sumChecks.textContent = data.summary.checks_1h || 0;
    if (sumSeen && data.summary) sumSeen.textContent = data.summary.posts_seen_1h !== undefined ? data.summary.posts_seen_1h : 0;
    if (sumMsgs && data.summary) sumMsgs.textContent = data.summary.new_messages_1h || 0;
    if (sumLeads && data.summary) sumLeads.textContent = data.summary.new_leads_1h || 0;

    const logs = data.logs || [];
    if (logs.length === 0) {
      container.innerHTML = `
        <div style="padding: 24px; text-align: center; color: #94A3B8; font-size: 13px;">
          ⏳ Ожидание первого цикла опроса сборщика (каждые 25 секунд)...
        </div>
      `;
      return;
    }

    container.innerHTML = logs.map(log => {
      const isFailed = log.status === 'FAILED';
      const hasMsgs = log.new_messages_count > 0;
      const fetchedCount = log.total_fetched_count || 0;
      
      let statusBadge = '';
      if (isFailed) {
        statusBadge = `<span class="badge" style="background: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; font-weight: 700;">🔴 Ошибка</span>`;
      } else if (hasMsgs) {
        statusBadge = `<span class="badge" style="background: #DCFCE7; color: #15803D; border: 1px solid #86EFAC; font-weight: 700;" title="Новых: ${log.new_messages_count}, просмотрено постов: ${fetchedCount}">📩 +${log.new_messages_count}/${fetchedCount || 20}</span>`;
      } else if (fetchedCount > 0) {
        statusBadge = `<span class="badge" style="background: #F1F5F9; color: #475569; border: 1px solid #CBD5E1; font-weight: 600;" title="Новых: 0, просмотрено постов: ${fetchedCount}">🟢 0/${fetchedCount}</span>`;
      } else {
        statusBadge = `<span class="badge" style="background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE; font-weight: 600;" title="Групповой чат: считывается в фоновом режиме через Pyrogram MTProto Юзербот">💬 Группа (Юзербот)</span>`;
      }

      const leadBadge = log.new_leads_count > 0
        ? `<span class="badge" style="background: #FEF3C7; color: #D97706; border: 1px solid #FCD34D;">🔥 +${log.new_leads_count} лидов</span>`
        : '';

      let tgUrl = log.username_or_link || '';
      if (tgUrl && !tgUrl.startsWith('http')) {
        const cleanUser = tgUrl.replace('@', '').trim();
        tgUrl = `https://t.me/${cleanUser}`;
      }

      return `
        <div style="background: #FFF; border: 1px solid ${isFailed ? '#FCA5A5' : (hasMsgs ? '#86EFAC' : '#E2E8F0')}; border-radius: 8px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: 13px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
          <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex: 1;">
            <span style="font-size: 12px; font-weight: 700; color: #64748B; font-family: monospace;">⏱ ${escapeHtml(log.created_at_fmt)}</span>
            <button onclick="navigateToChannel('${escapeHtml(log.chat_title)}')" 
                    title="Перейти к этому каналу в списке чатов"
                    style="font-weight: 700; color: #1E293B; background: #F8FAFC; border: 1px solid #CBD5E1; padding: 2px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; transition: all 0.15s ease;"
                    onmouseover="this.style.background='#E2E8F0';" onmouseout="this.style.background='#F8FAFC';">
              📍 ${escapeHtml(log.chat_title)} ↗️
            </button>
            ${tgUrl ? `<a href="${escapeHtml(tgUrl)}" target="_blank" rel="noopener" style="color: #3B82F6; text-decoration: none; font-size: 12px;" aria-label="Открыть в Telegram">↗️ TG</a>` : ''}
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            ${statusBadge}
            ${leadBadge}
            <button class="btn-danger-sm" 
                    style="padding: 3px 8px; font-size: 11px; margin-left: 4px; border-radius: 6px; background: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; cursor: pointer;" 
                    onclick="deleteChannelFromLog('${log.channel_id || ''}', '${escapeHtml(log.chat_title)}', '${escapeHtml(log.username_or_link || '')}', this)" 
                    title="Удалить этот чат из прослушки">
              🗑️ Удалить
            </button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching collector logs:', err);
  }
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
    const sSub = document.getElementById('stat-scanned-subtext');
    if (s1h) {
      const h1 = stats.scanned_1h !== undefined ? stats.scanned_1h : 0;
      const pass = stats.scanned_pass !== undefined ? stats.scanned_pass : 0;
      const lastCheck = stats.userbot_info ? stats.userbot_info.last_check_at : '—';
      s1h.textContent = pass > 0 ? `${pass} новых` : `${h1} просмотрено`;
      if (sSub) {
        sSub.textContent = `🟢 Сканер активен • Опрос: ${lastCheck}`;
      }
    }

    // Update sidebar system status element
    const statusFooter = document.querySelector('.system-status');
    if (statusFooter && stats.userbot_info) {
      const uInfo = stats.userbot_info;
      const isConn = uInfo.is_connected;
      const label = isConn ? '⚡ Юзербот онлайн & сканирует' : '🟢 Веб-сканер активен (25с)';
      statusFooter.innerHTML = `
        <span class="status-dot" style="background: ${isConn ? '#10B981' : '#0EA5E9'};"></span>
        <span title="Режим: ${escapeHtml(uInfo.mode)} | Посл. опрос: ${escapeHtml(uInfo.last_check_at)}">${label}</span>
      `;
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
    const ttlMins = lead.ttl_remaining_minutes != null ? lead.ttl_remaining_minutes : 180;
    const ttlHrs = Math.floor(ttlMins / 60);
    const ttlRemMins = ttlMins % 60;
    const ttlBadge = lead.is_archived
      ? `<span style="background: #F1F5F9; color: #64748B; font-size: 11px; font-weight: 600; padding: 2px 6px; border-radius: 4px; border: 1px solid #CBD5E1;">📦 Архив</span>`
      : `<span style="background: #FFFBEB; color: #B45309; font-size: 11px; font-weight: 600; padding: 2px 6px; border-radius: 4px; border: 1px solid #FDE68A;" title="Через ${ttlMins} мин лид будет перенесен в архив">⏳ ${ttlHrs > 0 ? ttlHrs + 'ч ' : ''}${ttlRemMins}м</span>`;

    return `
      <div class="lead-item-card">
        <div>
          <div class="lead-header">
            <span class="niche-badge">🏷️ ${escapeHtml(rubricLabel)}</span>
            <span class="location-badge" style="background: #F3F4F6; color: #374151; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 6px; border: 1px solid #E5E7EB; white-space: nowrap;">📍 ${escapeHtml(locBadge)}</span>
            <span class="temp-badge ${lead.temperature}">${lead.temperature === 'HOT' ? '🔥 HOT' : '⚡ WARM'}</span>
            ${ttlBadge}
          </div>

          <div class="lead-summary">
            "${escapeHtml(lead.intent_summary)}"
          </div>

          <div class="ai-reasoning-card-box" style="font-size: 12.5px; color: #047857; margin-top: 10px; padding: 10px 12px; background: #ECFDF5; border: 1px solid #A7F3D0; border-left: 4px solid #10B981; border-radius: 8px; line-height: 1.45;">
            <div style="font-weight: 700; color: #065F46; margin-bottom: 4px; display: flex; align-items: center; gap: 6px;">
              🧠 <span>Рассуждения и аргументация ИИ:</span>
            </div>
            <div>${escapeHtml(lead.reasoning || lead.sales_hook || 'Квалифицирован ИИ как клиентский покупательский запрос.')}</div>
          </div>

          <div style="font-size: 13px; color: #4B5563; margin-top: 10px; padding: 8px 12px; background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
            <span>💬 Всего сообщений пользователя в системе: <strong>${lead.user_message_count || 1}</strong></span>
            <button class="btn-primary" style="padding: 4px 10px; font-size: 11px;" onclick="openDecryptModal(${lead.user_id})">📜 История сообщений</button>
          </div>

          <div class="admin-lead-actions" style="margin-top: 10px; padding-top: 8px; border-top: 1px dashed #E2E8F0; display: flex; gap: 6px; flex-wrap: wrap;">
            <button class="btn-primary" style="padding: 4px 10px; font-size: 11px; background: linear-gradient(135deg, #6366F1, #4F46E5);" onclick="openLeadAnalysisModal('${lead.id}')">
              🔬 ИИ-Анализ
            </button>
            <button class="btn-primary" style="padding: 4px 10px; font-size: 11px; background: linear-gradient(135deg, #0EA5E9, #0284C7);" onclick="requalifyLead('${lead.id}', this)">
              🔄 Переквалифицировать
            </button>
            <button class="btn-danger-sm" style="padding: 4px 10px; font-size: 11px;" onclick="deleteLeadAdmin('${lead.id}', this)">
              🗑️ Удалить
            </button>
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

async function openLeadAnalysisModal(leadId) {
  let modal = document.getElementById('lead-analysis-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'lead-analysis-modal';
    modal.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.65); backdrop-filter:blur(6px); z-index:99999; display:none; align-items:center; justify-content:center; padding:20px;';
    modal.innerHTML = `
      <div style="background:#FFF; border-radius:16px; width:100%; max-width:680px; max-height:85vh; display:flex; flex-direction:column; padding:24px; box-shadow:0 20px 50px rgba(0,0,0,0.3); font-family:inherit;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; border-bottom:1px solid #E5E7EB; padding-bottom:12px;">
          <h3 style="margin:0; font-size:18px; color:#1F2937; display:flex; align-items:center; gap:8px;">🔬 Детальный ИИ-Анализ и Квалификация Лида</h3>
          <button onclick="document.getElementById('lead-analysis-modal').style.display='none'" style="background:none; border:none; font-size:22px; cursor:pointer; color:#6B7280;">✕</button>
        </div>
        <div id="lead-analysis-modal-body" style="overflow-y:auto; flex:1; padding-right:6px;"></div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  const body = document.getElementById('lead-analysis-modal-body');
  body.innerHTML = '<div style="text-align:center; padding: 40px; color:#6B7280;">⏳ Загрузка результатов ИИ-Анализатора...</div>';
  modal.style.display = 'flex';

  try {
    const res = await fetch(`/api/leads/${leadId}/analysis`);
    const data = await res.json();
    if (!res.ok || data.status === 'error') {
      body.innerHTML = `<div style="color:#EF4444; padding: 30px; text-align:center;">❌ ${data.message || 'Ошибка загрузки анализа'}</div>`;
      return;
    }

    const confidencePct = Math.round((data.confidence_score || 0.85) * 100);

    body.innerHTML = `
      <div style="display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap;">
        <span style="background:#EEF2FF; color:#4338CA; border:1px solid #C7D2FE; font-weight:700; padding:4px 10px; border-radius:8px; font-size:12px;">🏷️ Ниша: ${escapeHtml(data.rubric_name)}</span>
        <span style="background:#F3F4F6; color:#374151; border:1px solid #E5E7EB; font-weight:600; padding:4px 10px; border-radius:8px; font-size:12px;">📍 Локация: ${escapeHtml(data.location_name)}</span>
        <span style="background:${data.temperature === 'HOT' ? '#DCFCE7' : '#FEF3C7'}; color:${data.temperature === 'HOT' ? '#15803D' : '#D97706'}; font-weight:800; padding:4px 10px; border-radius:8px; font-size:12px;">${data.temperature === 'HOT' ? '🔥 HOT' : '⚡ WARM'} (${confidencePct}% Уверенность)</span>
      </div>

      <div style="background:#F8FAFC; border-left:4px solid #3B82F6; padding:12px 16px; border-radius:8px; margin-bottom:16px;">
        <div style="font-size:12px; font-weight:700; color:#64748B; margin-bottom:4px;">💬 Прямой цитируемый запрос клиента:</div>
        <div style="font-size:14px; font-weight:600; color:#1E293B;">"${escapeHtml(data.intent_summary)}"</div>
      </div>

      <div style="background:#EEF2FF; border:1px solid #C7D2FE; border-radius:10px; padding:14px; margin-bottom:16px;">
        <div style="font-size:13px; font-weight:800; color:#3730A3; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
          💡 Цепочка рассуждений ИИ (Chain-of-Thought):
        </div>
        <div style="font-size:13px; color:#312E81; line-height:1.5;">${escapeHtml(data.reasoning)}</div>
      </div>

      ${data.sales_hook ? `
        <div style="background:#F0FDF4; border:1px solid #BBF7D0; border-radius:10px; padding:12px 14px; margin-bottom:16px;">
          <div style="font-size:12px; font-weight:700; color:#166534; margin-bottom:4px;">🎯 Рекомендация для менеджера продаж (Sales Hook):</div>
          <div style="font-size:13px; color:#14532D;">«${escapeHtml(data.sales_hook)}»</div>
        </div>
      ` : ''}

      <div style="margin-top:20px;">
        <div style="font-size:13px; font-weight:700; color:#374151; margin-bottom:10px;">📜 Сырая история сообщений диалога (${data.raw_messages ? data.raw_messages.length : 0}):</div>
        <div style="display:flex; flex-direction:column; gap:8px; max-height:220px; overflow-y:auto; border:1px solid #E5E7EB; border-radius:10px; padding:10px; background:#FAFAFA;">
          ${(data.raw_messages || []).map((m, i) => `
            <div style="background:#FFF; border:1px solid #E5E7EB; border-radius:8px; padding:8px 12px; font-size:13px;">
              <div style="display:flex; justify-content:space-between; font-size:11px; color:#6B7280; margin-bottom:4px;">
                <strong>${i+1}. 📍 ${escapeHtml(m.chat_title)}</strong>
                <span>⏱ ${escapeHtml(m.timestamp)}</span>
              </div>
              <div style="color:#1F2937;">"${escapeHtml(m.message_text)}"</div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  } catch (err) {
    body.innerHTML = `<div style="color:#EF4444; padding: 20px; text-align:center;">❌ Ошибка сети: ${err.message}</div>`;
  }
}

async function requalifyLead(leadId, btn) {
  if (!confirm('🤖 Запустить моментальную повторную квалификацию этого лида через нейросеть ИИ (Groq/Gemini)?')) return;

  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ ИИ квалифицирует...';

  try {
    const res = await fetch(`/api/leads/${leadId}/requalify`, { method: 'POST' });
    const data = await res.json();

    if (res.ok && data.status === 'requalified') {
      showToast(`✅ Лид успешно переквалифицирован ИИ!\nНиша: ${data.rubric_name} (${Math.round(data.confidence_score * 100)}%)`, 'success');
      fetchAllData();
      if (typeof fetchAIEvaluationLogs === 'function') fetchAIEvaluationLogs();
    } else if (res.ok && data.status === 'rejected') {
      showToast(`ℹ️ ИИ определил запрос как НЕ ЛИД (удален из списка).\nАргументация: ${data.reasoning}`, 'info', 5000);
      fetchAllData();
      if (typeof fetchAIEvaluationLogs === 'function') fetchAIEvaluationLogs();
    } else {
      showToast(`❌ ${data.message || 'Ошибка переквалификации'}`, 'error');
      btn.disabled = false;
      btn.textContent = originalText;
    }
  } catch (err) {
    console.error('Error requalifying lead:', err);
    showToast('❌ Ошибка сети при запросе к ИИ', 'error');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function deleteLeadAdmin(leadId, btn) {
  if (!confirm('🗑️ Вы уверены, что хотите навсегда удалить этот лид из системы?')) return;

  btn.disabled = true;
  btn.textContent = '⏳ Удаление...';

  try {
    const res = await fetch(`/api/leads/${leadId}`, { method: 'DELETE' });
    const data = await res.json();

    if (res.ok && data.status === 'deleted') {
      showToast('✅ Лид успешно удален из системы', 'success');
      fetchAllData();
    } else {
      showToast(`❌ ${data.message || 'Ошибка удаления'}`, 'error');
      btn.disabled = false;
      btn.textContent = '🗑️ Удалить';
    }
  } catch (err) {
    console.error('Error deleting lead:', err);
    showToast('❌ Ошибка сети при удалении', 'error');
    btn.disabled = false;
    btn.textContent = '🗑️ Удалить';
  }
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
          <h3 style="margin:0; font-size:18px; color:#1F2937;">📜 История сообщений пользователя</h3>
          <button onclick="document.getElementById('decrypt-modal').style.display='none'" style="background:none; border:none; font-size:22px; cursor:pointer; color:#6B7280;">✕</button>
        </div>
        <div id="decrypt-modal-body" style="overflow-y:auto; flex:1; padding-right:6px;"></div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  const body = document.getElementById('decrypt-modal-body');
  body.innerHTML = '<div style="text-align:center; padding: 30px; color:#6B7280;">⏳ Загрузка истории сообщений...</div>';
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
        ? `<span class="temp-badge HOT" style="background:#DCFCE7; color:#15803D; border:1px solid #86EFAC; font-weight:700; padding:3px 8px; border-radius:6px; font-size:12px;">🔥 ЛИД [${escapeHtml(item.niche_code || 'ГОРЯЧИЙ')}]</span>`
        : `<span class="temp-badge WARM" style="background:#F1F5F9; color:#64748B; border:1px solid #CBD5E1; font-weight:600; padding:3px 8px; border-radius:6px; font-size:12px;">❌ НЕ ЛИД</span>`;

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
              <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                <button onclick="navigateToChannel('${escapeHtml(item.chat_title)}')" 
                        title="Нажмите, чтобы перейти к этому каналу в списке чатов"
                        style="font-weight: 700; color: #1F2937; background: #F3F4F6; border: 1px solid #E5E7EB; padding: 2px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; transition: all 0.15s ease;"
                        onmouseover="this.style.background='#E5E7EB';" onmouseout="this.style.background='#F3F4F6';">
                  📍 ${escapeHtml(item.chat_title)} ↗️
                </button>
                ${tgUrl ? `
                  <a href="${escapeHtml(tgUrl)}" target="_blank" rel="noopener noreferrer" title="Открыть в Telegram" style="text-decoration: none; color: #3B82F6; font-size: 12px; opacity: 0.85;" aria-label="Открыть чат в Telegram">
                    ↗️ TG
                  </a>
                ` : ''}
                <button class="btn-danger-sm" 
                        style="padding: 2px 6px; font-size: 11px; margin-left: 4px; border-radius: 6px;" 
                        onclick="deleteChannelFromLog('${item.channel_id || ''}', '${escapeHtml(item.chat_title)}', '${escapeHtml(item.channel_link || '')}', this)" 
                        title="Удалить этот канал из прослушки">
                  🗑️ Удалить
                </button>
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

let channelsDataCache = [];
let channelsVisibleCount = 10;

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

    channelsDataCache = channels || [];
    channelsVisibleCount = 10; // Reset to 10 on filter change
    renderChannelsTable();
  } catch (err) {
    console.error('Error loading channels:', err);
  }
}

function showMoreChannels(delta = 10) {
  channelsVisibleCount += delta;
  renderChannelsTable();
}

function showAllChannels() {
  channelsVisibleCount = channelsDataCache.length;
  renderChannelsTable();
}

function collapseChannels() {
  channelsVisibleCount = 10;
  renderChannelsTable();
}

let chSortField = 'days_idle';
let chSortAsc = true;

function sortChannelsTable(field) {
  if (chSortField === field) {
    chSortAsc = !chSortAsc;
  } else {
    chSortField = field;
    chSortAsc = (field === 'title' || field === 'status' || field === 'location' || field === 'niche');
  }
  renderChannelsTable();
}

function resetChannelFilters() {
  const locSel = document.getElementById('filter-channel-location');
  const nicheSel = document.getElementById('filter-channel-niche');
  const queryInp = document.getElementById('filter-channel-query');
  if (locSel) locSel.value = 'all';
  if (nicheSel) nicheSel.value = 'all';
  if (queryInp) queryInp.value = '';
  loadChannels();
  showToast('⚡ Фильтры сброшены — показаны все каналы!', 'info');
}

function renderChannelsTable() {
  const tbody = document.getElementById('channels-table-body');
  const badgeEl = document.getElementById('channels-count-badge');
  const pagEl = document.getElementById('channels-pagination-container');
  if (!tbody) return;

  let sorted = [...channelsDataCache];

  sorted.sort((a, b) => {
    let va, vb;
    if (chSortField === 'status') {
      va = a.days_idle != null ? a.days_idle : 999;
      vb = b.days_idle != null ? b.days_idle : 999;
    } else if (chSortField === 'title') {
      va = (a.title || a.username_or_link || '').toLowerCase();
      vb = (b.title || b.username_or_link || '').toLowerCase();
    } else if (chSortField === 'location') {
      va = (a.location_code || '').toLowerCase();
      vb = (b.location_code || '').toLowerCase();
    } else if (chSortField === 'niche') {
      va = (a.niche_code || '').toLowerCase();
      vb = (b.niche_code || '').toLowerCase();
    } else if (chSortField === 'msgs_7d') {
      va = a.msgs_7d || 0;
      vb = b.msgs_7d || 0;
    } else if (chSortField === 'leads_7d') {
      va = a.leads_7d || 0;
      vb = b.leads_7d || 0;
    } else if (chSortField === 'leads_total') {
      va = a.leads_total || 0;
      vb = b.leads_total || 0;
    } else if (chSortField === 'last_activity') {
      va = a.last_scraped_at || '';
      vb = b.last_scraped_at || '';
    } else {
      va = a.days_idle != null ? a.days_idle : 999;
      vb = b.days_idle != null ? b.days_idle : 999;
    }

    if (va < vb) return chSortAsc ? -1 : 1;
    if (va > vb) return chSortAsc ? 1 : -1;
    return 0;
  });

  ['status', 'title', 'location', 'niche', 'msgs_7d', 'leads_7d', 'leads_total', 'last_activity'].forEach(f => {
    const iconEl = document.getElementById(`ch-sort-icon-${f}`);
    if (iconEl) {
      if (f === chSortField) {
        iconEl.textContent = chSortAsc ? '▲' : '▼';
        iconEl.style.color = '#6366F1';
      } else {
        iconEl.textContent = '⇅';
        iconEl.style.color = '#94A3B8';
      }
    }
  });

  const total = sorted.length;
  const visibleChannels = sorted.slice(0, channelsVisibleCount);

  if (badgeEl) {
    badgeEl.textContent = `Показано ${Math.min(channelsVisibleCount, total)} из ${total}`;
  }

  if (total === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="9" style="text-align: center; color: var(--text-muted); padding: 32px;">
          По заданным фильтрам чатов не найдено.
        </td>
      </tr>
    `;
    if (pagEl) pagEl.innerHTML = '';
    return;
  }

  const rubricsList = window.cachedRubrics || [];

  tbody.innerHTML = visibleChannels.map(ch => {
    let tgUrl = ch.username_or_link || '';
    if (tgUrl && !tgUrl.startsWith('http')) {
      const cleanUser = tgUrl.replace('@', '').trim();
      tgUrl = `https://t.me/${cleanUser}`;
    }

    let nicheOpts = `<option value="community" ${ch.niche_code === 'community' ? 'selected' : ''}>💬 Сообщество</option>`;
    if (rubricsList.length > 0) {
      nicheOpts = rubricsList.map(r => `
        <option value="${r.code}" ${r.code === ch.niche_code ? 'selected' : ''}>${r.icon || '🏷️'} ${escapeHtml(r.name)}</option>
      `).join('');
    }

    const badgeEmoji = ch.color_emoji || (ch.status === 'JOINED' ? '🟢' : '⏳');
    const badgeClass = ch.color_class || 'eff-fresh';
    const badgeLabel = ch.color_label || (ch.status === 'JOINED' ? 'Активный' : 'Подключение');

    return `
      <tr>
        <td><span class="eff-badge ${badgeClass}">${badgeEmoji} ${badgeLabel}</span></td>
        <td>
          <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
            <strong>${escapeHtml(ch.title || ch.username_or_link)}</strong>
            ${tgUrl ? `
              <a href="${escapeHtml(tgUrl)}" target="_blank" rel="noopener noreferrer" title="Открыть в Telegram" style="text-decoration: none; color: #3B82F6; font-size: 13px; display: inline-flex; align-items: center;">
                ↗️
              </a>
            ` : ''}
          </div>
          <small style="color: var(--text-muted); display: block; margin-top: 2px;">${escapeHtml(ch.username_or_link)}</small>
        </td>
        <td>
          <select class="form-select-sm" 
                  style="padding: 3px 6px; font-size: 12px; border-radius: 6px; border: 1px solid #D1D5DB; background: #F9FAFB; cursor: pointer; color: #1F2937; font-weight: 500; max-width: 125px;"
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
        <td>
          <select class="form-select-sm"
                  style="padding: 3px 6px; font-size: 12px; border-radius: 6px; border: 1px solid #D1D5DB; background: #F9FAFB; cursor: pointer; color: #1F2937; font-weight: 500; max-width: 140px;"
                  onchange="updateChannelNiche('${ch.id}', this.value)"
                  title="Изменить нишу канала">
            ${nicheOpts}
          </select>
        </td>
        <td><strong>${ch.msgs_7d || 0}</strong></td>
        <td><strong style="color: #059669;">${ch.leads_7d || 0}</strong></td>
        <td><strong style="color: #4F46E5;">${ch.leads_total || 0}</strong></td>
        <td><span style="font-size: 12px; color: #4B5563; font-weight: 600; background: #F3F4F6; padding: 2px 8px; border-radius: 6px; border: 1px solid #E5E7EB; white-space: nowrap;">⏱️ ${escapeHtml(ch.last_scraped_fmt || '—')}</span></td>
        <td>
          <div style="display: flex; gap: 6px;">
            <button class="btn-secondary-sm" onclick="openChannelPostsModal('${ch.id}', '${escapeHtml(ch.title || ch.username_or_link)}')" style="font-size:11px; padding:3px 8px; border-radius:6px; background:#EEF2FF; color:#4F46E5; border:1px solid #C7D2FE; font-weight:600; cursor:pointer;" title="Просмотреть ленту постов">📜 Посты</button>
            <button class="btn-danger-sm" style="font-size:11px; padding:3px 8px;" onclick="deleteChannelFromLog('${ch.id}', '${escapeHtml(ch.title)}', '${escapeHtml(ch.username_or_link)}', this)">🗑️ Удалить</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  if (pagEl) {
    const hasMore = channelsVisibleCount < total;
    const remaining = total - channelsVisibleCount;
    const isExpanded = channelsVisibleCount > 10;

    let btnsHtml = '';
    if (hasMore) {
      btnsHtml += `
        <button class="btn-primary" style="background: linear-gradient(135deg, #4F46E5, #3730A3); font-size: 12px; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; border: none; color: white;" onclick="showMoreChannels(10)">
          ⬇️ Показать еще 10 чатов (осталось ${remaining})
        </button>
        <button class="btn-secondary-sm" style="background: #F1F5F9; color: #475569; border: 1px solid #CBD5E1; font-size: 12px; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-weight: 600;" onclick="showAllChannels()">
          🚀 Показать все (${total})
        </button>
      `;
    }
    if (isExpanded) {
      btnsHtml += `
        <button class="btn-secondary-sm" style="background: #FEF2F2; color: #991B1B; border: 1px solid #FCA5A5; font-size: 12px; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-weight: 600;" onclick="collapseChannels()">
          ⬆️ Свернуть до 10
        </button>
      `;
    }

    pagEl.innerHTML = `
      <div style="font-size: 13px; color: #64748B; font-weight: 500;">
        Отображается <strong>${Math.min(channelsVisibleCount, total)}</strong> из <strong>${total}</strong> отслеживаемых чатов
      </div>
      <div style="display: flex; gap: 8px; flex-wrap: wrap;">
        ${btnsHtml}
      </div>
    `;
  }
}

async function openChannelPostsModal(channelId, title) {
  const modal = document.getElementById('modal-channel-posts');
  const modalTitle = document.getElementById('channel-posts-modal-title');
  const feed = document.getElementById('channel-posts-feed-container');

  if (!modal || !feed) return;

  modalTitle.textContent = `📜 Лента постов: ${title} (по убыванию)`;
  feed.innerHTML = '<div style="text-align:center; padding: 40px; color:#64748B;">⏳ Загрузка сообщений и результатов квалификации ИИ...</div>';
  modal.style.display = 'flex';

  try {
    const res = await fetch(`/api/channels/${channelId}/messages?limit=30`);
    if (!res.ok) {
      feed.innerHTML = `<div style="text-align:center; padding: 40px; color:#EF4444;">⚠️ Ошибка загрузки сообщений (HTTP ${res.status}).</div>`;
      return;
    }
    const data = await res.json();
    const messages = data.messages || [];

    if (messages.length === 0) {
      feed.innerHTML = '<div style="text-align:center; padding: 40px; color:#94A3B8;">Сообщения в данном канале еще не зафиксированы.</div>';
      return;
    }

    feed.innerHTML = messages.map(msg => {
      let badgeHtml = '';
      if (msg.status_badge === 'LEAD' || msg.is_lead) {
        badgeHtml = `<span style="background:#DCFCE7; color:#15803D; border:1px solid #86EFAC; font-size:12px; font-weight:700; padding:3px 9px; border-radius:6px;">🔥 ЛИД (${Math.round((msg.confidence_score || 0.95)*100)}%)</span>`;
      } else if (msg.status_badge === 'SELLER') {
        badgeHtml = `<span style="background:#F3E8FF; color:#7E22CE; border:1px solid #D8B4FE; font-size:12px; font-weight:700; padding:3px 9px; border-radius:6px;">💼 B2B ПРОДАВЕЦ</span>`;
      } else {
        badgeHtml = `<span style="background:#F1F5F9; color:#64748B; border:1px solid #CBD5E1; font-size:12px; font-weight:600; padding:3px 9px; border-radius:6px;">❌ НЕ ЛИД / ФЛУД</span>`;
      }

      return `
        <div style="background:#FFF; border:1px solid #E2E8F0; border-radius:10px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; flex-wrap:wrap; gap:6px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-weight:700; font-size:13px; color:#1E293B;">👤 ${escapeHtml(msg.first_name || 'Участник')} (@${escapeHtml(msg.username || 'anon')})</span>
              ${msg.source === 'LIVE_WEB_PREVIEW' ? '<span style="font-size:10px; background:#FEF3C7; color:#B45309; padding:1px 6px; border-radius:4px; font-weight:600;">⚡ Веб-превью</span>' : ''}
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:11px; color:#94A3B8;">⏱ ${escapeHtml(msg.created_at)}</span>
              ${badgeHtml}
            </div>
          </div>

          <div style="background:#F8FAFC; border-left:3px solid ${msg.is_lead ? '#10B981' : (msg.status_badge==='SELLER' ? '#A855F7' : '#94A3B8')}; padding:10px 12px; border-radius:6px; font-size:13px; color:#1E293B; margin-bottom:8px; line-height:1.4;">
            💬 "${escapeHtml(msg.message_text)}"
          </div>

          <div style="background:#EEF2FF; border:1px solid #C7D2FE; border-radius:6px; padding:8px 12px; font-size:12px; color:#3730A3;">
            💡 <strong>Квалификация ИИ (CoT):</strong> ${escapeHtml(msg.reasoning)}
          </div>
        </div>
      `;
    }).join('');

  } catch (err) {
    console.error('Error opening channel posts modal:', err);
    feed.innerHTML = '<div style="text-align:center; padding: 40px; color:#EF4444;">⚠️ Ошибка загрузки постов канала.</div>';
  }
}

function closeChannelPostsModal() {
  const modal = document.getElementById('modal-channel-posts');
  if (modal) modal.style.display = 'none';
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
    window.cachedRubrics = rubrics;

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
  const outreachNicheSel = document.getElementById('filter-outreach-niche');
  const profileContainer = document.getElementById('profile-rubrics-checkboxes');

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

  if (outreachNicheSel) {
    const currentVal = outreachNicheSel.value;
    outreachNicheSel.innerHTML = `<option value="all">Все ниши</option>` + rubrics.map(r => `
      <option value="${r.code}">${r.icon || '🏷️'} ${escapeHtml(r.name)}</option>
    `).join('');
    outreachNicheSel.value = currentVal || 'all';
  }

  if (profileContainer && rubrics) {
    profileContainer.innerHTML = rubrics.map(r => `
      <label style="display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; background: #F8FAFC; border: 1px solid #E2E8F0; padding: 5px 10px; border-radius: 6px; font-weight: 500;">
        <input type="checkbox" name="user_rubrics" value="${r.code}" checked> ${r.icon || '🏷️'} ${escapeHtml(r.name)}
      </label>
    `).join('');
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

function navigateToChannel(chatQuery) {
  if (!chatQuery) return;
  switchTab('channels');
  const queryInp = document.getElementById('filter-channel-query');
  if (queryInp) {
    queryInp.value = chatQuery;
  }
  loadChannels();
  showToast(`🔍 Фильтр по каналу "${chatQuery}" применен`, 'info');
}

async function deleteChannelFromLog(channelId, chatTitle, usernameOrLink, btn) {
  const targetName = chatTitle || usernameOrLink || 'этот чат';
  if (!confirm(`🗑️ Вы уверены, что хотите полностью удалить чат "${targetName}" из списка прослушки?`)) return;

  let origText = '';
  if (btn) {
    origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Удаление...';
  }

  try {
    let url = `/api/channels/${channelId || 'by-target'}`;
    const queryTarget = usernameOrLink || chatTitle;
    if (queryTarget) {
      url += `?target=${encodeURIComponent(queryTarget)}`;
    }

    const res = await fetch(url, { method: 'DELETE' });
    const data = await res.json();

    if (res.ok && data.status === 'deleted') {
      showToast(`✅ Чат "${targetName}" успешно удален из прослушки!`, 'success');
      fetchAllData();
    } else {
      showToast(`❌ ${data.message || 'Не удалось удалить чат'}`, 'error');
      if (btn) {
        btn.disabled = false;
        btn.textContent = origText || '🗑️ Удалить';
      }
    }
  } catch (err) {
    console.error('Error deleting channel from log:', err);
    showToast('❌ Ошибка сети при удалении чата', 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText || '🗑️ Удалить';
    }
  }
}

async function deleteChannel(channelId) {
  if (!confirm('Вы уверены, что хотите удалить этот чат из прослушки?')) return;

  try {
    const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('✅ Канал успешно удален!', 'success');
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
let effChannelsData = [];
let effSortField = 'days_idle';
let effSortAsc = true;

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
      effChannelsData = [];
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#94A3B8;padding:24px;">Каналы не найдены</td></tr>';
      return;
    }

    effChannelsData = channels;

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

    renderEffectivenessTable();

  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:#EF4444;padding:24px;">❌ Ошибка загрузки: ${err.message}</td></tr>`;
    console.error('Channel effectiveness load error:', err);
  }
}

function sortEffectivenessTable(field) {
  if (effSortField === field) {
    effSortAsc = !effSortAsc;
  } else {
    effSortField = field;
    // For numeric metrics, default first click to descending (highest first)
    if (['msgs_7d', 'leads_7d', 'leads_total'].includes(field)) {
      effSortAsc = false;
    } else {
      effSortAsc = true;
    }
  }
  renderEffectivenessTable();
}

function renderEffectivenessTable() {
  const tbody = document.getElementById('eff-table-body');
  if (!tbody || !effChannelsData) return;

  const sorted = [...effChannelsData];

  sorted.sort((a, b) => {
    let valA, valB;
    switch (effSortField) {
      case 'msgs_7d':
        valA = a.msgs_7d || 0;
        valB = b.msgs_7d || 0;
        return effSortAsc ? valA - valB : valB - valA;

      case 'leads_7d':
        valA = a.leads_7d || 0;
        valB = b.leads_7d || 0;
        return effSortAsc ? valA - valB : valB - valA;

      case 'leads_total':
        valA = a.leads_total || 0;
        valB = b.leads_total || 0;
        return effSortAsc ? valA - valB : valB - valA;

      case 'last_activity':
        valA = a.days_idle !== null ? a.days_idle : 9999;
        valB = b.days_idle !== null ? b.days_idle : 9999;
        return effSortAsc ? valA - valB : valB - valA;

      case 'status':
        valA = a.is_dead ? 99 : (a.days_idle !== null ? a.days_idle : 9999);
        valB = b.is_dead ? 99 : (b.days_idle !== null ? b.days_idle : 9999);
        return effSortAsc ? valA - valB : valB - valA;

      case 'title':
        valA = (a.title || a.username_or_link || '').toLowerCase();
        valB = (b.title || b.username_or_link || '').toLowerCase();
        return effSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);

      case 'location':
        valA = (a.location_name || '').toLowerCase();
        valB = (b.location_name || '').toLowerCase();
        return effSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);

      case 'niche':
        valA = (a.niche_name || '').toLowerCase();
        valB = (b.niche_name || '').toLowerCase();
        return effSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);

      default:
        return 0;
    }
  });

  // Update header sort icons
  const fields = ['status', 'title', 'location', 'niche', 'msgs_7d', 'leads_7d', 'leads_total', 'last_activity'];
  fields.forEach(f => {
    const iconEl = document.getElementById(`sort-icon-${f}`);
    if (iconEl) {
      if (f === effSortField) {
        iconEl.textContent = effSortAsc ? '▲' : '▼';
        iconEl.style.color = '#4F46E5';
        iconEl.style.fontWeight = 'bold';
      } else {
        iconEl.textContent = '⇅';
        iconEl.style.color = '#94A3B8';
        iconEl.style.fontWeight = 'normal';
      }
    }
  });

  tbody.innerHTML = sorted.map(ch => {
    const rowClass = `eff-row-${ch.color_class.replace('eff-', '')}`;
    const tgLink = ch.username_or_link
      ? (ch.username_or_link.startsWith('@')
          ? `https://t.me/${ch.username_or_link.slice(1)}`
          : ch.username_or_link)
      : '#';
    const safeTitle = (ch.title || ch.username_or_link || '').replace(/'/g, "\\'");
    const deleteBtn = `<button class="btn-danger-sm" style="padding: 4px 10px; font-size: 12px;" onclick="deleteChannelFromEffectiveness('${ch.id}', '${safeTitle}')">🗑 Удалить</button>`;

    return `
      <tr class="${rowClass}">
        <td>
          <span class="eff-badge ${ch.color_class}">
            ${ch.color_emoji} ${ch.color_label}
          </span>
        </td>
        <td>
          <a href="${tgLink}" target="_blank" rel="noopener" style="color: var(--primary); font-weight:600; text-decoration:none;">
            ${escapeHtml(ch.title || ch.username_or_link)}
          </a>
          <div style="font-size:11px;color:#94A3B8;">${escapeHtml(ch.username_or_link)}</div>
        </td>
        <td><span style="font-size:13px;">${escapeHtml(ch.location_name)}</span></td>
        <td><span style="font-size:13px;">${escapeHtml(ch.niche_name)}</span></td>
        <td style="text-align:center; font-weight:700; color: ${ch.msgs_7d > 0 ? 'var(--primary)' : '#94A3B8'};">${ch.msgs_7d}</td>
        <td style="text-align:center; font-weight:700; color: ${ch.leads_7d > 0 ? '#059669' : '#94A3B8'};">${ch.leads_7d}</td>
        <td style="text-align:center; font-weight:600;">${ch.leads_total}</td>
        <td style="font-size:12px; color:#64748B;">${escapeHtml(ch.last_activity_at)}</td>
        <td>${deleteBtn}</td>
      </tr>`;
  }).join('');
}

async function deleteChannelFromEffectiveness(channelId, channelName) {
  if (!confirm(`🗑 Вы действительно хотите удалить канал «${channelName}» из системы прослушки?\n\nОн перестанет сканироваться.`)) return;
  try {
    const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok && (data.status === 'deleted' || data.status === 'ok')) {
      showToast(`✅ Канал «${channelName}» успешно удален!`, 'success');
      loadChannelEffectiveness();
      loadChannels();
    } else {
      showToast(`❌ ${data.message || 'Ошибка при удалении канала'}`, 'error');
    }
  } catch (err) {
    console.error('Error deleting channel from effectiveness:', err);
    showToast('❌ Ошибка сети при удалении канала', 'error');
  }
}

async function deleteDeadChannel(channelId, channelName) {
  if (!confirm(`🗑 Удалить канал «${channelName}» из мониторинга?\n\nЭто действие необратимо. Канал перестанет сканироваться.`)) return;
  try {
    const res = await fetch(`/api/channels/${channelId}/dead`, { method: 'DELETE' });
    const data = await res.json();
    if (data.status === 'deleted') {
      alert(`✅ Канал «${channelName}» удалён из базы.`);
      loadChannelEffectiveness();
      loadChannels();
    } else {
      alert(`❌ ${data.message || 'Ошибка удаления'}`);
    }
  } catch (err) {
    console.error(err);
  }
}

// ─── MASS BATCH IMPORT MODAL & CANDIDATES HUB ───
function openBatchImportModal() {
  const modal = document.getElementById('modal-batch-import');
  if (modal) modal.style.display = 'flex';
}

function closeBatchImportModal() {
  const modal = document.getElementById('modal-batch-import');
  if (modal) modal.style.display = 'none';
  const resDiv = document.getElementById('batch-import-result');
  if (resDiv) resDiv.style.display = 'none';
}

async function submitBatchImport(e) {
  e.preventDefault();
  const text = document.getElementById('batch-import-text').value;
  const loc = document.getElementById('batch-import-location').value;
  const niche = document.getElementById('batch-import-niche').value;
  const btn = document.getElementById('btn-submit-batch');
  const resDiv = document.getElementById('batch-import-result');

  if (!text || !text.trim()) return;

  btn.disabled = true;
  btn.textContent = '⏳ Анализ и проверка каналов...';
  if (resDiv) resDiv.style.display = 'none';

  try {
    const res = await fetch('/api/channels/batch-import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: text,
        location_code: loc,
        niche_code: niche
      })
    });
    const data = await res.json();
    btn.disabled = false;
    btn.textContent = '🚀 Распознать, проверить и подключить';

    if (resDiv) {
      resDiv.style.display = 'block';
      let html = `
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px; font-size: 13px;">
          <div style="font-weight: 700; color: #1E293B; margin-bottom: 8px;">📊 Результаты массового импорта:</div>
          <div style="display: flex; gap: 12px; margin-bottom: 10px;">
            <span style="color: #059669; font-weight: 700;">✅ Добавлено: ${data.added}</span>
            <span style="color: #64748B;">ℹ️ Дубликатов: ${data.duplicates}</span>
            <span style="color: #DC2626;">❌ Не найдено: ${data.invalid}</span>
          </div>
      `;
      if (data.details && data.details.length > 0) {
        html += `<div style="max-height: 140px; overflow-y: auto; font-family: monospace; font-size: 12px; display: flex; flex-direction: column; gap: 4px;">`;
        data.details.forEach(item => {
          let badgeColor = item.status === 'added' ? '#059669' : (item.status === 'duplicate' ? '#64748B' : '#DC2626');
          html += `<div><span style="color: ${badgeColor}; font-weight: 700;">[${item.status.toUpperCase()}]</span> ${escapeHtml(item.username)} — ${escapeHtml(item.title)}</div>`;
        });
        html += `</div>`;
      }
      html += `</div>`;
      resDiv.innerHTML = html;
    }

    if (data.added > 0) {
      loadChannels();
      loadChannelEffectiveness();
    }
  } catch (err) {
    btn.disabled = false;
    btn.textContent = '🚀 Распознать, проверить и подключить';
    alert('Ошибка при импорте: ' + err.message);
  }
}

async function loadChannelCandidates() {
  const container = document.getElementById('candidates-feed-container');
  const section = document.getElementById('card-candidates-section');
  const badge = document.getElementById('candidates-count-badge');
  if (!container || !section) return;

  try {
    const res = await fetch('/api/candidates');
    if (!res.ok) return;
    const candidates = await res.json();

    if (candidates.length === 0) {
      section.style.display = 'none';
      return;
    }

    section.style.display = 'block';
    if (badge) badge.textContent = `${candidates.length} новых`;

    container.innerHTML = candidates.map(cand => {
      const sourceMap = {
        'RECURSIVE_MENTION': '💬 Упомянут в переписке пользователей',
        'FORWARDED_POST': '🔁 Найдено из репостов',
        'GLOBAL_SEARCH': '🔍 Глобальный MTProto поиск ИИ',
        'DIRECTORY_CATALOG': '🌐 Из каталогов и справочников'
      };
      const sourceLabel = sourceMap[cand.source] || cand.source;

      return `
        <div style="background: #FFF; border: 1px solid #DDD6FE; border-radius: 8px; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; gap: 12px; box-shadow: 0 1px 2px rgba(124, 58, 237, 0.05);">
          <div style="flex: 1;">
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
              <span style="font-weight: 700; color: #1E293B; font-size: 14px;">📍 ${escapeHtml(cand.title)}</span>
              <a href="https://t.me/${escapeHtml(cand.username_or_link.replace('@', ''))}" target="_blank" rel="noopener" style="color: #6D28D9; text-decoration: none; font-size: 12px; font-weight: 600;">${escapeHtml(cand.username_or_link)} ↗️</a>
            </div>
            <div style="font-size: 12px; color: #64748B; margin-top: 4px; display: flex; gap: 12px;">
              <span>${sourceLabel}</span>
              <span>⏱ ${escapeHtml(cand.discovered_at_fmt)}</span>
            </div>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="btn-primary" style="background: #059669; font-size: 12px; padding: 6px 12px;" onclick="approveCandidate('${cand.id}')">✅ Принять в прослушку</button>
            <button class="btn-primary" style="background: #F1F5F9; color: #64748B; border: 1px solid #CBD5E1; font-size: 12px; padding: 6px 10px;" onclick="rejectCandidate('${cand.id}')">✕</button>
          </div>
        </div>
      `;
    }).join('');

  } catch (err) {
    console.error('Error loading candidates:', err);
  }
}

async function approveCandidate(candId) {
  try {
    const res = await fetch(`/api/candidates/${candId}/approve`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      loadChannelCandidates();
      loadChannels();
      loadChannelEffectiveness();
    }
  } catch (err) {
    alert('Ошибка при принятии кандидата: ' + err.message);
  }
}

async function rejectCandidate(candId) {
  try {
    const res = await fetch(`/api/candidates/${candId}/reject`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      loadChannelCandidates();
    }
  } catch (err) {
    alert('Ошибка при отклонении: ' + err.message);
  }
}

// ────────────────────────────────────────────────────────────────────────────
// B2B SELLER OUTREACH AUDIENCE DASHBOARD MODULE
// ────────────────────────────────────────────────────────────────────────────
let b2bLeadsCache = [];

async function loadB2BOutreachLeads() {
  const tbody = document.getElementById('b2b-outreach-table-body');
  if (!tbody) return;

  const geo = document.getElementById('filter-outreach-geo')?.value || 'all';
  const niche = document.getElementById('filter-outreach-niche')?.value || 'all';
  const status = document.getElementById('filter-outreach-status')?.value || 'all';

  try {
    const res = await fetch(`/api/outreach/leads?location=${encodeURIComponent(geo)}&niche=${encodeURIComponent(niche)}&status=${encodeURIComponent(status)}`);
    const data = await res.json();

    if (data.status !== 'ok' || !data.leads || data.leads.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: #94A3B8; padding: 24px;">Потенциальные B2B-продавцы по выбранным фильтрам пока не найдены</td></tr>`;
      b2bLeadsCache = [];
      return;
    }

    b2bLeadsCache = data.leads;

    const locFlags = {
      dubai: '🇦🇪 Дубай',
      nhatrang: '🇻🇳 Вьетнам',
      phuket: '🇹🇭 Таиланд',
      global: '🌐 Глобал'
    };

    const statusBadges = {
      READY_FOR_OUTREACH: '<span class="badge badge-success">🟢 Утвержден</span>',
      NEED_APPROVAL: '<span class="badge" style="background:#FEF3C7; color:#D97706; border:1px solid #FDE68A;">⏳ На утверждении</span>',
      REJECTED: '<span class="badge badge-danger">❌ Отклонен</span>',
      SENT: '<span class="badge" style="background:#E0E7FF; color:#4F46E5;">📩 Отправлен</span>'
    };

    const rubricsList = window.cachedRubrics || [
      { code: 'real_estate', name: '🏠 Недвижимость' },
      { code: 'bike_rent', name: '🛵 Аренда байков' },
      { code: 'currency_exchange', name: '💱 Обмен валюты' },
      { code: 'services_visa', name: '🛂 Визы & Услуги' },
      { code: 'auto_kasko', name: '🚗 Автострахование' },
      { code: 'hr_hiring', name: '👔 HR & Найм персонала' },
      { code: 'community', name: '💬 Сообщество' },
      { code: 'other_b2b', name: '💼 B2B Услуги & Прочее' }
    ];

    tbody.innerHTML = data.leads.map(lead => {
      const geoLabel = locFlags[lead.location_code] || lead.location_code || '🌐 Глобал';
      const statusBadge = statusBadges[lead.status] || `<span class="badge">${lead.status}</span>`;
      const uname = lead.author_username ? `@${lead.author_username}` : (lead.author_first_name || `ID ${lead.telegram_id}`);
      const historyCount = (lead.messages_history || []).length || 1;

      let nicheOptions = rubricsList.map(r => `
        <option value="${r.code}" ${r.code === lead.niche_code ? 'selected' : ''}>${r.icon || '🏷️'} ${escapeHtml(r.name)}</option>
      `).join('');
      nicheOptions += `<option value="__new__">➕ Создать новую рубрику...</option>`;

      return `
        <tr>
          <td>
            <strong>${escapeHtml(uname)}</strong>
            <div style="font-size: 11px; color: #94A3B8;">${escapeHtml(lead.author_first_name || '')}</div>
          </td>
          <td><span class="badge" style="background: #F1F5F9; color: #334155;">${geoLabel}</span></td>
          <td>
            <select class="form-select" style="padding: 4px 8px; font-size: 12px; max-width: 170px; font-weight: 600;" onchange="handleOutreachNicheChange('${lead.id}', this.value, this)">
              ${nicheOptions}
            </select>
          </td>
          <td style="max-width: 250px; font-size: 12.5px;">${escapeHtml(lead.sales_hook || lead.raw_ad_text)}</td>
          <td><strong style="color: #059669;">${formatConfidencePct(lead.confidence_score)}%</strong></td>
          <td>
            <button class="btn-primary" style="padding: 4px 10px; font-size: 12px; background: #6366F1;" onclick="viewSellerHistory('${lead.id}')">
              📚 История (${historyCount} сообщ.)
            </button>
          </td>
          <td>
            <div style="display: flex; flex-direction: column; gap: 6px; align-items: flex-start;">
              ${statusBadge}
              ${lead.status !== 'READY_FOR_OUTREACH' ? `<button class="btn-primary" style="padding: 3px 8px; font-size: 11px; background: #059669;" onclick="updateOutreachLeadStatus('${lead.id}', 'READY_FOR_OUTREACH')">✅ Утвердить</button>` : ''}
              ${lead.status !== 'REJECTED' ? `<button class="btn-primary" style="padding: 3px 8px; font-size: 11px; background: #EF4444;" onclick="updateOutreachLeadStatus('${lead.id}', 'REJECTED')">❌ Отклонить</button>` : ''}
              <button class="btn-danger-sm" style="padding: 3px 8px; font-size: 11px;" onclick="deleteOutreachLead('${lead.id}')">🗑️ Удалить</button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error('Error loading B2B outreach leads:', err);
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: #EF4444; padding: 24px;">Ошибка загрузки B2B аудитории: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function handleOutreachNicheChange(leadId, chosenVal, selectEl) {
  let targetNiche = chosenVal;

  if (chosenVal === '__new__') {
    const newName = prompt('➕ Введите название новой рубрики (например: 👔 HR & Найм персонала):');
    if (!newName || !newName.trim()) {
      loadB2BOutreachLeads();
      return;
    }
    const cleanCode = newName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_');
    const iconInput = prompt('Иконка для рубрики (например 👔 или 🏷️):', '🏷️');

    try {
      const resR = await fetch('/api/rubrics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: cleanCode, name: newName.trim(), icon: iconInput || '🏷️' })
      });
      if (resR.ok) {
        targetNiche = cleanCode;
        await fetchRubrics();
      }
    } catch (e) {
      console.error('Error creating rubric:', e);
    }
  }

  try {
    const res = await fetch(`/api/outreach/leads/${leadId}/niche`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ niche_code: targetNiche })
    });
    if (res.ok) {
      showToast('✅ Рубрика B2B лида успешно изменена!', 'success');
      loadB2BOutreachLeads();
    }
  } catch (err) {
    alert('Ошибка при изменении рубрики: ' + err.message);
  }
}

async function deleteOutreachLead(leadId) {
  if (!confirm('🗑 Вы уверены, что хотите полностью удалить этого B2B лида?')) return;

  try {
    const res = await fetch(`/api/outreach/leads/${leadId}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      showToast('✅ B2B лид успешно удален!', 'success');
      loadB2BOutreachLeads();
    } else {
      alert('Ошибка при удалении: ' + (data.message || 'ошибка сервера'));
    }
  } catch (err) {
    alert('Ошибка сети при удалении: ' + err.message);
  }
}

function viewSellerHistory(leadId) {
  const lead = b2bLeadsCache.find(l => l.id === leadId);
  if (!lead) return;

  const modal = document.getElementById('modal-seller-history');
  const feed = document.getElementById('seller-history-feed');
  const title = document.getElementById('seller-history-title');

  if (!modal || !feed) return;

  const uname = lead.author_username ? `@${lead.author_username}` : lead.author_first_name;
  if (title) title.textContent = `📚 История рекламы продавца ${uname} (Ниша: ${lead.niche_code})`;

  const history = lead.messages_history || [];
  if (history.length === 0) {
    feed.innerHTML = `
      <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px;">
        <div style="font-size: 12px; color: #64748B; margin-bottom: 6px;">📍 ${escapeHtml(lead.chat_title || 'Telegram Chat')}</div>
        <div style="font-size: 13.5px; color: #0F172A; white-space: pre-wrap;">${escapeHtml(lead.raw_ad_text)}</div>
      </div>
    `;
  } else {
    feed.innerHTML = history.map(item => `
      <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px;">
        <div style="display: flex; justify-content: space-between; font-size: 12px; color: #64748B; margin-bottom: 6px;">
          <span>📍 <strong>${escapeHtml(item.chat_title || 'Telegram Chat')}</strong></span>
          <span>⏱ ${item.timestamp ? new Date(item.timestamp).toLocaleString('ru-RU') : ''}</span>
        </div>
        <div style="font-size: 13.5px; color: #0F172A; white-space: pre-wrap;">${escapeHtml(item.message_text)}</div>
      </div>
    `).join('');
  }

  modal.style.display = 'flex';
}

function closeSellerHistoryModal() {
  const modal = document.getElementById('modal-seller-history');
  if (modal) modal.style.display = 'none';
}

async function updateOutreachLeadStatus(leadId, newStatus) {
  try {
    const res = await fetch(`/api/outreach/leads/${leadId}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      loadB2BOutreachLeads();
    }
  } catch (err) {
    alert('Ошибка при обновлении статуса B2B лида: ' + err.message);
  }
}

function exportB2BLeadsCSV() {
  if (!b2bLeadsCache || b2bLeadsCache.length === 0) {
    alert('Нет B2B лидов для экспорта!');
    return;
  }

  let csvContent = 'data:text/csv;charset=utf-8,Username,First Name,Niche,GEO,Confidence,Status,Sales Hook,Raw Text\n';
  b2bLeadsCache.forEach(l => {
    const row = [
      `"${l.author_username || ''}"`,
      `"${l.author_first_name || ''}"`,
      `"${l.niche_code || ''}"`,
      `"${l.location_code || ''}"`,
      `"${l.confidence_score || 0}"`,
      `"${l.status || ''}"`,
      `"${(l.sales_hook || '').replace(/"/g, '""')}"`,
      `"${(l.raw_ad_text || '').replace(/"/g, '""')}"`
    ].join(',');
    csvContent += row + '\n';
  });

  const encodedUri = encodeURI(csvContent);
  const link = document.createElement('a');
  link.setAttribute('href', encodedUri);
  link.setAttribute('download', `b2b_outreach_audience_${new Date().toISOString().slice(0,10)}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}


// ────────────────────────────────────────────────────────────────────────────
// EMPLOYEES & LIVE DIALOGUES MANUAL TAKEOVER MODULE
// ────────────────────────────────────────────────────────────────────────────
async function loadOutreachEmployees() {
  const tbody = document.getElementById('employees-table-body');
  if (!tbody) return;

  try {
    const res = await fetch('/api/outreach/accounts');
    const data = await res.json();

    if (data.status !== 'ok' || !data.accounts || data.accounts.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: #94A3B8; padding: 20px;">Сотрудники пока не подключены. Нажмите «Подключить сессию & прокси», чтобы добавить Ульяну, Петра, Максима или Влада.</td></tr>`;
      return;
    }

    const defaultNames = ["Ульяна", "Петр", "Максим", "Влад"];

    tbody.innerHTML = data.accounts.map((acc, idx) => {
      const defaultName = defaultNames[idx % defaultNames.length];
      const mName = acc.manager_name || defaultName;
      const mRole = acc.manager_role || "Руководитель B2B развития LeadRadar";
      const proxyStr = acc.proxy_url ? escapeHtml(acc.proxy_url.replace(/:[^:@]+@/, ':***@')) : '🌐 Без прокси (Прямой)';
      
      const statusBadge = acc.status === 'ACTIVE' 
        ? '<span class="badge badge-success">🟢 Активен</span>' 
        : (acc.status === 'COOL_DOWN' ? '<span class="badge" style="background:#FEF3C7; color:#D97706;">⏳ Охлаждение 24ч</span>' : '<span class="badge badge-danger">🔴 Заблокирован</span>');

      return `
        <tr>
          <td>
            <strong style="color: #4F46E5; font-size: 14px;">👤 ${escapeHtml(mName)}</strong>
            <div style="font-size: 11px; color: #64748B;">${escapeHtml(mRole)}</div>
          </td>
          <td>
            <code>${escapeHtml(acc.phone_number || `ID ${acc.id}`)}</code>
          </td>
          <td>
            <span style="font-size: 12px; font-family: monospace; color: #475569;">${proxyStr}</span>
          </td>
          <td>
            <strong style="color: #059669;">${acc.daily_sent_count}</strong> / ${acc.max_daily_limit} сообщ.
          </td>
          <td>${statusBadge}</td>
          <td>
            <button class="btn-primary" style="padding: 4px 10px; font-size: 11px; background: #6366F1;" onclick="editEmployeeModal(${acc.id}, '${escapeHtml(mName)}', '${escapeHtml(mRole)}')">✏️ Настроить</button>
          </td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error('Error loading outreach employees:', err);
  }
}

async function loadB2BDialogues() {
  const container = document.getElementById('dialogues-feed-container');
  if (!container) return;

  try {
    const res = await fetch('/api/outreach/dialogues');
    const data = await res.json();

    if (data.status !== 'ok' || !data.dialogues || data.dialogues.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; color: #94A3B8; padding: 30px; background: #F8FAFC; border: 1px dashed #CBD5E1; border-radius: 12px;">
          💬 Активных переписок с B2B-клиентами пока нет. Как только клиент ответит на рассылку сотрудника, здесь появится диалог с возможностью перехвата управления!
        </div>
      `;
      return;
    }

    container.innerHTML = data.dialogues.map(d => {
      const uname = d.username ? `@${d.username}` : `ID ${d.telegram_id}`;
      const isAI = d.ai_enabled;
      const modeBadge = isAI 
        ? `<span class="badge" style="background:#ECFDF5; color:#047857; border:1px solid #A7F3D0;">🤖 ИИ-Сотрудник ответит автоматически</span>` 
        : `<span class="badge" style="background:#FEF2F2; color:#DC2626; border:1px solid #FCA5A5;">👤 Ручной режим (ИИ выключен)</span>`;

      const historyHtml = (d.dialogue_history || []).map(msg => {
        const isManager = msg.role === 'manager';
        const authorName = isManager ? `${d.manager_name} (LeadRadar)` : uname;
        const bg = isManager ? '#EEF2FF' : '#F1F5F9';
        const border = isManager ? '#C7D2FE' : '#E2E8F0';
        const align = isManager ? 'flex-end' : 'flex-start';

        return `
          <div style="align-self: ${align}; max-width: 85%; background: ${bg}; border: 1px solid ${border}; border-radius: 10px; padding: 10px 14px;">
            <div style="font-size: 11px; font-weight: 700; color: ${isManager ? '#4F46E5' : '#334155'}; margin-bottom: 4px; display: flex; justify-content: space-between; gap: 12px;">
              <span>${escapeHtml(authorName)} ${msg.is_manual ? '✍️ (вручную)' : ''}</span>
              <span style="font-weight: 400; color: #94A3B8;">${msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'}) : ''}</span>
            </div>
            <div style="font-size: 13px; color: #0F172A; white-space: pre-wrap;">${escapeHtml(msg.text)}</div>
          </div>
        `;
      }).join('');

      return `
        <div style="background: #FFF; border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; box-shadow: 0 2px 6px rgba(0,0,0,0.03);">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px;">
            <div>
              <h4 style="font-size: 15px; font-weight: 700; color: #0F172A; margin: 0;">💬 Переписка с ${escapeHtml(uname)} (Ниша: ${escapeHtml(d.niche)})</h4>
              <p style="font-size: 12px; color: #64748B; margin-top: 2px;">Закрепленный менеджер: <strong>${escapeHtml(d.manager_name)}</strong> (${escapeHtml(d.manager_role)})</p>
            </div>
            <div style="display: flex; align-items: center; gap: 10px;">
              ${modeBadge}
              <button class="btn-primary" style="padding: 6px 12px; font-size: 12px; background: ${isAI ? '#EF4444' : '#059669'};" onclick="toggleProspectAI(${d.id}, ${!isAI})">
                ${isAI ? '🛑 Отключить ИИ и войти в диалог' : '🟢 Включить ИИ-Сотрудника обратно'}
              </button>
            </div>
          </div>

          <!-- Chat History -->
          <div style="display: flex; flex-direction: column; gap: 8px; max-height: 280px; overflow-y: auto; padding: 10px; background: #FAFAFA; border: 1px solid #F1F5F9; border-radius: 10px; margin-bottom: 12px;">
            ${historyHtml}
          </div>

          <!-- Manual Message Input -->
          <div style="display: flex; gap: 8px;">
            <input type="text" id="manual-msg-inp-${d.id}" class="form-input" placeholder="Введите ваш ответ от имени ${escapeHtml(d.manager_name)}..." style="font-size: 13px;">
            <button class="btn-primary" style="padding: 8px 16px; font-size: 13px; white-space: nowrap; background: #4F46E5;" onclick="sendManualMessage(${d.id})">✉️ Отправить в Telegram</button>
          </div>
        </div>
      `;
    }).join('');

  } catch (err) {
    console.error('Error loading B2B dialogues:', err);
  }
}

async function toggleProspectAI(prospectId, newEnabled) {
  try {
    const res = await fetch(`/api/outreach/dialogues/${prospectId}/toggle-ai`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_enabled: newEnabled })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      loadB2BDialogues();
    }
  } catch (err) {
    alert('Ошибка при переключении режима ИИ: ' + err.message);
  }
}

async function sendManualMessage(prospectId) {
  const input = document.getElementById(`manual-msg-inp-${prospectId}`);
  if (!input) return;
  const txt = input.value.trim();
  if (!txt) return;

  try {
    const res = await fetch(`/api/outreach/dialogues/${prospectId}/send-manual`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: txt })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      input.value = '';
      loadB2BDialogues();
    } else {
      alert('Ошибка отправки сообщения: ' + data.message);
    }
  } catch (err) {
    alert('Ошибка сети при отправке сообщения: ' + err.message);
  }
}

function openAddEmployeeModal() {
  const modal = document.getElementById('modal-add-employee');
  if (modal) modal.style.display = 'flex';
}

function closeAddEmployeeModal() {
  const modal = document.getElementById('modal-add-employee');
  if (modal) modal.style.display = 'none';
}

async function submitAddEmployee(e) {
  e.preventDefault();
  const mName = document.getElementById('emp-manager-name').value;
  const mRole = document.getElementById('emp-manager-role').value;
  const sessionStr = document.getElementById('emp-session-string').value;
  const proxyUrl = document.getElementById('emp-proxy-url').value;
  const phoneNum = document.getElementById('emp-phone-number').value;
  const maxLimit = parseInt(document.getElementById('emp-max-limit').value) || 15;

  try {
    const res = await fetch('/api/outreach/accounts/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_string: sessionStr,
        phone_number: phoneNum,
        proxy_url: proxyUrl,
        manager_name: mName,
        manager_role: mRole,
        max_daily_limit: maxLimit
      })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      closeAddEmployeeModal();
      loadOutreachEmployees();
      alert(`✅ Аккаунт сотрудника "${mName}" успешно добавлен и активирован!`);
    } else {
      alert('Ошибка при добавлении сотрудника: ' + data.message);
    }
  } catch (err) {
    alert('Ошибка сети: ' + err.message);
  }
}

function editEmployeeModal(accId, curName, curRole) {
  const newName = prompt('Имя сотрудника (Ульяна, Петр, Максим, Влад):', curName);
  if (newName === null) return;
  const newRole = prompt('Должность сотрудника:', curRole);
  if (newRole === null) return;

  fetch(`/api/outreach/employees/${accId}/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      manager_name: newName,
      manager_role: newRole
    })
  }).then(res => res.json()).then(data => {
    if (data.status === 'ok') {
      loadOutreachEmployees();
    }
  }).catch(err => alert('Ошибка обновления сотрудника: ' + err.message));
}

// Handler for manual 1-hour forced rescan trigger
async function triggerManualRescanPastHour(btn) {
  let origText = '';
  if (btn) {
    origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Пересканирование...';
  }
  try {
    const res = await fetch('/api/collector/rescan-last-hour', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      showToast(`⚡ ${data.message || 'Приоритетный перескан за 1 час успешно запущен!'}`, 'success');
      if (typeof fetchAllData === 'function') fetchAllData();
      if (typeof fetchCollectorLogs === 'function') fetchCollectorLogs();
    } else {
      showToast(`❌ ${data.message || 'Ошибка запуска пересканирования'}`, 'error');
    }
  } catch (err) {
    console.error('Error triggering manual rescan:', err);
    showToast('❌ Ошибка сети при вызове пересканирования', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText || '⚡ Пересканировать за 1 час';
    }
  }
}

// ── LIVE PROCESS LOGS TERMINAL TICKER ─────────────────────────────────────
let lastProcessLogId = 0;

async function fetchLiveProcessLogs(reset = false) {
  const win = document.getElementById('process-terminal-window');
  const badge = document.getElementById('process-stream-badge');
  const filterSel = document.getElementById('process-category-filter');
  const autoChk = document.getElementById('process-autoscroll-chk');

  if (!win) return;

  if (reset) {
    lastProcessLogId = 0;
    win.innerHTML = `<div style="color: #64748B;">[${new Date().toLocaleTimeString()}] 🔄 Перезагрузка терминала процессов...</div>`;
  }

  const categoryVal = filterSel ? filterSel.value : 'all';
  const url = `/api/collector/live-process-logs?since_id=${lastProcessLogId}&limit=50&category=${categoryVal}`;

  try {
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();

    const logs = data.logs || [];
    const isStalled = data.is_stalled;
    const idleSec = data.last_activity_seconds || 0;

    // Update Status Badge
    if (badge) {
      if (isStalled) {
        badge.style.background = '#991B1B';
        badge.style.color = '#FCA5A5';
        badge.style.borderColor = '#EF4444';
        badge.innerHTML = `🔴 ТРЕВОГА: ПОТОК ЛОГОВ ОСТАНОВИЛСЯ! (Простой ${idleSec}с)`;
      } else {
        badge.style.background = '#064E3B';
        badge.style.color = '#34D399';
        badge.style.borderColor = '#059669';
        badge.innerHTML = `🟢 СТРИМИНГ АКТИВЕН (Логи бегут)`;
      }
    }

    if (logs.length > 0) {
      // Clear initial placeholder greeting message if present
      const firstChild = win.firstElementChild;
      if (firstChild && firstChild.textContent && (firstChild.textContent.includes('Терминал процессов подключен') || firstChild.textContent.includes('Перезагрузка терминала'))) {
        win.innerHTML = '';
      }

      logs.forEach(item => {
        if (item.id > lastProcessLogId) {
          lastProcessLogId = item.id;
        }

        const div = document.createElement('div');
        div.style.cssText = 'padding: 4px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); word-break: break-word; transition: all 0.2s ease;';

        let categoryTag = `[${item.category}]`;
        let color = '#94A3B8';
        let bgStyle = '';

        if (item.level === 'lead') {
          color = '#34D399';
          bgStyle = 'background: rgba(6, 78, 59, 0.5); border-radius: 6px; border-left: 4px solid #10B981; font-weight: bold; margin-bottom: 2px;';
        } else if (item.level === 'success') {
          color = '#38BDF8';
        } else if (item.level === 'warning') {
          color = '#FBBF24';
        } else if (item.level === 'noise') {
          color = '#64748B';
        } else if (item.level === 'error') {
          color = '#F43F5E';
          bgStyle = 'background: rgba(153, 27, 27, 0.4); border-radius: 6px; border-left: 4px solid #EF4444; margin-bottom: 2px;';
        }

        if (bgStyle) div.style.cssText += bgStyle;

        div.innerHTML = `
          <span style="color: #64748B; font-weight: 600;">[${escapeHtml(item.timestamp_fmt)}]</span>
          <span style="color: #A7F3D0; font-weight: 700;">${escapeHtml(categoryTag)}</span>
          <span style="color: ${color}; font-weight: 600;">${escapeHtml(item.title)}</span>
          ${item.details ? `<div style="color: #CBD5E1; font-size: 11px; margin-left: 14px; opacity: 0.9;">└ ${escapeHtml(item.details)}</div>` : ''}
        `;

        // Prepend newest log at the top of terminal
        win.prepend(div);
      });

      // Prune old logs at bottom if memory exceeds 200 rows
      while (win.children.length > 200) {
        win.removeChild(win.lastElementChild);
      }

      // Keep scroll anchored at top (0) so latest log is immediately visible on top line
      if (autoChk && autoChk.checked) {
        win.scrollTop = 0;
      }
    }
  } catch (err) {
    console.error('Error fetching live process logs:', err);
  }
}

function clearProcessTerminal() {
  const win = document.getElementById('process-terminal-window');
  if (win) {
    lastProcessLogId = 0;
    win.innerHTML = `<div style="color: #64748B;">[${new Date().toLocaleTimeString()}] 🗑 Консоль очищена. Загрузка логирования...</div>`;
    fetchLiveProcessLogs(false);
  }
}

// Auto-start live process logs ticker loop (every 1.5 seconds)
if (typeof window !== 'undefined') {
  setInterval(() => {
    fetchLiveProcessLogs(false);
  }, 1500);
}

