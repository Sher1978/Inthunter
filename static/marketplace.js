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
    if (typeof twa.disableVerticalSwipes === 'function') {
      twa.disableVerticalSwipes();
    }
    twa.isVerticalSwipesEnabled = false;
    // Apply TMA theme colors
    document.documentElement.style.setProperty('--bg', twa.themeParams?.bg_color || '#0F1117');
  }
  initAuth();
});

async function initAuth() {
  const twa = window.Telegram?.WebApp;

  // Case 0: Direct login redirect from Telegram Bot via URL parameters (auth_token or token)
  const urlParams = new URLSearchParams(window.location.search);
  const urlAuthToken = urlParams.get('auth_token');
  const urlToken = urlParams.get('token');

  if (urlAuthToken) {
    localStorage.setItem('radar_tma_token', urlAuthToken);
    window.history.replaceState({}, document.title, window.location.pathname);
    try {
      const me = await apiFetch('/me');
      if (me && me.id) {
        currentUser = me;
        showApp();
        return;
      }
    } catch (e) {
      console.error('URL auth_token verification error:', e);
    }
  }

  if (urlToken) {
    try {
      const resp = await fetch(`${API}/web-login-status?token=${urlToken}`);
      const data = await resp.json();
      if (data.status === 'approved' && data.token) {
        localStorage.setItem('radar_tma_token', data.token);
        window.history.replaceState({}, document.title, window.location.pathname);
        const me = await apiFetch('/me');
        if (me && me.id) {
          currentUser = me;
          showApp();
          return;
        }
      }
    } catch (e) {
      console.error('URL token verification error:', e);
    }
  }

  // Case 1: Inside Telegram TMA with initData or initDataUnsafe
  const rawInitData = twa?.initData || (twa?.initDataUnsafe?.user?.id ? `user=${encodeURIComponent(JSON.stringify(twa.initDataUnsafe.user))}` : '');
  if (rawInitData) {
    try {
      const resp = await fetch(`${API}/auth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: rawInitData })
      });
      const data = await resp.json();
      if (data.status === 'ok' && data.token) {
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
  updateCartBadge();
}

function showAuthScreen() {
  document.getElementById('loading-screen').style.display = 'none';
  document.getElementById('app').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'flex';
}

function updateBalanceDisplay(bal) {
  document.getElementById('balance-value').textContent = parseFloat(bal).toFixed(2);
}

let currentLoc = 'all';
let currentStatusFilter = 'AVAILABLE';

// ─── Status Filter ─────────────────────────────────────────────────────────
function setStatusFilter(status, el) {
  currentStatusFilter = status;
  document.querySelectorAll('#status-filter-row .chip').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
  fetchLeads();
}

// ─── Niche Filter ─────────────────────────────────────────────────────────
function setNicheFilter(niche, el) {
  currentNiche = niche;
  document.querySelectorAll('#niche-filter-row .chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  fetchLeads();
}

// ─── Geo Location Filter ──────────────────────────────────────────────────
function setLocationFilter(loc, el) {
  currentLoc = loc;
  document.querySelectorAll('#location-filter-row .chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  fetchLeads();
}

// ─── Deposit Modal ────────────────────────────────────────────────────────
function openDepositModal() {
  const bal = parseFloat(currentUser?.balance || 0).toFixed(2);
  const el = document.getElementById('deposit-modal-balance');
  if (el) el.textContent = bal;
  document.getElementById('deposit-modal').classList.add('show');
}

function closeDepositModal() {
  document.getElementById('deposit-modal').classList.remove('show');
}

function redirectToBotDeposit() {
  closeDepositModal();
  const botLink = "https://t.me/intenthunter_bot?start=deposit";
  const twa = window.Telegram?.WebApp;
  if (twa?.openTelegramLink) {
    twa.openTelegramLink(botLink);
  } else {
    window.open(botLink, '_blank');
  }
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
    const locParam = currentLoc !== 'all' ? `&location=${currentLoc}` : '';
    const statusParam = `&status=${currentStatusFilter}`;
    const leads = await apiFetch(`/leads?limit=50${nicheParam}${locParam}${statusParam}`);
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

    const ttlMins = lead.ttl_remaining_minutes != null ? lead.ttl_remaining_minutes : 180;
    const ttlHrs = Math.floor(ttlMins / 60);
    const ttlRemMins = ttlMins % 60;
    const ttlLabel = lead.is_archived
      ? '<span class="badge" style="background:#F1F5F9; color:#64748B; border:1px solid #CBD5E1;">📦 В архиве</span>'
      : `<span class="badge" style="background:#FFFBEB; color:#B45309; border:1px solid #FDE68A;" title="Через ${ttlMins} мин лид будет перенесен в архив">⏳ До архива: ${ttlHrs > 0 ? ttlHrs + 'ч ' : ''}${ttlRemMins}м</span>`;

    return `
    <div class="lead-card" id="lead-card-${lead.id}">
      <div class="lead-card-top">
        <div class="lead-badges">
          <span class="badge ${tempClass}">${tempLabel}</span>
          <span class="badge badge-niche">${lead.niche_name}</span>
          <span class="badge badge-location">${lead.location_name}</span>
          ${ttlLabel}
        </div>
        <div class="lead-price">$${parseFloat(lead.price).toFixed(2)}</div>
      </div>
      <div class="lead-intent">${escapeHtml(lead.intent_summary)}</div>
      <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
        <span>💬 Сообщений в системе: <strong>${lead.user_message_count || 1}</strong></span>
        ${['ADMIN', 'SUPERADMIN'].includes(currentUser?.role || '') ? `<button class="btn-buy" style="padding:3px 8px; font-size:11px; background:rgba(255,255,255,0.08);" onclick="openTmaDecryptModal(${lead.user_id})">📜 История сообщений</button>` : ''}
      </div>
      <div class="lead-footer">
        <div>
          <div class="confidence-bar">
            <div class="conf-track"><div class="conf-fill" style="width:${conf}%"></div></div>
            <span class="conf-label">${conf}%</span>
          </div>
          <div class="lead-time">${date}</div>
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn-buy" style="background: linear-gradient(135deg, #F59E0B, #EA580C); box-shadow: 0 3px 12px rgba(234,88,12,0.35); padding: 8px 12px; font-size:12px;" onclick="openBuyModal('${lead.id}')">
            🛒 Купить ($1.00)
          </button>
          <button class="btn-buy" style="background: linear-gradient(135deg, #8B5CF6, #6366F1); box-shadow: 0 3px 12px rgba(99,102,241,0.35); padding: 8px 12px; font-size:12px;" onclick="openBuyModal('${lead.id}')">
            👑 Выкупить ($10)
          </button>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ─── Fetch & Render Purchases ─────────────────────────────────────────────
async function updateCartBadge() {
  try {
    const purchases = await apiFetch('/my-purchases');
    const badge = document.getElementById('cart-badge');
    const fab = document.getElementById('cart-fab');
    if (badge && fab) {
      if (purchases && purchases.length > 0) {
        badge.textContent = purchases.length;
        badge.classList.add('show');
        fab.style.display = 'flex';
      } else {
        badge.classList.remove('show');
        fab.style.display = 'none';
      }
    }
  } catch (e) {
    console.error('Error fetching cart badge count', e);
  }
}

async function fetchPurchases() {
  const container = document.getElementById('purchases-container');
  container.innerHTML = '<div class="empty-state"><div class="spinner" style="margin:0 auto"></div></div>';
  try {
    const purchases = await apiFetch('/my-purchases');
    updateCartBadge();
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
    const isVip = parseFloat(p.price_paid || 1.0) >= 9.0;
    const vipBadge = isVip ? '<span class="badge badge-hot" style="margin-left:4px;">⭐ V.I.P. Выкуп</span>' : '';

    let contactHtml = '';
    if (p.contact) {
      contactHtml = `
      <div class="purchase-contact" style="background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.3); border-radius: 8px; padding: 12px; margin-top: 12px;">
        <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 4px;">👤 Контакт для связи:</div>
        <div style="font-size: 16px; font-weight: 700; color: #6EE7B7; margin-bottom: 4px;">${escapeHtml(p.contact.full_name)}</div>
        <a href="${p.contact.tg_link}" target="_blank" style="display: inline-block; background: #10B981; color: #FFF; padding: 6px 12px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none; margin-top: 4px;">
          👉 Написать в Telegram (${p.contact.username})
        </a>
      </div>`;
    } else if (p.user_id) {
      contactHtml = `<div class="purchase-contact">ID ${p.user_id} — напишите через Telegram Bot: /contact_${p.user_id}</div>`;
    }

      let sourceHtml = '';
      if (p.source && (p.source.title || p.source.username)) {
         let srcName = p.source.title || p.source.username;
         sourceHtml = `
         <div style="background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.3); border-radius: 8px; padding: 12px; margin-top: 12px;">
           <div style="font-size: 13px; color: #64748B; margin-bottom: 4px;">📢 Источник лида:</div>
           <div style="font-size: 14px; font-weight: 600; color: #2563EB;">${escapeHtml(srcName)}</div>
           ${p.source.username ? `<a href="https://t.me/${p.source.username.replace('@', '')}" target="_blank" style="display: inline-block; color: #3B82F6; font-size: 13px; text-decoration: none; margin-top: 4px;">🔗 Перейти в канал</a>` : ''}
         </div>`;
      }

    return `
    <div class="purchase-card">
      <div class="purchase-card-header">
        <div>
          <span class="badge badge-niche" style="margin-bottom:4px;display:inline-block">${p.niche_name}</span>
          <span class="badge badge-location">${p.location_name}</span>
          ${vipBadge}
        </div>
        <div style="font-size:13px;color:var(--text-dim)">${p.purchased_at ? new Date(p.purchased_at).toLocaleString('ru-RU', {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : ''}</div>
      </div>
      <div style="font-size:14px;color:var(--text);margin-bottom:8px;">${escapeHtml(p.intent_summary)}</div>
      ${sourceHtml}
      ${contactHtml}
      <div style="font-size:12px;color:var(--text-dim);margin-top:12px;">💳 Оплачено: $${parseFloat(p.price_paid).toFixed(2)} USD</div>
    </div>`;
  }).join('');
}

async function openTmaDecryptModal(userId) {
  try {
    const res = await fetch(`/api/user/${userId}/messages`);
    const logs = await res.json();
    if (!logs || logs.length === 0) {
      showToast('Сообщения не найдены', 'error');
      return;
    }
    const text = logs.map((l, i) => `${i+1}. [${l.timestamp}] ${l.chat_title}:\n"${l.message_text}"`).join('\n\n');
    alert(`🔍 РАСШИФРОВКА СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ (ID ${userId}):\n\n${text}`);
  } catch (err) {
    showToast('Ошибка загрузки расшифровки', 'error');
  }
}

// ─── Buy Modal ────────────────────────────────────────────────────────────
function openBuyModal(leadId) {
  selectedLead = currentLeads.find(l => l.id === leadId);
  if (!selectedLead) return;

  document.getElementById('modal-intent-text').textContent = selectedLead.intent_summary;
  document.getElementById('modal-balance').textContent = parseFloat(currentUser?.balance || 0).toFixed(2);
  document.getElementById('buy-modal').classList.add('show');
}

function closeBuyModal() {
  document.getElementById('buy-modal').classList.remove('show');
  selectedLead = null;
}

async function confirmBuy(isExclusive = false) {
  if (!selectedLead) return;
  const btnStd = document.getElementById('btn-buy-std');
  const btnExcl = document.getElementById('btn-buy-excl');

  if (btnStd) btnStd.disabled = true;
  if (btnExcl) btnExcl.disabled = true;

  try {
    const result = await apiFetch(`/leads/${selectedLead.id}/buy?is_exclusive=${isExclusive}`, {
      method: 'POST',
      body: JSON.stringify({ is_exclusive: isExclusive })
    });

    if (result.status === 'ok') {
      currentUser.balance = result.new_balance;
      updateBalanceDisplay(result.new_balance);
      closeBuyModal();
      
      const successMsg = isExclusive
        ? '👑 Лид выкуплен эксклюзивно в 1 руки!'
        : '🛒 Контакт лида успешно куплен! Проверьте «Мои покупки»';
      showToast(successMsg, 'success');

      if (isExclusive) {
        currentLeads = currentLeads.filter(l => l.id !== selectedLead?.id);
        const card = document.getElementById(`lead-card-${selectedLead?.id}`);
        if (card) {
          card.style.opacity = '0.3';
          card.style.pointerEvents = 'none';
        }
      }
      setTimeout(() => {
        fetchLeads();
        updateCartBadge();
      }, 1200);
    } else if (result.status === 'insufficient_balance') {
      closeBuyModal();
      showToast(`⚠️ Недостаточно средств (${result.message}). Пополните баланс командой /deposit в боте`, 'error', 4000);
    } else {
      showToast(`❌ ${result.message || 'Ошибка покупки'}`, 'error');
    }
  } catch (e) {
    showToast('❌ Ошибка сети при покупке', 'error');
  } finally {
    if (btnStd) btnStd.disabled = false;
    if (btnExcl) btnExcl.disabled = false;
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
      if (statusEl) {
        statusEl.textContent = '✅ Авторизован! Загружаем маркетплейс...';
        statusEl.className = 'success';
      }
      try {
        const me = await apiFetch('/me');
        if (me && me.id) currentUser = me;
      } catch (e) {
        console.warn('Profile fetch warning:', e);
      }
      showApp();

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
