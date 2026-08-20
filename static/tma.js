// RADAR AI Lead Engine - Telegram Mini App (TMA) Client Logic

let tgWebApp = null;
let currentRubric = 'all';

document.addEventListener('DOMContentLoaded', () => {
  initTelegramWebApp();
  initRubricChips();
  fetchLeadsFeed();
  initActionHandlers();

  // Refresh live feed every 8 seconds
  setInterval(fetchLeadsFeed, 8000);
});

// 1. Init Telegram WebApp SDK
function initTelegramWebApp() {
  if (window.Telegram && window.Telegram.WebApp) {
    tgWebApp = window.Telegram.WebApp;
    tgWebApp.ready();
    tgWebApp.expand();

    // Set header color
    if (tgWebApp.setHeaderColor) {
      tgWebApp.setHeaderColor('#0B0F19');
    }

    // Set user name pill if opened inside Telegram
    const tgUser = tgWebApp.initDataUnsafe ? tgWebApp.initDataUnsafe.user : null;
    const userNameElem = document.getElementById('user-name');
    if (tgUser && userNameElem) {
      const displayName = tgUser.first_name || tgUser.username || 'Пользователь';
      userNameElem.textContent = displayName;
    }
  }
}

// Haptic feedback helper
function triggerHaptic(type = 'impact', style = 'medium') {
  if (tgWebApp && tgWebApp.HapticFeedback) {
    if (type === 'impact') {
      tgWebApp.HapticFeedback.impactOccurred(style);
    } else if (type === 'notification') {
      tgWebApp.HapticFeedback.notificationOccurred(style);
    }
  }
}

// 2. Rubric Filter Chips
function initRubricChips() {
  const chips = document.querySelectorAll('.chip');
  chips.forEach(chip => {
    chip.addEventListener('click', () => {
      triggerHaptic('impact', 'light');
      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      currentRubric = chip.getAttribute('data-rubric');
      fetchLeadsFeed();
    });
  });
}

// 3. Fetch Live Leads Feed
async function fetchLeadsFeed() {
  const container = document.getElementById('tma-leads-feed');
  if (!container) return;

  try {
    let url = '/api/leads?limit=10';
    if (currentRubric !== 'all') {
      url += `&niche=${currentRubric}`;
    }

    const res = await fetch(url);
    if (!res.ok) return;
    const leads = await res.json();

    renderLeadsFeed(leads);
  } catch (err) {
    console.error('Error fetching TMA leads:', err);
  }
}

function renderLeadsFeed(leads) {
  const container = document.getElementById('tma-leads-feed');
  if (!container) return;

  if (!leads || leads.length === 0) {
    container.innerHTML = `
      <div style="padding: 24px; text-align: center; color: var(--text-dim); font-size: 13px;">
        🔍 В этой рубрике пока нет активных лидов. Сканер опрашивает чаты...
      </div>
    `;
    return;
  }

  const rubricLabels = {
    real_estate: '🏠 Недвижимость',
    currency_exchange: '💱 Обмен валюты',
    bike_rent: '🛵 Аренда байков',
    services_visa: '🛂 Визы & Услуги',
    auto_kasko: '🚗 Автострахование'
  };

  container.innerHTML = leads.map(l => {
    const rLabel = l.rubric_name || rubricLabels[l.niche_code] || l.niche_code;
    const isHot = l.temperature === 'HOT';

    return `
      <div class="tma-lead-card">
        <div class="tma-lead-head">
          <span class="niche-tag">${escapeHtml(rLabel)}</span>
          <span class="temp-tag ${l.temperature}">${isHot ? '🔥 HOT' : '⚡ WARM'}</span>
        </div>

        <div class="tma-lead-intent">
          "${escapeHtml(l.intent_summary)}"
        </div>

        <div class="tma-lead-hook">
          💡 <strong>Sales Hook:</strong> «${escapeHtml(l.sales_hook)}»
        </div>

        <div class="tma-lead-foot">
          <span class="price-tag">$${l.price.toFixed(2)} USD</span>
          <button class="btn-buy-lead" onclick="claimLead('${l.id}', '${l.price}')">
            Выкупить контакты
          </button>
        </div>
      </div>
    `;
  }).join('');
}

// 4. Actions & Callbacks
function initActionHandlers() {
  const btnHero = document.getElementById('btn-hero-start');
  if (btnHero) {
    btnHero.addEventListener('click', () => {
      triggerHaptic('impact', 'heavy');
      openTelegramBot();
    });
  }

  const btnBottom = document.getElementById('btn-bottom-cta');
  if (btnBottom) {
    btnBottom.addEventListener('click', () => {
      triggerHaptic('impact', 'heavy');
      openTelegramBot();
    });
  }

  const btnGrok = document.getElementById('btn-grok-submit');
  if (btnGrok) {
    btnGrok.addEventListener('click', () => {
      triggerHaptic('impact', 'medium');
      handleGrokSearch();
    });
  }
}

function openTelegramBot() {
  if (tgWebApp) {
    tgWebApp.close();
  } else {
    window.location.href = 'https://t.me/OutreachAiBot';
  }
}

function claimLead(leadId, price) {
  triggerHaptic('notification', 'success');
  alert(`⚡ Выкупаем контакт лида #${leadId} за $${price} USD...\nКонтакт отправлен в ваш Telegram бот RADAR!`);
}

function buyTariff(name, price) {
  triggerHaptic('notification', 'success');
  alert(`💎 Активация тарифного плана ${name} ($${price} USD)...\nЗаполните баланс в боте для автоматического подключения!`);
}

async function handleGrokSearch() {
  const input = document.getElementById('tma-grok-input');
  const box = document.getElementById('tma-grok-candidates-box');
  if (!input || !input.value.trim()) return;

  const kw = input.value.trim();
  box.style.display = 'block';
  box.innerHTML = `<div style="padding: 10px; color: var(--text-muted); font-size: 12px;">🤖 Grok ищет релевантные чаты по запросу "${escapeHtml(kw)}"...</div>`;

  try {
    const res = await fetch('/api/grok/search-channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keywords: kw, niche_code: 'general' })
    });

    if (res.ok) {
      const data = await res.json();
      const candidates = data.candidates || [];
      if (candidates.length === 0) {
        box.innerHTML = `<div style="padding: 10px; color: var(--text-dim); font-size: 12px;">По запросу ничего не найдено.</div>`;
      } else {
        box.innerHTML = `
          <div style="font-size: 12px; font-weight: 700; margin-bottom: 8px; color: var(--primary-cyan);">🎯 Найдено ${candidates.length} целевых чатов:</div>
          ${candidates.slice(0, 3).map(c => `
            <div style="background: rgba(0,0,0,0.3); padding: 8px 10px; border-radius: 8px; margin-bottom: 6px; font-size: 12px; display: flex; justify-content: space-between; align-items: center;">
              <div>
                <strong>${escapeHtml(c.title)}</strong>
                <div style="color: var(--primary-cyan); font-size: 11px;">${escapeHtml(c.username)}</div>
              </div>
              <button style="background: var(--primary-cyan); border: none; padding: 4px 8px; border-radius: 4px; font-size: 10px; font-weight: 800;" onclick="approveGrokTMA('${escapeHtml(c.username)}')">➕ Вступить</button>
            </div>
          `).join('')}
        `;
      }
    }
  } catch (err) {
    console.error('Error Grok TMA search:', err);
  }
}

async function approveGrokTMA(username) {
  triggerHaptic('notification', 'success');
  alert(`✅ Группа ${username} отправлена в очередь подключения юзербота!`);
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
