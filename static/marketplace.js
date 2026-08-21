// RADAR Marketplace JS - TMA-first auth + lead browsing + purchase flow

const API = '/api/tma';
let currentUser = null;
let currentLeads = [];
let selectedLead = null;
let currentNiche = 'all';
let currentTab = 'leads';
let webLoginToken = null;
let webLoginPollInterval = null;

// ─── Init ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const twa = window.Telegram?.WebApp;
  if (twa) {
    twa.ready();
    twa.expand();
    twa.enableClosingConfirmation();
    // Apply TMA theme colors
    document.documentElement.style.setProperty('--bg', twa.themeParams?.bg_color || '#0F1117');
  }
  initAuth();
});

async function initAuth() {
  const twa = window.Telegram?.WebApp;

  // Case 1: Inside Telegram TMA with initData
  if (twa?.initData) {
    try {
      const resp = await fetch(`${API}/auth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: twa.initData })
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        // Store JWT in localStorage for subsequent requests
        localStorage.setItem('radar_tma_token', data.token);
        currentUser = data.partner;
        showApp();
        return;
      }
    } catch (e) {
      console.error('TMA auth failed:', e);
    }
  }

  // Case 2: Has stored JWT (returning browser user)
  const stored = localStorage.getItem('radar_tma_token');
  if (stored) {
    try {
      const me = await apiFetch('/me');
      if (me && me.id) {
        currentUser = me;
        showApp();
        return;
      }
    } catch (e) {
      localStorage.removeItem('radar_tma_token');
    }
  }

  // Case 3: Browser without TMA - show auth screen
  showAuthScreen();
}

// ─── API helper with auth header ─────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('radar_tma_token');
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...(options.headers || {})
  };
  const resp = await fetch(`${API}${path}`, { ...options, headers });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

// ─── UI State ─────────────────────────────────────────────────────────────
function showApp() {
  document.getElementById('loading-screen').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';

  // Show admin banner if admin/superadmin
  const role = currentUser?.role || '';
  if (['ADMIN', 'SUPERADMIN'].includes(role)) {
    const banner = document.getElementById('admin-banner');
    if (banner) banner.style.display = 'flex';
  }

  // Update header
  const name = currentUser?.company_name || 'Партнёр';
  document.getElementById('user-display').textContent = name;
  updateBalanceDisplay(currentUser?.balance || 0);

  // Load leads
  fetchLeads();
}

function showAuthScreen() {
  document.getElementById('loading-screen').style.display = 'none';
  document.getElementById('app').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'flex';
}

function updateBalanceDisplay(bal) {
  document.getElementById('balance-value').textContent = parseFloat(bal).toFixed(2);
}

// ─── Niche Filter ─────────────────────────────────────────────────────────
function setNicheFilter(niche, el) {
  currentNiche = niche;
  document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  fetchLeads();
}

// ─── Tabs ─────────────────────────────────────────────────────────────────
function switchTab(tab) {
  currentTab = tab;
  document.getElementById('tab-leads').style.display = tab === 'leads' ? 'block' : 'none';
  document.getElementById('tab-purchases').style.display = tab === 'purchases' ? 'block' : 'none';
  document.getElementById('tab-btn-leads').classList.toggle('active', tab === 'leads');
  document.getElementById('tab-btn-purchases').classList.toggle('active', tab === 'purchases');
  if (tab === 'purchases') fetchPurchases();
}

// ─── Fetch & Render Leads ─────────────────────────────────────────────────
async function fetchLeads() {
  const container = document.getElementById('leads-container');
  container.innerHTML = '<div class="empty-state"><div class="spinner" style="margin:0 auto"></div></div>';
  try {
    const nicheParam = currentNiche !== 'all' ? `&niche=${currentNiche}` : '';
    const leads = await apiFetch(`/leads?limit=50${nicheParam}`);
    currentLeads = leads;
    renderLeads(leads);
  } catch (e) {
    container.innerHTML = `<div class="empty-state"><div class="emoji">⚠️</div><h3>Ошибка загрузки</h3><p>${e.message}</p></div>`;
  }
}

function renderLeads(leads) {
  const container = document.getElementById('leads-container');
  if (!leads || leads.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="emoji">🎯</div>
        <h3>Нет доступных лидов</h3>
        <p>Сканер ИИ постоянно мониторит каналы. Новые лиды появляются в реальном времени.</p>
      </div>`;
    return;
  }

  container.innerHTML = leads.map(lead => {
    const tempClass = lead.temperature === 'HOT' ? 'badge-hot' : 'badge-warm';
    const tempLabel = lead.temperature === 'HOT' ? '🔥 HOT' : '🌡 WARM';
    const conf = Math.round((lead.confidence_score || 0) * 100);
    const date = lead.created_at ? new Date(lead.created_at).toLocaleString('ru-RU', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    }) : '';

    return `
    <div class="lead-card" id="lead-card-${lead.id}">
      <div class="lead-card-top">
        <div class="lead-badges">
          <span class="badge ${tempClass}">${tempLabel}</span>
          <span class="badge badge-niche">${lead.niche_name}</span>
          <span class="badge badge-location">${lead.location_name}</span>
        </div>
        <div class="lead-price">$${parseFloat(lead.price).toFixed(2)}</div>
      </div>
      <div class="lead-intent">${escapeHtml(lead.intent_summary)}</div>
      <div class="lead-hook">${escapeHtml(lead.sales_hook)}</div>
      <div class="lead-footer">
        <div>
          <div class="confidence-bar">
            <div class="conf-track"><div class="conf-fill" style="width:${conf}%"></div></div>
            <span class="conf-label">${conf}%</span>
          </div>
          <div class="lead-time">${date}</div>
        </div>
        <button class="btn-buy" onclick="openBuyModal('${lead.id}')">
          💰 Выкупить
        </button>
      </div>
    </div>`;
  }).join('');
}

// ─── Fetch & Render Purchases ─────────────────────────────────────────────
async function fetchPurchases() {
  const container = document.getElementById('purchases-container');
  container.innerHTML = '<div class="empty-state"><div class="spinner" style="margin:0 auto"></div></div>';
  try {
    const purchases = await apiFetch('/my-purchases');
    renderPurchases(purchases);
  } catch (e) {
    container.innerHTML = `<div class="empty-state"><div class="emoji">⚠️</div><h3>Ошибка загрузки</h3></div>`;
  }
}

function renderPurchases(purchases) {
  const container = document.getElementById('purchases-container');
  if (!purchases || purchases.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="emoji">📦</div>
        <h3>Покупок пока нет</h3>
        <p>Выкупите лид в маркетплейсе — здесь появятся контакты клиентов.</p>
      </div>`;
    return;
  }

  container.innerHTML = purchases.map(p => {
    const date = p.purchased_at ? new Date(p.purchased_at).toLocaleString('ru-RU', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    }) : '';
    return `
    <div class="purchase-card">
      <div class="purchase-card-header">
        <div>
          <span class="badge badge-niche" style="margin-bottom:4px;display:inline-block">${p.niche_name}</span>
          <span class="badge badge-location">${p.location_name}</span>
        </div>
        <div style="font-size:13px;color:var(--text-dim)">${date}</div>
      </div>
      <div style="font-size:14px;color:var(--text);margin-bottom:8px;">${escapeHtml(p.intent_summary)}</div>
      <div class="lead-hook" style="margin-bottom:8px;">${escapeHtml(p.sales_hook || '')}</div>
      ${p.user_id ? `<div class="purchase-contact">ID ${p.user_id} — напишите через Telegram Bot: /contact_${p.user_id}</div>` : ''}
      <div style="font-size:12px;color:var(--text-dim);margin-top:8px;">💳 Оплачено: $${parseFloat(p.price_paid).toFixed(2)}</div>
    </div>`;
  }).join('');
}

// ─── Buy Modal ────────────────────────────────────────────────────────────
function openBuyModal(leadId) {
  selectedLead = currentLeads.find(l => l.id === leadId);
  if (!selectedLead) return;

  document.getElementById('modal-intent-text').textContent = selectedLead.intent_summary;
  document.getElementById('modal-balance').textContent = parseFloat(currentUser?.balance || 0).toFixed(2);
  document.getElementById('modal-price').textContent = parseFloat(selectedLead.price).toFixed(2);
  document.getElementById('buy-modal').classList.add('show');
}

function closeBuyModal() {
  document.getElementById('buy-modal').classList.remove('show');
  selectedLead = null;
}

async function confirmBuy() {
  if (!selectedLead) return;
  const btn = document.getElementById('btn-confirm-buy');
  btn.disabled = true;
  btn.textContent = '⏳ Обработка...';

  try {
    const result = await apiFetch(`/leads/${selectedLead.id}/buy`, { method: 'POST' });

    if (result.status === 'ok') {
      currentUser.balance = result.new_balance;
      updateBalanceDisplay(result.new_balance);
      closeBuyModal();
      showToast('✅ Лид выкуплен! Проверьте «Мои покупки»', 'success');
      // Remove bought lead from list
      currentLeads = currentLeads.filter(l => l.id !== selectedLead?.id);
      const card = document.getElementById(`lead-card-${result.lead?.id}`);
      if (card) {
        card.style.opacity = '0.4';
        card.style.pointerEvents = 'none';
        card.querySelector('.btn-buy').textContent = '✅ Выкуплен';
      }
      setTimeout(fetchLeads, 1500);
    } else if (result.status === 'insufficient_balance') {
      closeBuyModal();
      showToast('⚠️ Недостаточно средств. Пополните баланс командой /deposit в боте', 'error', 4000);
    } else {
      showToast(`❌ ${result.message || 'Ошибка покупки'}`, 'error');
    }
  } catch (e) {
    showToast('❌ Ошибка сети', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '✅ Выкупить';
  }
}

// ─── Web Login Flow (browser) ─────────────────────────────────────────────
async function startWebLogin() {
  const statusEl = document.getElementById('auth-login-status');
  const btn = document.getElementById('btn-web-login');
  btn.disabled = true;
  statusEl.textContent = '⏳ Генерация ссылки...';
  statusEl.className = '';

  try {
    const resp = await fetch(`${API}/web-login-request`, { method: 'POST' });
    const data = await resp.json();
    webLoginToken = data.token;

    // Open bot link
    window.open(data.deep_link, '_blank');
    statusEl.textContent = '📱 Откройте Telegram-бот и нажмите «Подтвердить». Ожидаем...';
    btn.textContent = '⏳ Ожидание подтверждения...';

    // Start polling
    if (webLoginPollInterval) clearInterval(webLoginPollInterval);
    webLoginPollInterval = setInterval(() => pollWebLoginStatus(), 2000);

  } catch (e) {
    statusEl.textContent = '❌ Ошибка. Попробуйте ещё раз.';
    statusEl.className = 'error';
    btn.disabled = false;
    btn.textContent = '🔑 Войти через Telegram';
  }
}

async function pollWebLoginStatus() {
  if (!webLoginToken) return;
  try {
    const resp = await fetch(`${API}/web-login-status?token=${webLoginToken}`);
    const data = await resp.json();

    if (data.status === 'approved') {
      clearInterval(webLoginPollInterval);
      localStorage.setItem('radar_tma_token', data.token);
      const statusEl = document.getElementById('auth-login-status');
      statusEl.textContent = '✅ Авторизован! Загружаем маркетплейс...';
      statusEl.className = 'success';
      // Load profile
      const me = await apiFetch('/me');
      currentUser = me;
      setTimeout(showApp, 800);

    } else if (data.status === 'expired' || data.status === 'invalid') {
      clearInterval(webLoginPollInterval);
      const statusEl = document.getElementById('auth-login-status');
      statusEl.textContent = '⏰ Ссылка истекла. Попробуйте снова.';
      statusEl.className = 'error';
      const btn = document.getElementById('btn-web-login');
      btn.disabled = false;
      btn.textContent = '🔑 Войти через Telegram';
    }
  } catch (e) { /* continue polling */ }
}

// ─── Toast ────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = 'info', duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove('show');
  }, duration);
}

// ─── Util ─────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Close modal on backdrop click
document.getElementById('buy-modal').addEventListener('click', function(e) {
  if (e.target === this) closeBuyModal();
});
