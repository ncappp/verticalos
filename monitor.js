/* FaxClip: reports browser errors to the server (admin panel → «Ошибки»). */
(function () {
  let sent = 0;
  const seen = new Set();
  function report(message, stack, source) {
    const key = String(message).slice(0, 120);
    if (sent >= 10 || seen.has(key)) return;
    seen.add(key);
    sent++;
    try {
      const headers =
        typeof authHeaders === 'function' ? authHeaders({ 'Content-Type': 'application/json' }) : {};
      fetch('/api/client-error', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: String(message).slice(0, 200),
          stack: String(stack || '').slice(0, 3000),
          source: String(source || '').slice(0, 300),
          page: document.querySelector('nav .nav-item.active')?.dataset.page || location.hash,
          user_agent: navigator.userAgent
        })
      }).catch(() => {});
    } catch (e) {
      /* never break the app because of reporting */
    }
  }
  window.addEventListener('error', (e) =>
    report(e.message, e.error && e.error.stack, e.filename + ':' + e.lineno)
  );
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason || {};
    if (r.status && r.status < 500) return; // expected API answers (validation, limits)
    report(r.message || String(r), r.stack, 'promise');
  });
  window.fxReportError = report;
})();
