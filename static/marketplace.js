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
    // Always enforce sleek dark background
    document.documentElement.style.setProperty('--bg', '#0F1117');
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
  const tgUser = twa?.initDataUnsafe?.user;
  const rawInitData = twa?.initData || (tgUser?.id ? `user=${encodeURIComponent(JSON.stringify(tgUser))}` : '');
  if (rawInitData || tgUser?.id) {
    try {
      const resp = await fetch(`${API}/auth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          init_data: rawInitData || '',
          user_id: tgUser?.id,
          first_name: tgUser?.first_name,
          username: tgUser?.username
        })
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

  // Show admin banner ONLY if admin/superadmin
  const role = (currentUser?.role || '').toUpperCase();
  const banner = document.getElementById('admin-banner');
  if (banner) {
    if (['ADMIN', 'SUPERADMIN'].includes(role)) {
      banner.style.setProperty('display', 'flex', 'important');
    } else {
      banner.style.setProperty('display', 'none', 'important');
    }
  }

  // Update header
  const name = currentUser?.company_name || currentUser?.first_name || 'Партнёр';
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

async function toggleBotSub(nicheCode, locationCode, btnEl) {
  try {
    const res = await apiFetch('/toggle-subscription', {
      method: 'POST',
      body: JSON.stringify({ niche_code: nicheCode, location_code: locationCode })
    });
    
    if (res.status === 'ok') {
      if (res.is_active) {
        btnEl.classList.add('active');
        btnEl.innerHTML = '⚡ получать такие лиды в бот';
      } else {
        btnEl.classList.remove('active');
        btnEl.innerHTML = 'НЕ ПРИСЫЛАТЬ В BOT';
      }
      
      // Update local state if needed
      const me = await apiFetch('/me');
      if (me && me.id) {
        currentUser = me;
      }
      
      showToast(res.message, 'success');
    } else {
      showToast(res.message || 'Ошибка изменения подписки', 'error');
    }
  } catch (err) {
    showToast('Ошибка сети. Попробуйте еще раз.', 'error');
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

  const subNiches = currentUser?.subscribed_niches || ['all'];
  const subLocs = currentUser?.subscribed_locations || ['all'];

  container.innerHTML = leads.map(lead => {
    // If purchased, render the exact same purchase card as in the Cart
    if (lead.is_purchased_by_me && lead.purchase_details) {
      const p = {
        ...lead.purchase_details,
        lead_type_label: lead.lead_type_label,
        niche_name: lead.niche_name,
        location_name: lead.location_name,
        intent_summary: lead.quote_text || lead.intent_summary,
        user_id: lead.user_id,
        source: lead.purchase_details.source || {}
      };
      return renderPurchaseCard(p);
    }

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

    const leadTypeBadge = `<span class="badge" style="background:${lead.lead_type_bg || '#D1FAE5'}; color:${lead.lead_type_color || '#10B981'}; font-weight:700; border:1px solid ${lead.lead_type_color || '#10B981'}44;">${lead.lead_type_label || '🎯 Лид'}</span>`;
    const displayText = lead.quote_text || lead.intent_summary || '';

    // Bot subscription state for this lead's niche & location
    const isSubscribed = (subNiches.includes('all') || subNiches.includes(lead.niche_code)) &&
                         (subLocs.includes('all') || subLocs.includes(lead.location_code));
    const subBtnText = isSubscribed ? '⚡ получать такие лиды в бот' : 'НЕ ПРИСЫЛАТЬ В BOT';
    const subBtnClass = isSubscribed ? 'active' : '';

    let purchasedHtml = '';
    if (lead.is_purchased_by_me && lead.purchase_details) {
      const pur = lead.purchase_details;
      purchasedHtml = `
      <div style="margin-top: 10px; background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.3); border-radius: 8px; padding: 10px 12px; font-size: 13px;">
        <div style="color: #047857; font-weight: 700; margin-bottom: 4px;">👤 Контакт: ${escapeHtml(pur.contact.full_name)} (${escapeHtml(pur.contact.username)})</div>
        ${pur.contact.tg_link ? `<a href="${pur.contact.tg_link}" target="_blank" style="display:inline-block; background:#10B981; color:#FFF; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:700; text-decoration:none; margin-top:2px;">👉 Написать в Telegram</a>` : ''}
      </div>`;
    }

    const purchasedBadge = lead.is_purchased_by_me ? `<span class="badge" style="background:rgba(16,185,129,0.2); color:#10B981; border:1px solid rgba(16,185,129,0.4);">✅ Вы выкупили этот лид</span>` : '';

    const unitP = parseFloat(lead.price || 1.0).toFixed(2);
    const exclP = (lead.exclusive_price ? parseFloat(lead.exclusive_price) : (unitP * 10)).toFixed(0);

    const actionButtons = lead.is_purchased_by_me
      ? `<div style="font-size:12px; color:#10B981; font-weight:700; padding:6px 12px; background:rgba(16,185,129,0.15); border-radius:8px;">✅ Выкуплено</div>`
      : `<div style="display:flex; gap:8px;">
          <button class="btn-buy" style="background: linear-gradient(135deg, #F59E0B, #EA580C); box-shadow: 0 3px 12px rgba(234,88,12,0.35); padding: 8px 12px; font-size:12px;" onclick="openBuyModal('${lead.id}')">
            🛒 Купить ($${unitP})
          </button>
          <button class="btn-buy" style="background: linear-gradient(135deg, #8B5CF6, #6366F1); box-shadow: 0 3px 12px rgba(99,102,241,0.35); padding: 8px 12px; font-size:12px;" onclick="openBuyModal('${lead.id}')">
            👑 Выкупить ($${exclP})
          </button>
        </div>`;

    return `
    <div class="lead-card" id="lead-card-${lead.id}">
      <div class="lead-card-top">
        <div class="lead-badges">
          ${leadTypeBadge}
          <span class="badge ${tempClass}">${tempLabel}</span>
          <span class="badge badge-niche">${lead.niche_name}</span>
          <span class="badge badge-location">${lead.location_name}</span>
          ${purchasedBadge}
          ${ttlLabel}
        </div>
        <div class="lead-price">$${unitP}</div>
      </div>
      <div class="lead-intent" style="font-style: italic; background: rgba(255,255,255,0.04); padding: 10px 12px; border-radius: 8px; border-left: 3px solid ${lead.lead_type_color || '#10B981'}; margin-bottom: 12px; word-break: break-word;">
        "${escapeHtml(displayText)}"
      </div>
      ${purchasedHtml}
      <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
        <span>💬 Сообщений в системе: <strong>${lead.user_message_count || 1}</strong></span>
        ${['ADMIN', 'SUPERADMIN'].includes((currentUser?.role || '').toUpperCase()) ? `<button class="btn-buy" style="padding:3px 8px; font-size:11px; background:rgba(255,255,255,0.08);" onclick="openTmaDecryptModal(${lead.user_id})">📜 История сообщений</button>` : ''}
      </div>
      <div class="lead-footer">
        <div>
          <div class="confidence-bar">
            <div class="conf-track"><div class="conf-fill" style="width:${conf}%"></div></div>
            <span class="conf-label">${conf}%</span>
          </div>
          <div class="lead-time">${date}</div>
        </div>
        ${actionButtons}
      </div>
      <button class="btn-bot-sub ${subBtnClass}" onclick="toggleBotSub('${lead.niche_code}', '${lead.location_code}', this)">
        ${subBtnText}
      </button>
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

  container.innerHTML = purchases.map(p => renderPurchaseCard(p)).join('');
}

function renderPurchaseCard(p) {
    const date = p.purchased_at ? new Date(p.purchased_at).toLocaleString('ru-RU', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    }) : '';
    const isVip = parseFloat(p.price_paid || 1.0) >= 9.0;
    const vipBadge = isVip ? '<span class="badge badge-hot" style="margin-left:4px;">⭐ V.I.P. Выкуп</span>' : '';

    let msgLink = (p.source && p.source.message_url) ? p.source.message_url : '';
    if (!msgLink && p.source && p.source.message_id) {
      if (p.source.username) {
          msgLink = `https://t.me/${p.source.username.replace('@', '')}/${p.source.message_id}`;
      } else if (p.source.chat_id) {
          let cid = String(p.source.chat_id).replace('-100', '');
          msgLink = `https://t.me/c/${cid}/${p.source.message_id}`;
      }
    }

    let chatLink = (p.source && p.source.chat_url) ? p.source.chat_url : ((p.source && p.source.group_url) ? p.source.group_url : '');
    if (!chatLink && p.source && p.source.username) {
      chatLink = `https://t.me/${p.source.username.replace('@', '')}`;
    } else if (!chatLink && p.source && p.source.invite_link) {
      chatLink = p.source.invite_link;
    } else if (!chatLink && p.source && p.source.chat_id) {
      let cid = String(p.source.chat_id).replace('-100', '');
      chatLink = `https://t.me/c/${cid}`;
    }

    let isPrivateGroup = !p.source.username && p.source.chat_id;
    let inviteLink = p.source.invite_link && p.source.invite_link.includes('+') ? p.source.invite_link : '';

    let userTgLink = (p.contact && p.contact.tg_link) ? p.contact.tg_link : (p.user_id ? `tg://user?id=${p.user_id}` : '');

    let contactHtml = '';
    if (p.contact || p.user_id) {
      let contactObj = p.contact || {};
      let fullName = contactObj.full_name || 'Пользователь Telegram';
      let usernameDisplay = contactObj.username || (p.user_id ? `ID ${p.user_id}` : 'Скрыт');
      let isNoUsername = contactObj.no_username || (!contactObj.username || contactObj.username.includes('ID '));

      let buttonsHtml = '';
      if (!isNoUsername && contactObj.tg_link && !contactObj.tg_link.includes('tg://user')) {
        buttonsHtml += `
          <a href="${contactObj.tg_link}" target="_blank" style="display: inline-block; background: #10B981; color: #FFF; padding: 7px 12px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none;">
            👉 Написать лиду в Telegram
          </a>
        `;
      }
      
      if (msgLink) {
        buttonsHtml += `
          <a href="${msgLink}" target="_blank" style="display: inline-block; background: #3B82F6; color: #FFF; padding: 7px 12px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none;">
            🔗 Открыть сообщение лида
          </a>
        `;
      }
      
      if (chatLink) {
        buttonsHtml += `
          <a href="${chatLink}" target="_blank" style="display: inline-block; background: #6366F1; color: #FFF; padding: 7px 12px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none;">
            📢 Открыть чат (вступить)
          </a>
        `;
      }

      contactHtml = `
      <div class="purchase-contact" style="background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.3); border-radius: 8px; padding: 12px; margin-top: 12px;">
        <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 4px;">👤 Контакт для связи:</div>
        <div style="font-size: 16px; font-weight: 700; color: #047857; margin-bottom: 4px;">${escapeHtml(fullName)} (${escapeHtml(usernameDisplay)})</div>
        
        <div style="display: flex; flex-direction: column; gap: 6px; margin-top: 6px;">
          ${buttonsHtml}
        </div>

        ${isNoUsername ? `<div style="margin-top:8px; font-size:12px; color:#D97706; background:rgba(254,243,199,0.1); padding:8px; border-radius:4px; border: 1px solid rgba(217,119,6,0.3);">⚠️ У пользователя скрыт юзернейм (Privacy Telegram). Перейдите к сообщению в чате (кнопка выше), чтобы написать ему напрямую через профиль.</div>` : ''}
      </div>`;
    }

      let sourceHtml = '';
      if (p.source && (p.source.title || p.source.username || chatLink)) {
         let srcName = p.source.title || (p.source.username ? `@${p.source.username}` : 'Телеграм группа');
         
         sourceHtml = `
         <div style="background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.3); border-radius: 8px; padding: 12px; margin-top: 12px;">
           <div style="font-size: 13px; color: #94A3B8; margin-bottom: 4px;">📢 Источник лида (Чат / Группа):</div>
           <div style="font-size: 15px; font-weight: 700; color: #60A5FA; margin-bottom: 8px;">${escapeHtml(srcName)}</div>
           
           <div style="display: flex; flex-direction: column; gap: 6px;">
             ${chatLink ? `
               <a href="${chatLink}" target="_blank" style="display: inline-flex; align-items: center; gap: 6px; background: rgba(59,130,246,0.2); color: #93C5FD; border: 1px solid rgba(59,130,246,0.4); padding: 7px 12px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none;">
                 💬 1. Вступить / Открыть группу (${escapeHtml(srcName)})
               </a>
             ` : ''}
             
             ${msgLink ? `
               <a href="${msgLink}" target="_blank" style="display: inline-flex; align-items: center; gap: 6px; color: #60A5FA; font-size: 13px; text-decoration: underline; padding-left: 4px; margin-top: 2px;">
                 🔗 2. Перейти к сообщению в группе
               </a>
             ` : ''}
           </div>
         </div>`;
      }

    const leadTypeBadge = `<span class="badge" style="background:#EEF2FF; color:#4F46E5; border:1px solid #C7D2FE; margin-right:4px;">${p.lead_type_label || '🎯 Лид'}</span>`;

    return `
    <div class="purchase-card">
      <div class="purchase-card-header">
        <div>
          ${leadTypeBadge}
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
document.getElementById('buy-modal')?.addEventListener('click', function(e) {
  if (e.target === this) closeBuyModal();
});

document.getElementById('deposit-modal')?.addEventListener('click', function(e) {
  if (e.target === this) closeDepositModal();
});

document.getElementById('profile-modal')?.addEventListener('click', function(e) {
  if (e.target === this) closeProfileModal();
});

// ─── Bot Subscription Toggle ──────────────────────────────────────────────
async function toggleBotSub(nicheCode, locCode, btnEl) {
  try {
    const res = await apiFetch('/toggle-subscription', {
      method: 'POST',
      body: JSON.stringify({ niche_code: nicheCode, location_code: locCode })
    });
    if (res.status === 'ok') {
      currentUser.subscribed_niches = res.subscribed_niches;
      currentUser.subscribed_locations = res.subscribed_locations;
      
      const isNowEnabled = res.is_enabled;
      showToast(
        isNowEnabled
          ? '⚡ Уведомления включены! Новые лиды будут приходить в бот'
          : '🔕 Доставка лидов в бот отключена',
        isNowEnabled ? 'success' : 'info'
      );

      fetchLeads();
      if (document.getElementById('profile-modal')?.classList.contains('show')) {
        openProfileModal();
      }
    }
  } catch (e) {
    showToast('❌ Ошибка изменения подписки', 'error');
  }
}

// ─── Profile Modal Logic ──────────────────────────────────────────────────
function openProfileModal() {
  if (!currentUser) return;
  const nameEl = document.getElementById('profile-modal-name');
  if (nameEl) nameEl.textContent = currentUser.company_name || currentUser.first_name || 'Партнёр';
  
  const tgidEl = document.getElementById('profile-modal-tgid');
  if (tgidEl) tgidEl.textContent = `ID: ${currentUser.telegram_id || currentUser.id || '-'}`;
  
  const roleEl = document.getElementById('profile-modal-role');
  if (roleEl) roleEl.textContent = currentUser.role || 'PARTNER';

  const balEl = document.getElementById('profile-modal-balance');
  if (balEl) balEl.textContent = parseFloat(currentUser.balance || 0).toFixed(2);
  
  const webhookInput = document.getElementById('profile-webhook-url');
  if (webhookInput) webhookInput.value = currentUser.webhook_url || '';

  // Render active niches pills
  const nichesContainer = document.getElementById('profile-niches-list');
  if (nichesContainer) {
    const allNiches = [
      { code: 'real_estate', label: '🏠 Недвижимость' },
      { code: 'bike_rent', label: '🛵 Аренда байков' },
      { code: 'currency_exchange', label: '💱 Обмен валюты' },
      { code: 'services_visa', label: '🛂 Визы & Услуги' },
      { code: 'auto_kasko', label: '🚗 Страхование' }
    ];
    const subNiches = currentUser.subscribed_niches || ['all'];
    
    nichesContainer.innerHTML = allNiches.map(n => {
      const isSub = subNiches.includes('all') || subNiches.includes(n.code);
      return `<div class="chip ${isSub ? 'active' : ''}" style="font-size:11px; padding:4px 10px;" onclick="toggleBotSub('${n.code}', 'all', this)">${n.label} ${isSub ? '✓' : ''}</div>`;
    }).join('');
  }

  document.getElementById('profile-modal').classList.add('show');
}

function closeProfileModal() {
  document.getElementById('profile-modal').classList.remove('show');
}

async function saveProfileWebhook() {
  const url = document.getElementById('profile-webhook-url').value.trim();
  try {
    const res = await apiFetch('/profile/settings', {
      method: 'POST',
      body: JSON.stringify({ webhook_url: url })
    });
    if (res.status === 'ok') {
      currentUser.webhook_url = url;
      showToast('✅ Webhook URL успешно сохранён!', 'success');
    }
  } catch (e) {
    showToast('❌ Ошибка сохранения Webhook', 'error');
  }
}

async function submitProfileWithdraw() {
  const details = document.getElementById('profile-withdraw-details').value.trim();
  if (!details) {
    showToast('⚠️ Укажите реквизиты для вывода', 'error');
    return;
  }
  try {
    const res = await apiFetch('/withdraw', {
      method: 'POST',
      body: JSON.stringify({ details: details })
    });
    if (res.status === 'ok') {
      showToast(res.message, 'success', 4000);
      document.getElementById('profile-withdraw-details').value = '';
    }
  } catch (e) {
    showToast(`❌ ${e.message || 'Ошибка запроса вывода'}`, 'error', 3500);
  }
}

function logoutApp() {
  localStorage.removeItem('radar_tma_token');
  window.location.reload();
}
