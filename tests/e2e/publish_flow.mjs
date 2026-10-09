// End-to-end: create a post in the UI → phone agent (simulated over the real bridge API) publishes it
// → the UI shows «Опубликован». Run against a server started with ALLOW_DEV_AUTH=1.
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';

const BASE = process.env.E2E_BASE || 'http://localhost:8010';
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=',
  'base64'
);
const j = async (path, opt = {}, headers = {}) => {
  const r = await fetch(BASE + path, {
    ...opt,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: opt.body && typeof opt.body !== 'string' && !(opt.body instanceof Buffer) ? JSON.stringify(opt.body) : opt.body
  });
  const x = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${JSON.stringify(x)}`);
  return x;
};
const step = (s) => console.log('•', s);

let browser;
try {
  step('API: device + @redmaagi account');
  const d = await j('/api/devices', { method: 'POST', body: { name: 'E2E phone', model: 'Android', connection: 'USB' } });
  const H = { 'X-Device-ID': d.id, Authorization: 'Bearer ' + d.device_token };
  await j('/api/accounts', { method: 'POST', body: { platform: 'TikTok', username: '@redmaagi', device_id: d.id } });
  await j('/api/bridge/heartbeat', { method: 'POST', body: { battery: 90 } }, H);

  step('UI: create and publish a post');
  let exe;
  try {
    exe = execSync('which chromium').toString().trim();
  } catch (e) {
    exe = undefined;
  }
  browser = await chromium.launch(exe ? { executablePath: exe } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof goPage === 'function');
  await page.evaluate(() => goPage('publishing'));
  await page.getByText('+ Новый пост').click();
  await page.locator('#pfFile').setInputFiles({
    name: 'e2e.mp4',
    mimeType: 'video/mp4',
    // minimal ISO-BMFF header (ftyp box) + unique tail: the server checks the container signature
    buffer: Buffer.concat([Buffer.from('00000018667479706d703432000000006d703432', 'hex'), Buffer.from('E2E' + Date.now())])
  });
  await page.fill('#pfTitle', 'E2E пост');
  await page.fill('#pfCap', 'E2E проверка публикации #faxclip');
  await page.locator('.pfAcc').first().check();
  await page.check('#pfOk');
  await page.check('#pfRights');
  await page.click('#pfGo');
  await page.waitForFunction(() => !document.querySelector('#modal.show #pfGo'), null, { timeout: 15000 });

  step('Bridge: phone claims, publishes and verifies');
  const { job } = await j('/api/bridge/claim', { method: 'POST', body: {} }, H);
  if (!job) throw new Error('phone did not receive the job');
  const L = { ...H, 'X-Job-Lease': job.lease };
  for (const phase of ['PUBLISH_STARTED', 'SUBMITTED', 'UI_CONFIRMED'])
    await j(`/api/bridge/jobs/${job.id}/phase`, { method: 'POST', body: { phase } }, L);
  const ev = await fetch(`${BASE}/api/bridge/jobs/${job.id}/evidence`, { method: 'POST', headers: L, body: PNG });
  if (!ev.ok) throw new Error('evidence upload failed: ' + ev.status);
  await j(
    `/api/bridge/jobs/${job.id}/complete`,
    {
      method: 'POST',
      body: {
        post_url: 'https://www.tiktok.com/@redmaagi/video/7234567890123456789',
        verification: 'PROFILE_MATCHING_POST_REOPENED_TWICE_BY_URL',
        sha256: job.payload.sha256,
        caption: job.payload.caption
      }
    },
    L
  );

  step('UI: post shows as published');
  await page.evaluate(() => goPage('publishing'));
  await page.getByText('E2E пост').first().waitFor({ timeout: 10000 });
  const row = page.locator('.ws-post', { hasText: 'E2E пост' }).first();
  await row.getByText('Опубликован', { exact: true }).first().waitFor({ timeout: 10000 });
  if (errors.length) throw new Error('browser errors: ' + errors.join(' | '));
  console.log('E2E OK');
  await page.close();
  await browser.close();
  process.exit(0);
} catch (e) {
  console.error('E2E FAILED:', e.message);
  if (browser) await browser.close();
  process.exit(1);
}
