/* FaxClip: session (plan, features, announcement) + admin panel for the service owner. */
(function () {
  const A = { tab: 'overview', q: '', filter: 'all', errStatus: 'open', jobStatus: '' };
  const fmt = (t) =>
    t
      ? new Date(typeof t === 'number' ? t * 1000 : t).toLocaleString('ru-RU', {
          day: 'numeric',
          month: 'short',
          hour: '2-digit',
          minute: '2-digit'
        })
      : '—';
  const ago = (t) => {
    if (!t) return 'никогда';
    const m = Math.round((Date.now() - new Date(t).getTime()) / 60000);
    if (m < 2) return 'сейчас';
    if (m < 60) return m + ' мин назад';
    if (m < 1440) return Math.round(m / 60) + ' ч назад';
    return Math.round(m / 1440) + ' дн назад';
  };
  const pill = (t, red) => `<span class="pill"><i class="dot ${red ? 'red' : ''}"></i>${esc(t)}</span>`;
  const val = (id) => {
    const e = document.getElementById(id);
    return e ? (e.type === 'checkbox' ? e.checked : e.value.trim()) : '';
  };
  const ICON = {
    admin:
      '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>'
  };
  function addNav(page, label, icon, beforeSel) {
    const navEl = document.querySelector('nav');
    if (!navEl || navEl.querySelector(`[data-page="${page}"]`)) return;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'nav-item';
    b.dataset.page = page;
    b.innerHTML = icon + `<span>${label}</span>`;
    b.onclick = () => goPage(page);
    const ref = beforeSel && navEl.querySelector(beforeSel);
    ref ? ref.before(b) : navEl.append(b);
  }

  // ---------------------------------------------------------------- session
  function banner(a) {
    document.getElementById('fxBanner')?.remove();
    if (!a || !a.active || !a.text) return;
    const d = document.createElement('div');
    d.id = 'fxBanner';
    d.className = 'card fx-banner ' + (a.level || 'info');
    d.innerHTML = `<span>${esc(a.text)}</span><button class="btn secondary" aria-label="Скрыть">✕</button>`;
    d.querySelector('button').onclick = () => {
      sessionStorage.setItem('fxBannerHidden', a.text);
      d.remove();
    };
    if (sessionStorage.getItem('fxBannerHidden') === a.text) return;
    view.before(d);
  }
  function deny(msg) {
    document.querySelector('nav')?.setAttribute('hidden', '');
    shell('FaxClip', 'Доступ ограничен', `<div class="card"><h3>Нет доступа</h3><p>${esc(msg)}</p></div>`);
  }
  async function boot() {
    try {
      const s = await api('/session');
      window.FX_SESSION = s;
      const pages = {
        assembly: 'assembly',
        warmup: 'warmup',
        analytics: 'analytics',
        links: 'links',
        antiban: 'antiban'
      };
      for (const [f, p] of Object.entries(pages)) {
        const el = document.querySelector(`nav [data-page="${p}"]`);
        if (el) el.hidden = s.features && s.features[f] === false;
      }
      if (s.is_admin) addNav('admin', 'Админка', ICON.admin, '[data-page="registration"]');
      banner(s.announcement);
    } catch (e) {
      if (e.denied) deny(e.message);
    }
  }

  // ------------------------------------------------------------------ admin
  const TABS = [
    ['overview', 'Обзор'],
    ['users', 'Пользователи'],
    ['plans', 'Тарифы'],
    ['news', 'Объявления'],
    ['errors', 'Ошибки'],
    ['jobs', 'Задачи'],
    ['system', 'Система'],
    ['audit', 'Журнал']
  ];
  function tabs() {
    return `<div class="tabs ws-tabs">${TABS.map(
      ([k, l]) => `<button class="${A.tab === k ? 'active' : ''}" onclick="admTab('${k}')">${l}</button>`
    ).join('')}</div>`;
  }
  window.admTab = (t) => {
    A.tab = t;
    page();
  };
  const card = (label, value, hint = '') =>
    `<div class="card"><div class="muted">${label}</div><div class="adm-num">${value}</div>${hint ? `<div class="muted">${hint}</div>` : ''}</div>`;

  async function overview() {
    const o = await api('/admin/overview');
    const u = o.users,
      j = o.jobs.counts || {};
    return `<div class="grid adm-cards">
      ${card('Пользователей', u.total, `+${u.new_24h} за сутки · +${u.new_7d} за неделю`)}
      ${card('Активны за 24 ч', u.active_24h, `${u.active_7d} за 7 дней`)}
      ${card('Заблокировано', u.blocked)}
      ${card('Аккаунтов соцсетей', o.totals.accounts, `${o.totals.posts_today} публикаций сегодня`)}
      ${card('Устройств', o.totals.devices, `${o.totals.devices_online} онлайн`)}
      ${card('Ошибок не разобрано', o.errors.open, `${o.errors.last_24h} за сутки`)}
      ${card('Фоновые задачи', (j.queued || 0) + ' в очереди', `${j.dead || 0} упали окончательно · ${j.running || 0} выполняются`)}
      ${card(
        'Тарифы',
        '<span style="font-size:16px">' +
          (Object.entries(o.plans)
            .map(([k, v]) => `${esc(k)}: ${v}`)
            .join(' · ') || '—') +
          '</span>'
      )}
    </div>
    <div class="card"><h3>Система</h3><div class="list adm-kv">
      <div class="item"><span>База данных</span><b>${esc(o.system.database)}</b></div>
      <div class="item"><span>Фоновый обработчик</span><b>${esc(o.jobs.mode)} · пульс ${o.jobs.heartbeat_age == null ? 'нет' : o.jobs.heartbeat_age + ' с назад'}</b></div>
      <div class="item"><span>Sentry</span><b>${o.system.sentry ? 'подключён' : 'не подключён (нужен SENTRY_DSN)'}</b></div>
      <div class="item"><span>Регистрация</span><b>${o.system.registration.mode === 'open' ? 'открыта всем' : 'закрыта'}</b></div>
      <div class="item"><span>Техработы</span><b>${o.system.maintenance.on ? 'включены' : 'выключены'}</b></div>
    </div></div>`;
  }

  async function users() {
    const r = await api(`/admin/users?q=${encodeURIComponent(A.q)}&filter=${A.filter}`);
    window._admUsers = Object.fromEntries(r.users.map((u) => [u.tg_id, u]));
    const rows = r.users
      .map(
        (u) => `<tr onclick="admUser('${u.tg_id}')" style="cursor:pointer">
        <td><b>${esc(u.first_name || '—')}</b>${u.username ? ` <span class="muted">@${esc(u.username)}</span>` : ''}<div class="muted">${u.tg_id}${u.is_admin ? ' · админ' : ''}</div></td>
        <td>${pill(u.effective.plan_name)}</td>
        <td>${u.stats.accounts} / ${u.stats.devices}${u.stats.devices_online ? ` <span class="muted">(${u.stats.devices_online} онлайн)</span>` : ''}</td>
        <td>${u.stats.posts_today}</td>
        <td>${ago(u.last_seen)}<div class="muted">с ${fmt(u.created_at)}</div></td>
        <td>${u.blocked ? pill('Заблокирован', true) : pill('Активен')}</td></tr>`
      )
      .join('');
    return `<div class="card"><div class="row form ws-filter" style="gap:8px;justify-content:flex-start;flex-wrap:wrap">
      <input id="admQ" placeholder="Имя, @username или ID" value="${esc(A.q)}" onkeydown="if(event.key==='Enter')admSearch()">
      <select id="admF" onchange="admSearch()">${[
        ['all', 'Все'],
        ['active', 'Активные 7 дней'],
        ['blocked', 'Заблокированные'],
        ['free', 'Тариф free'],
        ['pro', 'Тариф pro'],
        ['unlimited', 'Без ограничений']
      ]
        .map(([k, l]) => `<option value="${k}" ${A.filter === k ? 'selected' : ''}>${l}</option>`)
        .join('')}</select>
      <button class="btn" onclick="admSearch()">Найти</button><span class="muted">Найдено: ${r.total}</span></div>
      ${r.users.length ? `<div style="overflow:auto"><table class="table"><thead><tr><th>Пользователь</th><th>Тариф</th><th>Аккаунты / устройства</th><th>Постов сегодня</th><th>Был в сети</th><th>Статус</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">Пользователей не найдено</div>'}</div>`;
  }
  window.admSearch = () => {
    A.q = val('admQ');
    A.filter = val('admF') || 'all';
    page();
  };
  window.admUser = async (id) => {
    const u = window._admUsers[id];
    const st = await api('/admin/settings');
    const plans = st.plans;
    const lim = Object.entries(st.limit_names)
      .map(
        ([k, l]) =>
          `<label>${l}<input id="admL_${k}" type="number" min="0" value="${u.limits[k] ?? ''}" placeholder="по тарифу: ${(plans[u.plan] || plans.free).limits[k] || '∞'}"></label>`
      )
      .join('');
    const fe = Object.entries(st.feature_names)
      .map(
        ([k, l]) =>
          `<label class="ws-check"><input id="admFe_${k}" type="checkbox" ${u.effective.features[k] !== false ? 'checked' : ''}> ${l}</label>`
      )
      .join('');
    modalBox(`<h3>${esc(u.first_name || u.tg_id)} ${u.username ? '@' + esc(u.username) : ''}</h3>
      <p class="muted">ID ${u.tg_id} · с ${fmt(u.created_at)} · был ${ago(u.last_seen)} · запросов ${u.requests}</p>
      <div class="form">
        <label>Тариф<select id="admPlan">${Object.entries(plans)
          .map(([k, p]) => `<option value="${k}" ${u.plan === k ? 'selected' : ''}>${esc(p.name)}</option>`)
          .join('')}</select></label>
        <h4>Индивидуальные лимиты <span class="muted">(пусто — как в тарифе, 0 — без ограничений)</span></h4>
        <div class="formgrid">${lim}</div>
        <h4>Разделы</h4><div class="formgrid">${fe}</div>
        <label>Заметка<input id="admNote" value="${esc(u.note || '')}" placeholder="Видно только админам"></label>
        <label class="ws-check"><input id="admBlocked" type="checkbox" ${u.blocked ? 'checked' : ''} ${u.is_admin ? 'disabled' : ''}> Заблокировать доступ</label>
        <label>Причина блокировки<input id="admReason" value="${esc(u.block_reason || '')}" placeholder="Пользователь увидит этот текст"></label>
        <div id="admErr"></div>
        <div class="row"><button class="close" onclick="closeModal()">Отмена</button><button class="btn" onclick="admUserSave('${u.tg_id}')">Сохранить</button></div>
        <h4>Написать в Telegram</h4>
        <textarea id="admMsg" rows="3" placeholder="Сообщение придёт от бота FaxClip"></textarea>
        <div class="row"><button class="btn secondary" onclick="admUserMsg('${u.tg_id}')">Отправить</button></div>
      </div>`);
  };
  window.admUserSave = async (id) => {
    const st = await api('/admin/settings');
    const limits = {},
      features = {};
    for (const k of Object.keys(st.limit_names)) if (val('admL_' + k) !== '') limits[k] = +val('admL_' + k);
    for (const k of Object.keys(st.feature_names)) features[k] = val('admFe_' + k);
    try {
      await api('/admin/users/' + id, {
        method: 'PATCH',
        body: JSON.stringify({
          plan: val('admPlan'),
          limits,
          features,
          note: val('admNote'),
          blocked: val('admBlocked'),
          block_reason: val('admReason')
        })
      });
      closeModal();
      page();
    } catch (e) {
      document.getElementById('admErr').innerHTML = `<div class="ws-err">${esc(e.message)}</div>`;
    }
  };
  window.admUserMsg = async (id) => {
    try {
      await api(`/admin/users/${id}/message`, {
        method: 'POST',
        body: JSON.stringify({ text: val('admMsg') })
      });
      document.getElementById('admMsg').value = '';
      document.getElementById('admErr').innerHTML =
        '<div class="muted">Сообщение поставлено в очередь отправки.</div>';
    } catch (e) {
      document.getElementById('admErr').innerHTML = `<div class="ws-err">${esc(e.message)}</div>`;
    }
  };

  async function plans() {
    const st = await api('/admin/settings');
    window._admSt = st;
    const ids = Object.keys(st.plans);
    const head = ids
      .map(
        (k) =>
          `<th><input id="admPn_${k}" value="${esc(st.plans[k].name)}"><div class="muted">${k}</div></th>`
      )
      .join('');
    const limRows = Object.entries(st.limit_names)
      .map(
        ([lk, l]) =>
          `<tr><td>${l}</td>${ids.map((k) => `<td><input type="number" min="0" id="admPl_${k}_${lk}" value="${st.plans[k].limits[lk] ?? 0}"></td>`).join('')}</tr>`
      )
      .join('');
    const feRows = Object.entries(st.feature_names)
      .map(
        ([fk, l]) =>
          `<tr><td>${l}</td>${ids.map((k) => `<td><input type="checkbox" id="admPf_${k}_${fk}" ${st.plans[k].features[fk] !== false ? 'checked' : ''}></td>`).join('')}</tr>`
      )
      .join('');
    return `<div class="card"><h3>Тарифы и лимиты</h3><p class="muted">0 — без ограничений. Новые пользователи получают тариф, выбранный во вкладке «Система». Индивидуальные лимиты задаются в карточке пользователя.</p>
      <div style="overflow:auto"><table class="table"><thead><tr><th></th>${head}</tr></thead><tbody>${limRows}<tr><td colspan="${ids.length + 1}"><b>Разделы</b></td></tr>${feRows}</tbody></table></div>
      <div id="admErr"></div><div class="row"><button class="btn" onclick="admPlansSave()">Сохранить тарифы</button></div></div>`;
  }
  window.admPlansSave = async () => {
    const st = window._admSt,
      out = {};
    for (const k of Object.keys(st.plans)) {
      out[k] = { name: val('admPn_' + k), limits: {}, features: {} };
      for (const lk of Object.keys(st.limit_names)) out[k].limits[lk] = +val(`admPl_${k}_${lk}`) || 0;
      for (const fk of Object.keys(st.feature_names)) out[k].features[fk] = val(`admPf_${k}_${fk}`);
    }
    try {
      await api('/admin/settings/plans', { method: 'PUT', body: JSON.stringify({ plans: out }) });
      page();
    } catch (e) {
      document.getElementById('admErr').innerHTML = `<div class="ws-err">${esc(e.message)}</div>`;
    }
  };

  async function news() {
    const st = await api('/admin/settings');
    const a = st.announcement;
    return `<div class="card"><h3>Баннер в приложении</h3><p class="muted">Показывается вверху у всех пользователей.</p><div class="form">
      <label>Текст<textarea id="admAnn" rows="2">${esc(a.text)}</textarea></label>
      <label>Тип<select id="admAnnL">${[
        ['info', 'Информация'],
        ['warning', 'Предупреждение'],
        ['success', 'Хорошая новость']
      ]
        .map(([k, l]) => `<option value="${k}" ${a.level === k ? 'selected' : ''}>${l}</option>`)
        .join('')}</select></label>
      <label class="ws-check"><input id="admAnnOn" type="checkbox" ${a.active ? 'checked' : ''}> Показывать</label>
      <div class="row"><button class="btn" onclick="admAnnSave()">Сохранить баннер</button></div></div></div>
      <div class="card"><h3>Рассылка в Telegram</h3><p class="muted">Сообщение придёт каждому пользователю от бота. Отправка идёт через очередь (≈20 сообщений в секунду) с повторами при сбоях.</p><div class="form">
      <textarea id="admBc" rows="4" placeholder="Текст рассылки"></textarea>
      <label>Кому<select id="admBcA"><option value="all">Всем незаблокированным</option><option value="active">Активным за 7 дней</option></select></label>
      <div id="admErr"></div>
      <div class="row"><button class="btn secondary" onclick="admBc(true)">Сколько получателей?</button><button class="btn" onclick="admBc(false)">Отправить</button></div></div></div>`;
  }
  window.admAnnSave = async () => {
    await api('/admin/settings/announcement', {
      method: 'PUT',
      body: JSON.stringify({ text: val('admAnn'), level: val('admAnnL'), active: val('admAnnOn') })
    });
    sessionStorage.removeItem('fxBannerHidden');
    boot();
    page();
  };
  window.admBc = async (dry) => {
    const box = document.getElementById('admErr');
    try {
      const body = { text: val('admBc'), audience: val('admBcA'), dry_run: dry };
      if (!dry) {
        const n = await api('/admin/broadcast', {
          method: 'POST',
          body: JSON.stringify({ ...body, dry_run: true })
        });
        if (!confirm(`Отправить сообщение ${n.recipients} пользователям?`)) return;
      }
      const r = await api('/admin/broadcast', { method: 'POST', body: JSON.stringify(body) });
      box.innerHTML = `<div class="muted">${dry ? 'Получателей' : 'Поставлено в очередь'}: ${r.recipients}</div>`;
    } catch (e) {
      box.innerHTML = `<div class="ws-err">${esc(e.message)}</div>`;
    }
  };

  async function errors() {
    const r = await api('/admin/errors?status=' + A.errStatus);
    window._admErrs = Object.fromEntries(r.errors.map((e) => [e.id, e]));
    const K = { server: 'Сервер', client: 'Браузер', job: 'Фоновая задача' };
    return `<div class="card"><div class="row form ws-filter" style="gap:8px;justify-content:flex-start;flex-wrap:wrap">
      <select onchange="admErrSt(this.value)">${[
        ['open', 'Не разобраны'],
        ['resolved', 'Решены'],
        ['all', 'Все']
      ]
        .map(([k, l]) => `<option value="${k}" ${A.errStatus === k ? 'selected' : ''}>${l}</option>`)
        .join('')}</select>
      <button class="btn secondary" onclick="admErrTest()">Проверить мониторинг</button>
      <span class="muted">Одинаковые ошибки объединяются. Новая ошибка приходит админам в Telegram (не чаще раза в час).</span></div>
      ${
        r.errors.length
          ? `<div class="list">${r.errors
              .map(
                (
                  e
                ) => `<div class="item" style="cursor:pointer" onclick="admErr('${e.id}')"><div><b>${esc(e.title)}</b>
              <div class="muted">${K[e.kind] || e.kind} · ${esc(e.path || '—')} · ${esc(e.tenant || '')} · ${fmt(e.last_seen)}</div></div>
              <span class="pill"><i class="dot ${e.resolved ? '' : 'red'}"></i>${e.count}×</span></div>`
              )
              .join('')}</div>`
          : '<div class="empty">Ошибок нет 🎉</div>'
      }</div>`;
  }
  window.admErrSt = (s) => {
    A.errStatus = s;
    page();
  };
  window.admErrTest = async () => {
    await api('/admin/errors/test', { method: 'POST', body: '{}' });
    page();
  };
  window.admErr = (id) => {
    const e = window._admErrs[id];
    modalBox(`<h3>${esc(e.title)}</h3><p class="muted">Код ${e.id} · ${e.count} раз · впервые ${fmt(e.first_seen)} · последний раз ${fmt(e.last_seen)}</p>
      <pre style="white-space:pre-wrap;max-height:50vh;overflow:auto;font-size:12px">${esc(e.detail || '')}</pre>
      <div class="row"><button class="btn secondary" onclick="admErrAct('${id}','delete')">Удалить</button>
      <button class="btn" onclick="admErrAct('${id}','${e.resolved ? 'reopen' : 'resolve'}')">${e.resolved ? 'Открыть снова' : 'Отметить решённой'}</button></div>`);
  };
  window.admErrAct = async (id, a) => {
    await api(`/admin/errors/${id}/${a}`, { method: 'POST', body: '{}' });
    closeModal();
    page();
  };

  async function jobsTab() {
    const r = await api('/admin/jobs?status=' + A.jobStatus);
    const S = {
      queued: 'В очереди',
      running: 'Выполняется',
      done: 'Готово',
      dead: 'Упала',
      cancelled: 'Отменена'
    };
    const c = r.status.counts || {};
    return `<div class="card"><div class="row form ws-filter" style="gap:8px;justify-content:flex-start;flex-wrap:wrap"><select onchange="admJobSt(this.value)"><option value="">Все</option>${Object.entries(
      S
    )
      .map(
        ([k, l]) => `<option value="${k}" ${A.jobStatus === k ? 'selected' : ''}>${l} (${c[k] || 0})</option>`
      )
      .join('')}</select>
      <span class="muted">Режим: ${esc(r.status.mode)} · пульс ${r.status.heartbeat_age == null ? 'нет' : r.status.heartbeat_age + ' с назад'}. Неудачные задачи повторяются с нарастающей паузой (30 с → 1 ч).</span></div>
      ${
        r.jobs.length
          ? `<div style="overflow:auto"><table class="table"><thead><tr><th>Задача</th><th>Пользователь</th><th>Статус</th><th>Попытки</th><th>Обновлена</th><th></th></tr></thead><tbody>${r.jobs
              .map(
                (
                  j
                ) => `<tr><td>${esc(j.kind)}${j.last_error ? `<div class="muted">${esc(j.last_error)}</div>` : ''}</td><td>${esc(j.tenant)}</td>
              <td>${pill(S[j.status] || j.status, j.status === 'dead')}</td><td>${j.attempts}/${j.max_attempts}</td><td>${fmt(j.updated_at)}</td>
              <td>${j.status === 'dead' || j.status === 'cancelled' ? `<button class="btn secondary" onclick="admJob('${j.id}','retry')">Повторить</button>` : j.status === 'queued' ? `<button class="btn secondary" onclick="admJob('${j.id}','cancel')">Отменить</button>` : ''}</td></tr>`
              )
              .join('')}</tbody></table></div>`
          : '<div class="empty">Задач нет</div>'
      }</div>`;
  }
  window.admJobSt = (s) => {
    A.jobStatus = s;
    page();
  };
  window.admJob = async (id, a) => {
    try {
      await api(`/admin/jobs/${id}/${a}`, { method: 'POST', body: '{}' });
    } catch (e) {
      showAppError(e);
    }
    page();
  };

  async function system() {
    const st = await api('/admin/settings');
    const r = st.registration,
      m = st.maintenance;
    return `<div class="card"><h3>Регистрация</h3><div class="form">
      <label>Кто может пользоваться<select id="admReg"><option value="open" ${r.mode === 'open' ? 'selected' : ''}>Все пользователи Telegram</option><option value="closed" ${r.mode === 'closed' ? 'selected' : ''}>Только уже зарегистрированные</option></select></label>
      <label>Тариф для новых<select id="admRegP">${Object.entries(st.plans)
        .map(
          ([k, p]) => `<option value="${k}" ${r.default_plan === k ? 'selected' : ''}>${esc(p.name)}</option>`
        )
        .join('')}</select></label>
      <div class="row"><button class="btn" onclick="admRegSave()">Сохранить</button></div></div></div>
      <div class="card"><h3>Технические работы</h3><p class="muted">Пока включено, пользователи видят сообщение вместо приложения. Админы работают как обычно.</p><div class="form">
      <label class="ws-check"><input id="admM" type="checkbox" ${m.on ? 'checked' : ''}> Включить режим техработ</label>
      <label>Сообщение<input id="admMm" value="${esc(m.message)}"></label>
      <div class="row"><button class="btn" onclick="admMSave()">Сохранить</button></div></div></div>`;
  }
  window.admRegSave = async () => {
    await api('/admin/settings/registration', {
      method: 'PUT',
      body: JSON.stringify({ mode: val('admReg'), default_plan: val('admRegP') })
    });
    page();
  };
  window.admMSave = async () => {
    await api('/admin/settings/maintenance', {
      method: 'PUT',
      body: JSON.stringify({ on: val('admM'), message: val('admMm') })
    });
    page();
  };

  async function audit() {
    const r = await api('/admin/audit');
    return `<div class="card"><h3>Действия администраторов</h3>${
      r.items.length
        ? `<div class="list">${r.items
            .map(
              (i) =>
                `<div class="item"><div><b>${esc(i.action)}</b> ${esc(i.target || '')}<div class="muted">${esc((i.payload || '').slice(0, 160))}</div></div><span class="muted">${fmt(i.created_at)} · ${esc(i.admin_id)}</span></div>`
            )
            .join('')}</div>`
        : '<div class="empty">Пока пусто</div>'
    }</div>`;
  }

  async function page() {
    const fn = { overview, users, plans, news, errors, jobs: jobsTab, system, audit }[A.tab] || overview;
    const html = await fn();
    shell('Админка', 'Управление сервисом: пользователи, тарифы, ошибки, фоновые задачи', tabs() + html);
  }
  routes.admin = page;
  boot();
})();
