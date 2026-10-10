/* FaxClip workspace: onboarding, personas, proxies, extended accounts/devices (QUICON-style, no sales). */
(function () {
  const SLOT = { morning: 'Утро', afternoon: 'День', evening: 'Вечер', night: 'Ночь' };
  const MODE = { young: 'Молодой', warming: 'Прогрев', hot: 'Горячий', dormant: 'Спящий' };
  const ASTATUS = {
    draft: 'Черновик',
    pending: 'В ожидании',
    registered: 'Зарегистрирован',
    login: 'В системе',
    logout: 'Вышел',
    blocked: 'Заблокирован',
    archived: 'Архив'
  };
  const PSTATUS = {
    active: 'Активна',
    blocked: 'Заблокирована',
    suspended: 'Приостановлена',
    archived: 'Архив'
  };
  const XSTATUS = { draft: 'Не проверен', active: 'Работает', error: 'Не работает', archived: 'Архив' };
  const LOGIN = { username: 'Имя пользователя', email: 'Email', phone_number: 'Номер телефона' };
  const PERS = {
    creative: 'Креативный',
    empathetic: 'Эмпатичный',
    analytical: 'Аналитик',
    energetic: 'Энергичный',
    calm: 'Спокойный',
    humorous: 'С юмором',
    expert: 'Эксперт'
  };
  const opts = (m, v, empty) =>
    (empty !== undefined ? `<option value="">${empty}</option>` : '') +
    Object.entries(m)
      .map(([k, l]) => `<option value="${k}" ${k === v ? 'selected' : ''}>${l}</option>`)
      .join('');
  const val = (id) => {
    const e = document.getElementById(id);
    return e ? (e.type === 'checkbox' ? e.checked : e.value.trim()) : '';
  };
  const fail = (e) => {
    const b = document.getElementById('wsErr');
    if (b) {
      b.textContent = e.message || String(e);
      b.style.display = 'block';
    } else alert(e.message || e);
  };
  const errBox = '<p id="wsErr" class="ws-err" style="display:none"></p>';
  let after = null; // what to re-render after a modal save
  const rerender = async () => {
    closeModal();
    if (after) await after();
  };

  /* ---------- Proxies ---------- */
  async function proxies() {
    after = proxies;
    const [p, d] = await Promise.all([api('/proxies'), api('/devices')]);
    shell(
      'Прокси',
      'Отдельный IP для каждого телефона',
      `<div class="card"><div class="row"><b>Все прокси</b><button class="btn" onclick="wsProxyForm()">+ Добавить прокси</button></div>
<p class="muted">Одно устройство — один прокси. Прокси ставится на телефон (например, через приложение VPN/прокси). Кнопка «Проверить» показывает, жив ли прокси и какой у него внешний IP.</p>
<table class="table"><thead><tr><th>Прокси</th><th>Устройство</th><th>Статус</th><th>Проверка</th><th></th></tr></thead><tbody>${p.map((x) => `<tr><td><b>${esc(x.proxy_ip)}:${x.proxy_port}</b><br><span class="muted">${x.proxy_username ? esc(x.proxy_username) + ' · пароль сохранён' : 'без логина'}</span></td><td>${esc(x.device_name || 'Не назначено')}</td><td><span class="pill"><i class="dot ${x.status === 'active' ? '' : 'red'}"></i>${XSTATUS[x.status] || x.status}</span></td><td class="muted">${x.last_check_at ? `${esc(x.last_check_message || '')}${x.last_check_egress_ip ? '<br>IP: ' + esc(x.last_check_egress_ip) : ''}${x.last_check_latency_ms ? ' · ' + x.last_check_latency_ms + ' мс' : ''}` : 'Не проверялся'}</td><td><button class="btn secondary" onclick="wsProxyCheck('${x.id}',this)">Проверить</button> <button class="btn secondary" onclick="wsProxyForm('${x.id}')">Изменить</button> <button class="btn secondary" onclick="wsProxyDelete('${x.id}')">Удалить</button></td></tr>`).join('') || `<tr><td colspan="5" class="empty">Прокси пока нет</td></tr>`}</tbody></table></div>`
    );
  }
  window.wsProxyForm = async function (id) {
    const [list, devs] = await Promise.all([api('/proxies'), api('/devices')]);
    const x = list.find((p) => p.id === id) || {};
    const busy = new Set(list.filter((p) => p.id !== id && p.device_id).map((p) => p.device_id));
    modalBox(`<h3>${id ? 'Изменить прокси' : 'Новый прокси'}</h3><div class="form"><div class="formgrid"><label>IP или домен<input id="xi" value="${esc(x.proxy_ip || '')}" placeholder="45.12.34.56"></label><label>Порт<input id="xp" inputmode="numeric" value="${esc(x.proxy_port || '')}" placeholder="8000"></label></div>
<div class="formgrid"><label>Логин<input id="xu" value="${esc(x.proxy_username || '')}" autocomplete="off"></label><label>Пароль<input id="xw" type="password" autocomplete="new-password" placeholder="${x.has_password ? 'сохранён — оставьте пустым' : ''}"></label></div>
<label>Устройство<select id="xd"><option value="">Не назначено</option>${devs
      .filter((v) => v.status !== 'REVOKED')
      .map(
        (v) =>
          `<option value="${v.id}" ${v.id === x.device_id ? 'selected' : ''} ${busy.has(v.id) ? 'disabled' : ''}>${esc(v.name)}${busy.has(v.id) ? ' — занято' : ''}</option>`
      )
      .join('')}</select></label>
<p class="muted">Берите прокси страны, куда публикуете. Не берите «датацентр» и прокси с оплатой за гигабайты.</p>${errBox}
<div class="row"><button class="close" onclick="closeModal()">Отмена</button><button class="btn" onclick="wsProxySave('${id || ''}')">Сохранить</button></div></div>`);
  };
  window.wsProxySave = async function (id) {
    try {
      const b = {
        proxy_ip: val('xi'),
        proxy_port: val('xp'),
        proxy_username: val('xu'),
        device_id: val('xd') || null
      };
      const w = val('xw');
      if (w || !id) b.proxy_password = w;
      await api(id ? '/proxies/' + id : '/proxies', {
        method: id ? 'PATCH' : 'POST',
        body: JSON.stringify(b)
      });
      await rerender();
    } catch (e) {
      fail(e);
    }
  };
  window.wsProxyCheck = async function (id, btn) {
    btn.disabled = true;
    btn.textContent = 'Проверяем…';
    try {
      const r = await api(`/proxies/${id}/check`, { method: 'POST', body: '{}' });
      modalBox(
        `<h3>${r.ok ? 'Прокси работает' : 'Прокси не работает'}</h3><p>${esc(r.message)}</p>${r.egress_ip ? `<p>Внешний IP: <b>${esc(r.egress_ip)}</b></p>` : ''}${r.latency_ms ? `<p class="muted">Ответ за ${r.latency_ms} мс</p>` : ''}<div class="row"><button class="btn" onclick="wsAfter()">Понятно</button></div>`
      );
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Проверить';
    }
  };
  window.wsProxyDelete = async function (id) {
    if (!confirm('Удалить прокси?')) return;
    await api('/proxies/' + id, { method: 'DELETE' });
    if (after) await after();
  };
  window.wsAfter = rerender;

  /* ---------- Personas ---------- */
  async function personas() {
    after = personas;
    const p = await api('/personas');
    shell(
      'Агенты',
      'Агент работает в аккаунтах за вас: выполняет задачи, изучает темы и ведёт статистику',
      `<div class="card"><div class="row"><b>Все агенты</b><button class="btn" onclick="wsPersonaForm()">+ Создать агента</button></div>
<p class="muted">Один агент на одно устройство. Скажите агенту, что изучать (темы, хэштеги, @аккаунты), и поставьте задачи (минуты просмотра, лайки, подписки) — он учтёт это в каждой сессии прогрева на Mac и покажет, что сделал.</p></div>
<div class="agent-grid">${
        p
          .map(
            (
              x
            ) => `<div class="card agent-card"><div class="row"><div><b>${esc(x.name)}</b><div class="muted">${esc([x.gender === 'male' ? 'Муж.' : x.gender === 'female' ? 'Жен.' : '', x.country, x.city].filter(Boolean).join(' · ') || 'Профиль не заполнен')}</div></div><span class="pill"><i class="dot ${x.status === 'active' ? '' : 'red'}"></i>${PSTATUS[x.status] || x.status}</span></div>
<div class="muted" style="margin-top:8px">📱 ${esc(x.device_name || 'без устройства')} · Аккаунтов: ${x.accounts} · ${x.preferred_times.map((t) => SLOT[t.time_slot]).join(', ') || 'слоты не заданы'}</div>
<div style="margin-top:8px">${x.interests.map((i) => `<span class="pill">${esc(i.tag)}</span>`).join(' ') || '<span class="muted">Интересы не заданы</span>'}</div>
<div class="row" style="margin-top:12px;justify-content:flex-start;gap:6px"><button class="btn" onclick="wsAgent('${x.id}')">Задачи и аналитика</button><button class="btn secondary" onclick="wsPersonaForm('${x.id}')">Изменить</button><button class="btn secondary" onclick="wsPersonaDelete('${x.id}')">Удалить</button></div></div>`
          )
          .join('') ||
        '<div class="card empty">Агентов пока нет. Создайте первого и привяжите к нему телефон и аккаунты.</div>'
      }</div>`
    );
  }
  /* ---------- Agent: tasks + analytics ---------- */
  let agentId = null;
  async function agentPage() {
    after = agentPage;
    const o = await api('/agents/' + agentId + '/overview?days=30');
    const t = o.totals;
    const n = (v) => Number(v || 0).toLocaleString('ru-RU');
    const box = (l, v, h = '') =>
      `<div class="card"><div class="muted">${l}</div><div class="dd-num">${v}</div>${h ? `<div class="muted">${h}</div>` : ''}</div>`;
    const maxM = Math.max(1, ...o.by_day.map((d) => d.minutes));
    const tasks =
      o.tasks
        .map((k) => {
          const isStudy = k.kind.startsWith('study');
          const goal = isStudy
            ? `${n(k.progress)} сессий с поиском`
            : `${n(k.progress)} из ${n(k.target)}${k.period === 'day' ? ' сегодня' : ''}`;
          const pc = !isStudy && k.target ? Math.min(100, Math.round((k.progress / k.target) * 100)) : null;
          return `<div class="item"><div class="row"><span><b>${esc(k.label)}</b>${k.value ? ' · ' + esc(k.value) : ''} ${k.status !== 'active' ? `<span class="pill">${k.status === 'paused' ? 'на паузе' : 'выполнена'}</span>` : ''}</span><span class="muted">${goal}</span></div>${pc != null ? `<div class="agent-prog"><i style="width:${pc}%"></i></div>` : ''}<div class="row" style="justify-content:flex-start;gap:6px;margin-top:6px"><button class="btn secondary" onclick="wsAgentTask('${k.id}','${k.status === 'active' ? 'paused' : 'active'}')">${k.status === 'active' ? 'Пауза' : 'Возобновить'}</button>${!isStudy && k.status !== 'done' ? `<button class="btn secondary" onclick="wsAgentTask('${k.id}','done')">Выполнена</button>` : ''}<button class="btn secondary" onclick="wsAgentTask('${k.id}','delete')">Удалить</button></div></div>`;
        })
        .join('') || '<div class="empty">Задач пока нет. Добавьте первую ниже.</div>';
    shell(
      'Агент · ' + o.name,
      'Задачи, что изучает и что уже сделал (за 30 дней)',
      `<div class="row" style="justify-content:flex-start;gap:6px;margin-bottom:12px"><button class="btn secondary" onclick="goPage('personas')">← Все агенты</button><button class="btn secondary" onclick="wsPersonaForm('${agentId}')">Профиль агента</button></div>
<div class="dd-grid">${box('Сессий выполнено', n(t.done), `пропущено ${n(t.missed)} · ошибок ${n(t.failed)} · в плане ${n(t.scheduled)}`)}${box('Минут в приложении', n(t.minutes))}${box('Роликов просмотрено', n(t.videos))}${box('Лайков', n(t.likes))}${box('Подписок', n(t.follows))}${box('Поисков', n(t.searches))}</div>
<div class="grid2"><div class="card"><b>Задачи агента</b><div class="list">${tasks}</div>
<div class="form" style="margin-top:12px"><div class="formgrid"><label>Что сделать<select id="agk">${Object.entries(
        o.kinds
      )
        .map(([k, l]) => `<option value="${k}">${l}</option>`)
        .join(
          ''
        )}</select></label><label>Тема / @аккаунт / цель<input id="agv" placeholder="например: ремонт авто, @mrbeast или 30"></label></div><label class="ws-check"><input type="checkbox" id="agd"> Цель на каждый день (иначе — всего)</label><p id="wsErr" class="ws-err" style="display:none"></p><button class="btn" onclick="wsAgentAdd()">Добавить задачу</button></div></div>
<div class="card"><b>Что агент изучает</b><p class="muted">Эти слова агент ищет в TikTok во время прогрева (вместе с ключевыми словами аккаунта).</p><div>${o.keywords.map((k) => `<span class="pill">${esc(k)}</span>`).join(' ') || '<span class="muted">Пока ничего — добавьте задачу «Изучать тему» или интересы в профиле.</span>'}</div>
<b style="display:block;margin-top:14px">Активность по дням</b><div class="agent-bars">${o.by_day.map((d) => `<div title="${esc(d.day)}: ${d.minutes} мин, ${d.videos} роликов, ${d.likes} лайков"><i style="height:${Math.max(4, Math.round((d.minutes / maxM) * 100))}%"></i><small>${esc(d.day.slice(8, 10))}</small></div>`).join('') || '<div class="muted">Сессий ещё не было</div>'}</div>
<b style="display:block;margin-top:14px">Аккаунты агента</b><div class="list">${o.accounts.map((a) => `<div class="item row"><span>${esc(a.platform)} · ${esc(a.username || '—')}</span><span class="muted">${n(a.sessions)} сессий · ${n(a.videos)} роликов · ${n(a.likes)} лайков · ${n(a.published)} публикаций</span></div>`).join('') || '<div class="empty">Аккаунты не привязаны. Привяжите в «Аккаунты» → профиль аккаунта → Агент.</div>'}</div></div></div>`
    );
  }
  window.wsAgent = async (id) => {
    agentId = id;
    window.FX_CURRENT_PAGE = 'personas';
    try {
      await agentPage();
    } catch (e) {
      showAppError(e);
    }
  };
  window.wsAgentAdd = async () => {
    const kind = val('agk');
    const v = val('agv');
    const body = { kind, period: val('agd') ? 'day' : 'total' };
    if (kind.startsWith('study')) body.value = v;
    else body.target = parseInt(v, 10);
    try {
      await api('/agents/' + agentId + '/tasks', { method: 'POST', body: JSON.stringify(body) });
      await agentPage();
    } catch (e) {
      fail(e);
    }
  };
  window.wsAgentTask = async (tid, st) => {
    const url = '/agents/' + agentId + '/tasks/' + tid;
    if (st === 'delete') {
      if (!confirm('Удалить задачу?')) return;
      await api(url, { method: 'DELETE' });
    } else await api(url, { method: 'PATCH', body: JSON.stringify({ status: st }) });
    await agentPage();
  };
  window.wsPersonaForm = async function (id) {
    const [list, devs] = await Promise.all([api('/personas'), api('/devices')]);
    const x = list.find((p) => p.id === id) || { interests: [], preferred_times: [], status: 'active' };
    const busy = new Set(list.filter((p) => p.id !== id && p.status !== 'archived').map((p) => p.device_id));
    const ints = [...x.interests];
    while (ints.length < 5) ints.push({ tag: '', weight: 3 });
    const tm = Object.fromEntries(x.preferred_times.map((t) => [t.time_slot, t.weight]));
    modalBox(`<h3>${id ? 'Изменить агента' : 'Новый агент'}</h3><div class="form">
<label>Полное имя<input id="pn" value="${esc(x.name || '')}" placeholder="101 Ден — начните с номера телефона"></label>
<div class="formgrid"><label>Email<input id="pe" value="${esc(x.email || '')}"></label><label>Телефон<input id="pph" value="${esc(x.phone || '')}"></label></div>
<div class="formgrid"><label>Пол<select id="pg">${opts({ male: 'Мужской', female: 'Женский' }, x.gender, 'Не указан')}</select></label><label>День рождения<input id="pb" type="date" value="${esc(x.date_of_birth || '')}"></label></div>
<div class="formgrid"><label>Страна<input id="pc" value="${esc(x.country || '')}" placeholder="Россия"></label><label>Язык<input id="pl" value="${esc(x.language || '')}" placeholder="Русский"></label></div>
<div class="formgrid"><label>Город<input id="pci" value="${esc(x.city || '')}"></label><label>Устройство<select id="pd"><option value="">Выберите устройство</option>${devs
      .filter((v) => v.status !== 'REVOKED')
      .map(
        (v) =>
          `<option value="${v.id}" ${v.id === x.device_id ? 'selected' : ''} ${busy.has(v.id) ? 'disabled' : ''}>${esc(v.name)}${busy.has(v.id) ? ' — занято' : ''}</option>`
      )
      .join('')}</select></label></div>
<div class="formgrid"><label>Тип личности<select id="pt">${opts(PERS, x.personality, 'Не выбран')}</select></label><label>Статус<select id="ps">${opts(PSTATUS, x.status)}</select></label></div>
<b>Интересы</b><p class="muted">Не больше пяти, все в вашей нише. Вес 1–5 — приоритет.</p>${ints
      .slice(0, 5)
      .map(
        (i, k) =>
          `<div class="formgrid ws-pair"><input id="pi${k}" value="${esc(i.tag)}" placeholder="Интерес ${k + 1}"><input id="piw${k}" type="number" min="1" max="5" value="${i.weight}"></div>`
      )
      .join('')}
<b>Слоты активности</b><p class="muted">Когда агент выходит работать. Вес 5 включается чаще, чем 3.</p>${Object.entries(
      SLOT
    )
      .map(
        ([s, l]) =>
          `<div class="formgrid ws-pair"><label class="ws-check"><input type="checkbox" id="ts_${s}" ${tm[s] ? 'checked' : ''}> ${l}</label><input id="tw_${s}" type="number" min="1" max="5" value="${tm[s] || 3}"></div>`
      )
      .join('')}
<label>Контекст для ИИ (кто этот агент и как пишет)<textarea id="pno" placeholder="Например: Ден, 27 лет, механик из Казани, пишет коротко и с юмором">${esc(x.notes || '')}</textarea></label>
<label>Запрещённые темы<input id="pdk" value="${esc(x.denied_keywords || '')}" placeholder="политика, религия, NSFW"></label>
<label class="ws-check"><input type="checkbox" id="ptn" ${x.target_by_niche ? 'checked' : ''}> Подбирать контент только в нише агента</label>${errBox}
<div class="row"><button class="close" onclick="closeModal()">Отмена</button><button class="btn" onclick="wsPersonaSave('${id || ''}')">${id ? 'Сохранить' : 'Создать агента'}</button></div></div>`);
  };
  window.wsPersonaSave = async function (id) {
    try {
      const b = {
        name: val('pn'),
        email: val('pe'),
        phone: val('pph'),
        gender: val('pg'),
        date_of_birth: val('pb') || null,
        country: val('pc'),
        language: val('pl'),
        city: val('pci'),
        device_id: val('pd'),
        personality: val('pt'),
        status: val('ps'),
        notes: val('pno'),
        denied_keywords: val('pdk'),
        target_by_niche: val('ptn'),
        interests: [0, 1, 2, 3, 4]
          .map((k) => ({ tag: val('pi' + k), weight: +val('piw' + k) || 3 }))
          .filter((i) => i.tag),
        preferred_times: Object.keys(SLOT)
          .filter((s) => val('ts_' + s))
          .map((s) => ({ time_slot: s, weight: +val('tw_' + s) || 3 }))
      };
      if (!b.device_id) throw new Error('Выберите устройство');
      await api(id ? '/personas/' + id : '/personas', {
        method: id ? 'PATCH' : 'POST',
        body: JSON.stringify(b)
      });
      await rerender();
    } catch (e) {
      fail(e);
    }
  };
  window.wsPersonaDelete = async function (id) {
    if (!confirm('Удалить агента? Аккаунты останутся, но без агента. Задачи агента удалятся.')) return;
    await api('/personas/' + id, { method: 'DELETE' });
    if (after) await after();
  };

  /* ---------- Accounts (extended) ---------- */
  async function wsAccounts() {
    after = wsAccounts;
    const a = await api('/account-profiles');
    shell(
      'Аккаунты',
      'Аккаунты на телефонах, агенты и режимы прогрева',
      `<div class="card"><div class="row"><b>Все аккаунты</b><button class="btn" onclick="wsAccountForm()">+ Добавить аккаунт</button></div>
<table class="table"><thead><tr><th>Аккаунт</th><th>Агент</th><th>Устройство</th><th>Режим</th><th>Статус</th><th></th></tr></thead><tbody>${a.map((x) => `<tr><td><b>${esc(x.username || 'Без username')}</b><br><span class="muted">${esc(x.platform)}${x.channel_name ? ' · ' + esc(x.channel_name) : ''}</span></td><td>${esc(x.persona_name || '—')}</td><td>${esc(x.device_name || '—')}</td><td><span class="pill">${MODE[x.work_mode]}</span></td><td><span class="pill"><i class="dot ${['login', 'registered'].includes(x.status) ? '' : 'red'}"></i>${ASTATUS[x.status] || x.status}</span></td><td><button class="btn secondary" onclick="wsAccountForm('${x.id}')">Профиль</button> <button class="btn secondary" onclick="assignDevice('${x.id}')">Устройство</button></td></tr>`).join('') || `<tr><td colspan="6" class="empty">Аккаунтов пока нет</td></tr>`}</tbody></table></div>`
    );
  }
  window.wsAccountForm = async function (id) {
    const [list, pers] = await Promise.all([api('/account-profiles'), api('/personas')]);
    const x = list.find((a) => a.id === id) || {
      platform: 'TikTok',
      work_mode: 'young',
      status: 'login',
      login_method: 'username'
    };
    modalBox(`<h3>${id ? 'Профиль аккаунта' : 'Добавить аккаунт'}</h3><div class="form">
<div class="formgrid"><label>Сеть<select id="ac" ${id ? 'disabled' : ''}>${['TikTok', 'Instagram', 'YouTube', 'VK'].map((p) => `<option ${p === x.platform ? 'selected' : ''}>${p}</option>`).join('')}</select></label><label>Имя пользователя<input id="au2" value="${esc(x.username || '')}" ${id ? 'disabled' : ''} placeholder="@username — с учётом регистра"></label></div>
<div class="formgrid"><label>Агент<select id="apn"><option value="">Без агента</option>${pers.map((p) => `<option value="${p.id}" ${p.id === x.persona_id ? 'selected' : ''}>${esc(p.name)}${p.device_name ? ' · ' + esc(p.device_name) : ''}</option>`).join('')}</select></label><label>Метод входа<select id="alm">${opts(LOGIN, x.login_method)}</select></label></div>
<div class="formgrid"><label>Режим<select id="awm">${opts(MODE, x.work_mode)}</select></label><label>Статус<select id="ast">${opts(ASTATUS, x.status)}</select></label></div>
<label>Название канала<input id="ach" value="${esc(x.channel_name || '')}"></label>
<label>Заметки для ИИ (о чём этот аккаунт)<textarea id="ano" placeholder="Например: обзоры запчастей для японских авто, дружелюбный тон">${esc(x.notes || '')}</textarea></label>
<label>Ключевые слова для поиска (через запятую)<input id="akw" value="${esc(x.search_keywords || '')}" placeholder="ремонт авто, запчасти, тюнинг"></label>
<p class="muted">Пароль и коды 2FA FaxClip не хранит — аккаунт уже открыт в приложении на телефоне.</p>${errBox}
<div class="row"><button class="close" onclick="closeModal()">Отмена</button><button class="btn" onclick="wsAccountSave('${id || ''}')">Сохранить</button></div></div>`);
  };
  window.wsAccountSave = async function (id) {
    try {
      if (!val('akw')) throw new Error('Укажите ключевые слова для поиска через запятую');
      let aid = id;
      if (!aid) {
        if (!val('au2')) throw new Error('Укажите имя пользователя');
        const per = (await api('/personas')).find((p) => p.id === val('apn'));
        aid = (
          await api('/accounts', {
            method: 'POST',
            body: JSON.stringify({
              platform: val('ac'),
              username: val('au2'),
              device_id: per?.device_id || null
            })
          })
        ).id;
      }
      await api('/account-profiles/' + aid, {
        method: 'PUT',
        body: JSON.stringify({
          persona_id: val('apn') || null,
          login_method: val('alm'),
          work_mode: val('awm'),
          status: val('ast'),
          channel_name: val('ach'),
          notes: val('ano'),
          search_keywords: val('akw')
        })
      });
      await rerender();
    } catch (e) {
      fail(e);
    }
  };

  /* ---------- Devices (settings: name, locale, timezone, proxy) ---------- */
  const origDevices = window.devices;
  async function wsDevices() {
    after = wsDevices;
    await origDevices();
    const prof = await api('/device-profiles');
    document.querySelectorAll('#view button[onclick^="connectDevice("]').forEach((b) => {
      const id = (b.getAttribute('onclick').match(/'([^']+)'/) || [])[1];
      const p = prof.find((x) => x.id === id);
      if (!p) return;
      const info = document.createElement('div');
      info.className = 'muted';
      info.style.marginTop = '10px';
      info.innerHTML = `Агент: ${esc(p.persona_name || '—')} · Прокси: ${p.proxy_ip ? esc(p.proxy_ip + ':' + p.proxy_port) : '—'}<br>Локаль: ${esc(p.locale || '—')} · Часовой пояс: ${esc(p.timezone || '—')}`;
      const btn = document.createElement('button');
      btn.className = 'btn secondary';
      btn.style.marginTop = '12px';
      btn.style.marginLeft = '6px';
      btn.textContent = 'Настройки';
      btn.onclick = () => wsDeviceForm(id);
      const sb = document.createElement('button');
      sb.className = 'btn';
      sb.style.marginTop = '12px';
      sb.style.marginLeft = '6px';
      sb.textContent = 'Экран';
      sb.onclick = () => wsScreen(id, p.name);
      b.before(info);
      b.after(btn);
      btn.after(sb);
    });
  }
  window.wsDeviceForm = async function (id) {
    const [prof, px] = await Promise.all([api('/device-profiles'), api('/proxies')]);
    const x = prof.find((p) => p.id === id) || {};
    const tz = Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : ['Europe/Moscow'];
    modalBox(`<h3>Настройки устройства</h3><div class="form"><label>Название<input id="dvn" value="${esc(x.name || '')}" placeholder="101"></label>
<div class="formgrid"><label>Локаль (язык телефона)<select id="dvl">${opts({ 'ru-RU': 'Русский (ru-RU)', 'en-US': 'English (en-US)', 'uk-UA': 'Українська (uk-UA)', 'kk-KZ': 'Қазақ (kk-KZ)', 'de-DE': 'Deutsch (de-DE)', 'es-ES': 'Español (es-ES)' }, x.locale, 'Не указана')}</select></label>
<label>Часовой пояс<select id="dvt"><option value="">Не указан</option>${tz.map((z) => `<option ${z === x.timezone ? 'selected' : ''}>${z}</option>`).join('')}</select></label></div>
<label>Прокси<select id="dvp"><option value="">Не назначен</option>${px.map((p) => `<option value="${p.id}" ${p.id === x.proxy_id ? 'selected' : ''} ${p.device_id && p.device_id !== id ? 'disabled' : ''}>${esc(p.proxy_ip + ':' + p.proxy_port)}${p.device_id && p.device_id !== id ? ' — занят' : ''}</option>`).join('')}</select></label>
<p class="muted">Название лучше ставить цифрами: 101, 102, 103. По часовому поясу телефон будет публиковать.</p>${errBox}
<div class="row"><button class="btn danger" onclick="wsDeviceDelete('${id}')">Удалить устройство</button><span style="flex:1"></span><button class="close" onclick="closeModal()">Отмена</button><button class="btn" onclick="wsDeviceSave('${id}')">Сохранить</button></div></div>`);
  };
  window.wsDeviceDelete = async function (id, force = false) {
    if (
      !force &&
      !confirm(
        'Удалить устройство? Аккаунты, агенты и прокси останутся — они просто отвяжутся от этого телефона. Агент на телефоне перестанет работать (токен отзывается).'
      )
    )
      return;
    try {
      await api('/devices/' + id + (force ? '?force=1' : ''), { method: 'DELETE' });
      closeModal();
      await goPage('devices');
    } catch (e) {
      if (!force && e.status === 409) {
        if (confirm(e.message + '\n\nУдалить сейчас? Незавершённые публикации попадут в «Требует проверки».'))
          return wsDeviceDelete(id, true);
        return;
      }
      fail(e);
    }
  };
  window.wsDeviceSave = async function (id) {
    try {
      await api('/device-profiles/' + id, {
        method: 'PUT',
        body: JSON.stringify({
          name: val('dvn'),
          locale: val('dvl'),
          timezone: val('dvt'),
          proxy_id: val('dvp') || null
        })
      });
      await rerender();
    } catch (e) {
      fail(e);
    }
  };

  /* ---------- Remote screen (via Mac USB) ---------- */
  let scr = null;
  window.wsScreen = async function (id, name) {
    wsScreenStop();
    scr = { id, seq: -1, timer: null, ping: null, drag: null };
    modalBox(`<div class="row"><h3>Экран · ${esc(name || '')}</h3><button class="close" onclick="wsScreenStop();closeModal()">Закрыть</button></div>
<p class="muted" id="scrInfo">Подключаемся к телефону через Mac…</p>
<div id="scrSetup" class="card scr-setup" hidden><b>Mac не на связи — установите «Экран FaxClip»</b><p class="muted">Экран телефона передаёт Mac, к которому телефон подключён по USB. Один раз откройте «Терминал» на этом Mac, вставьте команду и нажмите Enter. Дальше агент запускается сам вместе с Mac.</p><textarea id="scrCmd" readonly rows="4" style="width:100%;font-family:monospace;font-size:12px" onclick="this.select()">Загрузка команды…</textarea><div class="row"><button class="btn secondary" onclick="navigator.clipboard.writeText(document.getElementById('scrCmd').value).then(()=>this.textContent='Скопировано')">Скопировать команду</button></div><p class="muted">Нужно: Mac включён, мост FaxClip установлен (телефон уже подключён по коду), телефон подключён кабелем и разрешена отладка по USB.</p></div>
<div class="scr-wrap"><img id="scrImg" alt="Экран телефона" draggable="false"><div id="scrEmpty" class="empty">Кадра пока нет. Если долго пусто — на Mac не установлен «Экран FaxClip».</div></div>
<div class="scr-keys"><button class="btn secondary" onclick="wsScr({action:'key',key:'back'})">◀ Назад</button><button class="btn secondary" onclick="wsScr({action:'key',key:'home'})">● Домой</button><button class="btn secondary" onclick="wsScr({action:'key',key:'recents'})">▢ Недавние</button><button class="btn secondary" onclick="wsScr({action:'wake'})">☀ Разбудить</button><button class="btn secondary" onclick="wsScr({action:'refresh'})">⟳ Обновить</button></div>
<div class="form"><div class="formgrid ws-pair2"><input id="scrText" placeholder="Текст (латиница) для ввода в активное поле"><button class="btn secondary" onclick="wsScrText()">Ввести</button></div></div>
<details class="scr-els"><summary onclick="wsScr({action:'elements'})">Элементы на экране (нажать по номеру)</summary><div id="scrEls" class="muted">Загрузка…</div></details>
<p id="scrLog" class="muted"></p><p class="muted">Нажмите на картинку — телефон нажмёт в этом месте. Проведите — будет свайп. Пока телефон публикует видео, нажимать нельзя: смотреть можно.</p>`);
    const img = document.getElementById('scrImg');
    const pos = (e) => {
      const r = img.getBoundingClientRect();
      return {
        x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
        y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
        t: Date.now()
      };
    };
    img.onpointerdown = (e) => {
      e.preventDefault();
      scr.drag = pos(e);
    };
    img.onpointerup = (e) => {
      if (!scr?.drag) return;
      const a = scr.drag,
        b = pos(e);
      scr.drag = null;
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (d < 0.02) wsScr({ action: 'tap', x: a.x, y: a.y });
      else
        wsScr({
          action: 'swipe',
          x1: a.x,
          y1: a.y,
          x2: b.x,
          y2: b.y,
          ms: Math.max(120, Math.min(1500, b.t - a.t))
        });
    };
    const open = () => api(`/devices/${id}/screen/open`, { method: 'POST', body: '{}' }).catch(() => {});
    await open();
    scr.ping = setInterval(open, 10000);
    const tick = async () => {
      if (!scr || !document.getElementById('scrImg')) return wsScreenStop();
      try {
        const s = await api(`/devices/${id}/screen`);
        const info = document.getElementById('scrInfo');
        const setup = document.getElementById('scrSetup');
        if (setup) {
          if (!s.agent_online) scr.offline = (scr.offline || 0) + 1;
          else scr.offline = 0;
          setup.hidden = !(scr.offline > 4 && s.frame_seq === 0);
          if (!setup.hidden && !scr.cmd) {
            scr.cmd = 1;
            api('/screen-install-command')
              .then((r) => (document.getElementById('scrCmd').value = r.command))
              .catch(() => (document.getElementById('scrCmd').value = 'Команда пока недоступна'));
          }
        }
        info.textContent = !s.agent_online
          ? 'Mac не на связи: проверьте, что Mac включён, а «Экран FaxClip» установлен.'
          : s.busy
            ? 'Телефон публикует видео — только просмотр.'
            : `На связи${s.frame_age != null ? ' · кадр ' + Math.round(s.frame_age) + ' с назад' : ''}`;
        if (s.frame_seq !== scr.seq && s.frame_seq > 0) {
          scr.seq = s.frame_seq;
          try {
            const r = await fetch(`/api/devices/${id}/screen.jpg?k=${s.frame_seq}`, {
              headers: authHeaders({})
            });
            if (r.ok) {
              const u = URL.createObjectURL(await r.blob());
              const old = img.src;
              img.src = u;
              if (old.startsWith('blob:')) URL.revokeObjectURL(old);
            }
          } catch (e) {}
        }
        document.getElementById('scrEmpty').style.display = s.frame_seq > 0 ? 'none' : 'block';
        const last = s.results[s.results.length - 1];
        if (last) document.getElementById('scrLog').textContent = (last.ok ? '✓ ' : '✗ ') + last.message;
        const els = document.getElementById('scrEls');
        if (s.elements_age != null)
          els.innerHTML = s.elements.length
            ? s.elements
                .map(
                  (e) =>
                    `<div class="item scr-el" onclick="wsScr({action:'tap_index',index:${e.index}})"><b>${e.index}</b> ${esc(e.text || e.desc || e.cls)}${e.clickable ? ' <span class="pill">кнопка</span>' : ''}</div>`
                )
                .join('')
            : 'Элементов не найдено';
      } catch (e) {}
      scr && (scr.timer = setTimeout(tick, 1000));
    };
    tick();
  };
  window.wsScreenStop = function () {
    if (!scr) return;
    clearTimeout(scr.timer);
    clearInterval(scr.ping);
    api(`/devices/${scr.id}/screen/close`, { method: 'POST', body: '{}' }).catch(() => {});
    scr = null;
  };
  window.wsScr = async function (cmd) {
    if (!scr) return;
    const log = document.getElementById('scrLog');
    try {
      await api(`/devices/${scr.id}/screen/command`, { method: 'POST', body: JSON.stringify(cmd) });
      if (log) log.textContent = 'Отправлено…';
    } catch (e) {
      if (log) log.textContent = '✗ ' + e.message;
    }
  };
  window.wsScrText = () => {
    const t = val('scrText');
    if (t) {
      wsScr({ action: 'text', text: t });
      document.getElementById('scrText').value = '';
    }
  };
  const _close = window.closeModal;
  window.closeModal = function () {
    wsScreenStop();
    _close();
  };

  /* ---------- Notifications ---------- */
  const LV = { info: 'Инфо', success: 'Успех', warning: 'Внимание', error: 'Ошибка' };
  const LVI = { info: 'ℹ️', success: '✅', warning: '⚠️', error: '⛔' };
  const ago = (t) => {
    const s = (Date.now() - new Date(t)) / 1000;
    return s < 60
      ? 'только что'
      : s < 3600
        ? Math.floor(s / 60) + ' мин назад'
        : s < 86400
          ? Math.floor(s / 3600) + ' ч назад'
          : new Date(t).toLocaleString('ru-RU', {
              day: '2-digit',
              month: '2-digit',
              hour: '2-digit',
              minute: '2-digit'
            });
  };
  let alf = { level: '', unread: false, offset: 0 };
  async function alerts() {
    after = alerts;
    const q = new URLSearchParams({
      limit: 30,
      offset: alf.offset,
      ...(alf.level ? { level: alf.level } : {}),
      ...(alf.unread ? { unread: 1 } : {})
    });
    const a = await api('/alerts?' + q);
    setBadge(a.unread);
    shell(
      'Уведомления',
      'События системы и публикаций',
      `<div class="card"><div class="row"><b>Уведомления в Telegram</b><label class="ws-check"><input type="checkbox" id="ntg" ${a.settings.telegram ? 'checked' : ''} onchange="wsNotifySave()"> Присылать в чат с ботом</label></div>
<p class="muted">${a.telegram_available ? 'Бот пришлёт сообщения выбранных типов.' : 'Бот Telegram на сервере не настроен.'}</p><div class="row" style="justify-content:flex-start;gap:14px;flex-wrap:wrap">${Object.entries(
        LV
      )
        .map(
          ([k, l]) =>
            `<label class="ws-check"><input type="checkbox" class="nlv" value="${k}" ${a.settings.levels.includes(k) ? 'checked' : ''} onchange="wsNotifySave()"> ${LVI[k]} ${l}</label>`
        )
        .join('')}<button class="btn secondary" onclick="wsNotifyTest()">Проверить</button></div></div>
<div class="card" style="margin-top:14px"><div class="row"><div class="row" style="gap:8px;justify-content:flex-start"><select id="nflt" onchange="alf.level=this.value;alf.offset=0;alerts()" class="ws-select"><option value="">Все уровни</option>${Object.entries(
        LV
      )
        .map(([k, l]) => `<option value="${k}" ${alf.level === k ? 'selected' : ''}>${l}</option>`)
        .join(
          ''
        )}</select><label class="ws-check"><input type="checkbox" ${alf.unread ? 'checked' : ''} onchange="alf.unread=this.checked;alf.offset=0;alerts()"> Только непрочитанные</label></div>
<div><button class="btn secondary" onclick="wsAlertsAll()">Прочитать все</button> <button class="btn secondary" onclick="wsAlertsClear()">Очистить</button></div></div>
<div class="list">${a.items.map((x) => `<div class="item al ${x.is_read ? '' : 'unread'}"><div class="row"><b>${LVI[x.level]} ${esc(x.title)}</b><span class="muted">${ago(x.created_at)}</span></div>${x.message ? `<div class="muted">${esc(x.message)}</div>` : ''}<div class="row" style="justify-content:flex-start;gap:8px;margin-top:6px">${x.page ? `<button class="btn secondary" onclick="goPage('${x.page}')">Открыть</button>` : ''}${x.link ? `<a class="btn secondary" href="${esc(x.link)}" target="_blank" rel="noopener">Ссылка</a>` : ''}${x.is_read ? '' : `<button class="btn secondary" onclick="wsAlertRead('${x.id}')">Прочитано</button>`}</div></div>`).join('') || '<div class="empty">Уведомлений нет</div>'}</div>
<div class="row" style="margin-top:12px"><button class="btn secondary" ${alf.offset ? '' : 'disabled'} onclick="alf.offset=Math.max(0,alf.offset-30);alerts()">Назад</button><span class="muted">${a.items.length ? `${alf.offset + 1}–${alf.offset + a.items.length}` : '0'} из ${a.total}</span><button class="btn secondary" ${alf.offset + 30 < a.total ? '' : 'disabled'} onclick="alf.offset+=30;alerts()">Далее</button></div></div>`
    );
  }
  window.alf = alf;
  window.alerts = alerts;
  window.wsNotifySave = async () => {
    await api('/alerts/settings', {
      method: 'PUT',
      body: JSON.stringify({
        telegram: val('ntg'),
        levels: [...document.querySelectorAll('.nlv:checked')].map((e) => e.value)
      })
    });
  };
  window.wsNotifyTest = async () => {
    try {
      await api('/alerts/test', { method: 'POST', body: '{}' });
      alert('Отправлено — проверьте чат с ботом');
    } catch (e) {
      alert(e.message);
    }
  };
  window.wsAlertRead = async (id) => {
    await api(`/alerts/${id}/read`, { method: 'POST', body: '{}' });
    await alerts();
  };
  window.wsAlertsAll = async () => {
    await api('/alerts/read-all', { method: 'POST', body: '{}' });
    await alerts();
  };
  window.wsAlertsClear = async () => {
    if (!confirm('Удалить все уведомления?')) return;
    await api('/alerts', { method: 'DELETE' });
    await alerts();
  };
  function setBadge(n) {
    const b = document.querySelector('nav [data-page="alerts"] span');
    if (b) b.innerHTML = `Уведомления${n ? ` <em class="nbadge">${n > 99 ? '99+' : n}</em>` : ''}`;
  }

  /* ---------- Dashboard live activity ---------- */
  const origDash = routes.dashboard;
  let feedTimer = null;
  async function wsDashboard() {
    await origDash();
    const v = document.getElementById('view');
    const card = document.createElement('div');
    card.className = 'card';
    card.style.marginTop = '14px';
    card.innerHTML =
      '<div class="row"><b>Лента событий</b><span class="pill"><i class="dot"></i>в реальном времени</span></div><div id="feed" class="list"><div class="muted">Загрузка…</div></div>';
    v.append(card);
    const load = async () => {
      const f = document.getElementById('feed');
      if (!f) {
        clearInterval(feedTimer);
        return;
      }
      try {
        const items = await api('/activity?limit=15');
        f.innerHTML =
          items
            .map(
              (x) =>
                `<div class="item feed-${x.level}" ${x.page ? `onclick="goPage('${x.page}')" style="cursor:pointer"` : ''}><div class="row"><span>${LVI[x.level]} ${esc(x.title)}</span><span class="muted">${ago(x.at)}</span></div>${x.message ? `<div class="muted">${esc(x.message)}</div>` : ''}</div>`
            )
            .join('') || '<div class="empty">Событий пока нет</div>';
      } catch (e) {}
    };
    clearInterval(feedTimer);
    await load();
    feedTimer = setInterval(load, 5000);
  }

  /* ---------- Onboarding ---------- */
  const STEPS = [
    { k: 'proxies', t: 'Прокси', opt: true },
    { k: 'devices', t: 'Устройства' },
    { k: 'personas', t: 'Персоны' },
    { k: 'accounts', t: 'Аккаунты' },
    { k: 'done', t: 'Готово' }
  ];
  let step = null;
  async function onboarding() {
    after = onboarding;
    const s = await api('/onboarding');
    const c = s.counts;
    const ok = (k) => (k === 'proxies' ? c.proxies > 0 || s.proxies_skipped : c[k] > 0);
    if (step === null) {
      step = STEPS.findIndex((x) => x.k !== 'done' && !ok(x.k));
      if (step < 0) step = STEPS.length - 1;
    }
    const cur = STEPS[step];
    const bar = `<div class="ws-steps">${STEPS.map((x, i) => `<div class="ws-step ${i < step ? 'done' : i === step ? 'cur' : ''}"><span>${i < step ? '✓' : i + 1}</span>${x.t}</div>`).join('')}</div><p class="muted">Шаг ${step + 1} из ${STEPS.length}</p>`;
    let body = '';
    if (cur.k === 'proxies') {
      const p = await api('/proxies');
      body = `<h3>Добавьте прокси <span class="pill">Необязательно</span></h3><p class="muted">Прокси даёт телефону отдельный IP нужной страны. Шаг можно пропустить и добавить прокси позже.</p>${p.map((x) => `<div class="item">${esc(x.proxy_ip)}:${x.proxy_port} · ${XSTATUS[x.status]}</div>`).join('') || '<div class="empty">Прокси пока нет</div>'}<p>Добавлено: ${c.proxies}</p><button class="btn secondary" onclick="wsProxyForm()">+ Добавить прокси</button>`;
    }
    if (cur.k === 'devices') {
      const d = await api('/devices');
      body = `<h3>Подключите первое устройство</h3><p class="muted">Телефон подключается к Mac по USB. Нажмите «Добавить устройство» и выполните команду в Терминале. Нужно хотя бы одно устройство.</p>${
        d
          .filter((x) => x.status !== 'REVOKED')
          .map(
            (x) =>
              `<div class="item">📱 ${esc(x.name)} · ${x.status === 'ONLINE' ? 'Подключено' : 'Не подключено'}</div>`
          )
          .join('') || '<div class="empty">Устройств пока нет</div>'
      }<p>Добавлено: ${c.devices}</p><button class="btn secondary" onclick="addDevice()">+ Добавить устройство</button> <button class="btn secondary" onclick="wsOnb()">Обновить</button>`;
    }
    if (cur.k === 'personas') {
      const p = await api('/personas');
      body = `<h3>Создайте персону</h3><p class="muted">Персона — от чьего имени работает аккаунт: интересы, расписание, характер. Нужна минимум одна.</p>${p.map((x) => `<div class="item">${esc(x.name)} · ${esc(x.device_name || '')}</div>`).join('') || '<div class="empty">Персон пока нет</div>'}<p>Добавлено: ${c.personas}</p><button class="btn secondary" onclick="wsPersonaForm()">+ Создать персону</button>`;
    }
    if (cur.k === 'accounts') {
      const a = await api('/account-profiles');
      body = `<h3>Привяжите аккаунт</h3><p class="muted">Привяжите аккаунт соцсети к персоне. Нужен минимум один аккаунт.</p>${a.map((x) => `<div class="item">${esc(x.platform)} · ${esc(x.username)} ${x.persona_name ? '· ' + esc(x.persona_name) : ''} ${x.id && !x.search_keywords ? `<button class="btn secondary" onclick="wsAccountForm('${x.id}')">Дополнить профиль</button>` : ''}</div>`).join('') || '<div class="empty">Аккаунтов пока нет</div>'}<p>Добавлено: ${c.accounts}</p><button class="btn secondary" onclick="wsAccountForm()">+ Добавить аккаунт</button>`;
    }
    if (cur.k === 'done')
      body = `<h3>Всё готово!</h3><p class="muted">Рабочее пространство настроено.</p><div class="list"><div class="item">Прокси: ${c.proxies}</div><div class="item">Устройства: ${c.devices}</div><div class="item">Персоны: ${c.personas}</div><div class="item">Аккаунты: ${c.accounts}</div></div><button class="btn" style="margin-top:14px" onclick="wsFinish()">Перейти на главную</button>`;
    const canNext = cur.k === 'done' ? false : ok(cur.k);
    const foot =
      cur.k === 'done'
        ? ''
        : `<div class="row" style="margin-top:18px">${step > 0 ? '<button class="close" onclick="wsStep(-1)">Назад</button>' : '<span></span>'}<div>${cur.k === 'proxies' && c.proxies === 0 ? '<button class="btn secondary" onclick="wsSkipProxies()">Пропустить</button> ' : ''}<button class="btn" ${canNext ? '' : 'disabled'} onclick="wsStep(1)">${cur.k === 'accounts' ? 'Завершить' : 'Продолжить'}</button></div></div>`;
    shell(
      'Настройка',
      'Несколько шагов, чтобы запустить FaxClip',
      `<div class="card ws-onb">${bar}${body}${foot}</div>`
    );
  }
  window.wsOnb = () => onboarding();
  window.wsStep = async (d) => {
    step = Math.max(0, Math.min(STEPS.length - 1, step + d));
    await onboarding();
  };
  window.wsSkipProxies = async () => {
    await api('/onboarding', { method: 'POST', body: JSON.stringify({ skip_proxies: true }) });
    step = 1;
    await onboarding();
  };
  window.wsFinish = async () => {
    try {
      await api('/onboarding', { method: 'POST', body: JSON.stringify({ finish: true }) });
      document.querySelector('nav [data-page="onboarding"]')?.remove();
      goPage('dashboard');
    } catch (e) {
      alert(e.message);
    }
  };

  /* ---------- wiring ---------- */
  Object.assign(routes, {
    onboarding,
    personas,
    proxies,
    alerts,
    accounts: wsAccounts,
    devices: wsDevices,
    dashboard: wsDashboard
  });
  window.dashboard = wsDashboard;
  window.accounts = wsAccounts;
  window.devices = wsDevices;
  window.addAccount = () => wsAccountForm();
  const navEl = document.querySelector('nav');
  const mk = (page, label, svg, beforePage) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'nav-item';
    b.dataset.page = page;
    b.innerHTML = `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${svg}</svg><span>${label}</span>`;
    b.onclick = () => goPage(page);
    const ref = navEl.querySelector(`[data-page="${beforePage}"]`);
    ref ? ref.before(b) : navEl.append(b);
  };
  mk(
    'personas',
    'Агенты',
    '<circle cx="9" cy="8" r="4"/><path d="M1 21a8 8 0 0 1 16 0"/><path d="M17 4a4 4 0 0 1 0 8m6 9a8 8 0 0 0-5-7.4"/>',
    'accounts'
  );
  mk(
    'proxies',
    'Прокси',
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    'publishing'
  );
  mk(
    'alerts',
    'Уведомления',
    '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    'publishing'
  );
  const pollBadge = () =>
    api('/alerts?limit=1')
      .then((a) => setBadge(a.unread))
      .catch(() => {});
  pollBadge();
  setInterval(pollBadge, 30000);
  if (document.querySelector('nav .nav-item.active')?.dataset.page === 'dashboard') goPage('dashboard');
  // «Настройка» (onboarding) removed from navigation: everything is configured in the sections themselves.
})();
