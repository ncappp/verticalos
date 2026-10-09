/* FaxClip «Защита от банов» — TEST MODE: shows what protection would do, changes nothing. */
(function () {
  const KEY = 'fxAntibanRules';
  const S = { open: {} };
  const LV = { high: ['Высокий риск', 'red'], medium: ['Средний риск', 'red'], low: ['Низкий риск', ''] };
  const DEC = {
    allow: ['Пройдёт как есть', ''],
    delay: ['Задержала бы', 'red'],
    block: ['Придержала бы', 'red']
  };
  const fmt = (t) =>
    t
      ? new Date(t).toLocaleString('ru-RU', {
          day: 'numeric',
          month: 'short',
          hour: '2-digit',
          minute: '2-digit'
        })
      : '—';
  const pill = (m, k) => {
    const [l, c] = m[k] || [k, ''];
    return `<span class="pill"><i class="dot ${c}"></i>${l}</span>`;
  };
  const rules = () => {
    try {
      return JSON.parse(localStorage.getItem(KEY) || '{}');
    } catch (e) {
      return {};
    }
  };
  const bar = (v, max, label) => {
    const p = max ? Math.min(100, Math.round((v / max) * 100)) : v ? 100 : 0;
    return `<div class="ab-bar"><div class="muted">${label}: <b>${v}</b> / ${max || 0}</div><div class="ab-track"><i style="width:${p}%" class="${v > max ? 'over' : ''}"></i></div></div>`;
  };

  function rulesForm(r, d) {
    const st = [
      ['young', 'Новый'],
      ['warming', 'Прогревается'],
      ['hot', 'Прогретый']
    ];
    const grid = (k, title) =>
      `<h4>${title}</h4><div class="formgrid">${st
        .map(
          ([s, l]) => `<label>${l}<input type="number" min="0" id="ab_${k}_${s}" value="${r[k][s]}"></label>`
        )
        .join('')}</div>`;
    const one = (k, l, hint = '') =>
      `<label>${l}<input type="number" min="0" id="ab_${k}" value="${r[k]}">${hint ? `<span class="muted">${hint}</span>` : ''}</label>`;
    return `<div class="form">
      ${grid('posts_per_day', 'Публикаций в сутки')}
      ${grid('likes_per_day', 'Лайков в сутки (прогрев)')}
      ${grid('follows_per_day', 'Подписок в сутки (прогрев)')}
      <h4>Прочее</h4><div class="formgrid">
      ${one('min_gap_minutes', 'Минимум минут между постами')}
      ${one('quiet_from', 'Ночная тишина с (час)')}
      ${one('quiet_to', 'до (час)')}
      ${one('min_warm_sessions', 'Сессий прогрева до первого поста')}
      ${one('pause_after_captcha_hours', 'Пауза после капчи, ч')}
      ${one('max_failures_24h', 'Сбоев публикации за сутки до остановки')}
      ${one('max_accounts_per_device', 'Аккаунтов на один телефон')}
      ${one('device_stagger_minutes', 'Разнос постов на одном телефоне, мин')}
      </div>
      <label class="ws-check"><input type="checkbox" id="ab_require_proxy" ${r.require_proxy ? 'checked' : ''}> Предупреждать, если у телефона нет прокси</label>
      <div class="row"><button class="btn secondary" onclick="abReset()">Вернуть по умолчанию</button><button class="btn" onclick="abApply()">Пересчитать</button></div>
      <p class="muted">Правила сохраняются только в этом браузере и используются для расчёта. На сервере ничего не меняется.</p></div>`;
  }
  window.abApply = () => {
    const d = window._abData.defaults,
      out = {};
    for (const k of ['posts_per_day', 'likes_per_day', 'follows_per_day']) {
      out[k] = {};
      for (const s of ['young', 'warming', 'hot']) out[k][s] = +document.getElementById(`ab_${k}_${s}`).value;
    }
    for (const k of Object.keys(d)) {
      if (typeof d[k] === 'number') out[k] = +document.getElementById('ab_' + k).value;
    }
    out.require_proxy = document.getElementById('ab_require_proxy').checked;
    localStorage.setItem(KEY, JSON.stringify(out));
    page();
  };
  window.abReset = () => {
    localStorage.removeItem(KEY);
    page();
  };
  window.abToggle = (id) => {
    S.open[id] = !S.open[id];
    document.getElementById('abf_' + id)?.toggleAttribute('hidden');
  };

  async function page() {
    const d = await api('/antiban/preview?rules=' + encodeURIComponent(JSON.stringify(rules())));
    window._abData = d;
    const s = d.summary;
    const acc = d.accounts
      .map(
        (a) => `<div class="card ab-acc">
        <div class="row" style="justify-content:space-between;cursor:pointer" onclick="abToggle('${a.account_id}')">
          <div><b>${esc(a.username || '—')}</b> <span class="muted">${esc(a.platform)} · ${esc(a.stage_name)} · в FaxClip ${a.age_days} дн${a.device ? ' · ' + esc(a.device) : ''}</span></div>
          <div class="row">${pill(LV, a.level)}<span class="ab-score ${a.level}">${a.score}</span></div></div>
        <div class="grid ab-bars">${bar(a.posts_24h, a.posts_limit, 'Посты за 24 ч')}${bar(a.likes_today, a.likes_limit, 'Лайки сегодня')}${bar(a.follows_today, a.follows_limit, 'Подписки сегодня')}</div>
        <div id="abf_${a.account_id}" ${S.open[a.account_id] || a.level !== 'low' ? '' : 'hidden'}>
        ${
          a.findings.length
            ? `<div class="list">${a.findings
                .map(
                  (f) =>
                    `<div class="item ab-find ${f.level}"><div><b>${esc(f.text)}</b><div class="muted">🛡 ${esc(f.action)}</div></div></div>`
                )
                .join('')}</div>`
            : '<div class="muted">Нарушений нет — защита ничего бы не сделала.</div>'
        }
        <div class="muted">Прогрев: ${a.warm_sessions} сесс. · сбоев за сутки: ${a.failures_24h} · последний пост: ${fmt(a.last_post_at)} · пояс ${esc(a.timezone)}</div></div></div>`
      )
      .join('');
    const q = d.queue.length
      ? `<div style="overflow:auto"><table class="table"><thead><tr><th>Аккаунт</th><th>Запланировано</th><th>Решение защиты</th><th>Почему</th></tr></thead><tbody>${d.queue
          .map(
            (x) =>
              `<tr><td>${esc(x.username || '—')}</td><td>${fmt(x.planned_at)}</td><td>${pill(DEC, x.decision)}${x.local_time ? `<div class="muted">→ ${x.local_time} (время телефона)</div>` : ''}</td><td>${x.reasons.map(esc).join('<br>') || '—'}</td></tr>`
          )
          .join('')}</tbody></table></div>`
      : '<div class="empty">В ближайшие 48 часов публикаций в очереди нет</div>';
    const dw = d.device_warnings.length
      ? `<div class="card"><h3>Телефоны и сеть</h3><div class="list">${d.device_warnings
          .map(
            (w) =>
              `<div class="item"><div><b>${esc(w.device)}</b><div class="muted">${esc(w.text)}</div></div></div>`
          )
          .join('')}</div></div>`
      : '';
    shell(
      'Защита от банов',
      'Тестовый режим: показываем, что сделала бы защита',
      `<div class="card fx-banner warning"><span><b>Тестовый режим.</b> Защита ничего не блокирует и не переносит. Расчёт только читает данные: соединение с базой открыто в режиме «только чтение».</span></div>
      <div class="grid adm-cards">
        <div class="card"><div class="muted">Аккаунтов проверено</div><div class="adm-num">${s.accounts}</div><div class="muted">${s.high} высокий · ${s.medium} средний · ${s.low} низкий риск</div></div>
        <div class="card"><div class="muted">Публикаций в очереди (48 ч)</div><div class="adm-num">${s.queue}</div></div>
        <div class="card"><div class="muted">Задержала бы</div><div class="adm-num">${s.would_delay}</div></div>
        <div class="card"><div class="muted">Придержала бы</div><div class="adm-num">${s.would_block}</div></div>
        <div class="card"><div class="muted">Предупреждений</div><div class="adm-num">${s.warnings}</div></div>
      </div>
      ${dw}
      <div class="card"><h3>Очередь публикаций: что изменилось бы</h3>${q}</div>
      <h3 style="margin:18px 4px 8px">Аккаунты</h3>${acc || '<div class="card empty">Аккаунтов пока нет</div>'}
      <details class="card"><summary><b>Правила защиты</b> <span class="muted">— можно менять и сразу смотреть результат</span></summary>${rulesForm(d.rules, d.defaults)}</details>`,
      '<button class="btn" onclick="goPage(\'antiban\')">Пересчитать</button>'
    );
  }
  routes.antiban = page;
  const navEl = document.querySelector('nav');
  if (navEl && !navEl.querySelector('[data-page="antiban"]')) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'nav-item';
    b.dataset.page = 'antiban';
    b.innerHTML =
      '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/></svg><span>Защита</span>';
    b.onclick = () => goPage('antiban');
    const ref = navEl.querySelector('[data-page="analytics"]');
    ref ? ref.after(b) : navEl.append(b);
  }
})();
