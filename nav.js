/* FaxClip navigation: 8 sections with sub-sections instead of 17 flat items. Every old page keeps working. */
(function () {
  const GROUPS = [
    { key: 'dashboard', label: 'Главная', icon: 'dashboard', pages: [['dashboard', 'Главная']] },
    {
      key: 'g_pub',
      label: 'Публикации',
      icon: 'publishing',
      pages: [
        ['publishing', 'Посты'],
        ['content', 'Медиатека'],
        ['tasks', 'Цели']
      ]
    },
    {
      key: 'g_acc',
      label: 'Аккаунты',
      icon: 'accounts',
      pages: [
        ['accounts', 'Аккаунты'],
        ['personas', 'Агенты'],
        ['warmup', 'Прогрев'],
        ['antiban', 'Защита от банов']
      ]
    },
    {
      key: 'g_dev',
      label: 'Устройства',
      icon: 'devices',
      pages: [
        ['devices', 'Телефоны и экран'],
        ['proxies', 'Прокси']
      ]
    },
    { key: 'assembly', label: 'Склейка', icon: 'assembly', pages: [['assembly', 'Склейка']] },
    {
      key: 'g_an',
      label: 'Аналитика',
      icon: 'analytics',
      pages: [
        ['analytics', 'Статистика'],
        ['links', 'Ссылки с метками']
      ]
    },
    { key: 'alerts', label: 'Уведомления', icon: 'alerts', pages: [['alerts', 'Уведомления']] },
    { key: 'admin', label: 'Админка', icon: 'admin', pages: [['admin', 'Админка']] }
  ];
  const FEATURE = {
    assembly: 'assembly',
    warmup: 'warmup',
    analytics: 'analytics',
    links: 'links',
    antiban: 'antiban'
  };
  const ADMIN_ICON =
    '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>';
  const icons = {};
  const last = {};
  const navEl = document.querySelector('nav');
  const original = window.goPage;

  function allowed(page) {
    const s = window.FX_SESSION;
    if (page === 'admin') return !!(s && s.is_admin);
    if (!s || !FEATURE[page]) return true;
    return !s.features || s.features[FEATURE[page]] !== false;
  }
  const groupOf = (page) => GROUPS.find((g) => g.pages.some((p) => p[0] === page));
  const visiblePages = (g) => g.pages.filter((p) => allowed(p[0]));

  function render() {
    if (!navEl) return;
    navEl.querySelectorAll('.nav-item').forEach((b) => {
      if (b.dataset.page && !icons[b.dataset.page])
        icons[b.dataset.page] = b.querySelector('svg')?.outerHTML || '';
    });
    icons.admin = icons.admin || ADMIN_ICON;
    const badge = navEl.querySelector('[data-page="alerts"] .nbadge')?.outerHTML || '';
    navEl.innerHTML = '';
    for (const g of GROUPS) {
      if (!visiblePages(g).length) continue;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'nav-item';
      b.dataset.page = g.key;
      b.dataset.group = g.key;
      b.innerHTML = (icons[g.icon] || '') + `<span>${g.label}${g.key === 'alerts' ? ' ' + badge : ''}</span>`;
      b.onclick = () => window.goPage(g.key);
      navEl.append(b);
    }
    mark(window.FX_CURRENT_PAGE || 'dashboard');
  }

  function mark(page) {
    const g = groupOf(page);
    navEl?.querySelectorAll('.nav-item').forEach((n) => {
      const on = !!g && n.dataset.group === g.key;
      n.classList.toggle('active', on);
      n.setAttribute('aria-current', on ? 'page' : 'false');
    });
    let sub = document.getElementById('subnav');
    const pages = g ? visiblePages(g) : [];
    if (pages.length < 2) {
      sub?.remove();
      return;
    }
    if (!sub) {
      sub = document.createElement('div');
      sub.id = 'subnav';
      sub.className = 'tabs ws-tabs subnav';
      view.before(sub);
    }
    sub.innerHTML = pages
      .map(
        ([p, l]) =>
          `<button type="button" class="${p === page ? 'active' : ''}" data-sub="${p}">${l}</button>`
      )
      .join('');
    sub.querySelectorAll('button').forEach((b) => (b.onclick = () => window.goPage(b.dataset.sub)));
  }

  window.goPage = function (name) {
    const g0 = GROUPS.find((x) => x.key === name && x.pages.length > 1);
    if (g0) {
      const pages = visiblePages(g0).map((p) => p[0]);
      name = pages.includes(last[g0.key]) ? last[g0.key] : pages[0];
    }
    if (!allowed(name)) name = 'dashboard';
    const g = groupOf(name);
    if (g) last[g.key] = name;
    window.FX_CURRENT_PAGE = name;
    const run = original(name);
    mark(name);
    return run;
  };
  // Old flat pages that are now part of other sections
  routes.onboarding = routes.dashboard;
  routes.registration = () => window.goPage('accounts');

  render();
  window.addEventListener('fx-session', () => {
    render();
    if (!allowed(window.FX_CURRENT_PAGE || 'dashboard')) window.goPage('dashboard');
  });
})();
