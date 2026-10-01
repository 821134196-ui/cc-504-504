import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { openDb } from '../src/db.js';
import { seed } from '../src/seed.js';
import { createApp } from '../src/server.js';

async function startServer() {
  const dataDir = mkdtempSync(join(tmpdir(), 'bsr-http-'));
  const photoDir = join(dataDir, 'photos');
  const db = openDb(join(dataDir, 'app.db'));
  seed(db, photoDir);
  const server = createApp(db, { dataDir, photoDir });
  server.listen(0);
  await once(server, 'listening');
  const base = `http://localhost:${server.address().port}`;
  return {
    base, db,
    stop: async () => { await new Promise((r) => server.close(r)); db.close(); rmSync(dataDir, { recursive: true, force: true }); },
  };
}

// 极简带 cookie 的客户端
function client(base) {
  let cookie = '';
  const req = async (method, path, { form, raw } = {}) => {
    const headers = {};
    let body;
    if (form) {
      headers['Content-Type'] = 'application/x-www-form-urlencoded';
      body = new URLSearchParams(form).toString();
    } else if (raw) {
      headers['Content-Type'] = raw.type;
      body = raw.buf;
    }
    if (cookie) headers.Cookie = cookie;
    const res = await fetch(base + path, { method, headers, body, redirect: 'manual' });
    const sc = res.headers.get('set-cookie');
    if (sc) cookie = sc.split(';')[0];
    const location = res.headers.get('location');
    const text = () => res.text();
    return { status: res.status, location, text, headers };
  };
  return {
    request: req,
    async login(username, password) { return req('POST', '/login', { form: { username, password } }); },
    async follow(path) { const r = await req('GET', path); return { status: r.status, body: await r.text() }; },
  };
}

test('HTTP 鉴权：未登录被重定向；错误口令不建立会话', async () => {
  const s = await startServer();
  try {
    const c = client(s.base);
    const anon = await c.request('GET', '/review');
    assert.equal(anon.status, 302);
    assert.match(anon.location, /\/login$/);
    const bad = await c.login('r1', 'wrong');
    assert.equal(bad.status, 200); // 重新渲染登录页
    const body = await bad.text();
    assert.match(body, /用户名或口令错误/);
    const stillAnon = await c.request('GET', '/');
    assert.equal(stillAnon.status, 302);
  } finally { await s.stop(); }
});

test('HTTP 匿名隔离：评审人页面无供应商字样，采购员页面有', async () => {
  const s = await startServer();
  try {
    const rev = client(s.base);
    await rev.login('r1', 'rev123');
    const list = await rev.follow('/review');
    assert.equal(list.status, 200);
    assert.doesNotMatch(list.body, /绿源农场|丰禾蔬菜|润民肉业/);
    assert.match(list.body, /盲评纪律/);
    const form = await rev.follow('/review/1');
    assert.doesNotMatch(form.body, /绿源农场/);
    assert.match(form.body, /供应商身份已密封/);

    const buyer = client(s.base);
    await buyer.login('buyer', 'buyer123');
    const ledger = await buyer.follow('/');
    assert.match(ledger.body, /绿源农场|丰禾蔬菜|润民肉业/);
  } finally { await s.stop(); }
});

test('HTTP 身份探测：揭盲前点击被拒且写审计', async () => {
  const s = await startServer();
  try {
    const c = client(s.base);
    await c.login('r1', 'rev123');
    const r = await c.request('POST', '/samples/1/identity-probe');
    assert.equal(r.status, 302);
    const page = await c.follow(r.location);
    assert.match(page.body, /揭盲前禁止查询供应商身份/);
    const n = s.db.prepare(
      "SELECT COUNT(*) n FROM audit_log WHERE action='probe_supplier_identity' AND allowed=0").get().n;
    assert.ok(n >= 1);
  } finally { await s.stop(); }
});

test('HTTP 采购员不能代填分；评审人漏项被退回并留痕', async () => {
  const s = await startServer();
  try {
    const buyer = client(s.base);
    await buyer.login('buyer', 'buyer123');
    const denied = await buyer.request('POST', '/review/1/scores',
      { form: { taste: '9', spec: '9', cooking_loss: '5' } });
    assert.equal(denied.status, 302);
    const page = await buyer.follow(denied.location);
    assert.match(page.body, /只有评审人本人可以填写盲评表/);
    assert.equal(s.db.prepare('SELECT COUNT(*) n FROM scores').get().n, 2, '种子评分不增加');

    const rev = client(s.base);
    await rev.login('r1', 'rev123');
    const incomplete = await rev.request('POST', '/review/1/scores',
      { form: { taste: '8', spec: '8' } });
    const errPage = await rev.follow(incomplete.location);
    assert.match(errPage.body, /评分存在漏项/);
    assert.ok(s.db.prepare("SELECT COUNT(*) n FROM events WHERE type='score_missing_items'").get().n >= 1);
  } finally { await s.stop(); }
});

test('HTTP 回避全流程：申请→批准→评分入口关闭', async () => {
  const s = await startServer();
  try {
    // 选一份没有预置回避的样品（SP-2026-001 = id 1），由 r3 申请
    const r3 = client(s.base);
    await r3.login('r3', 'rev123');
    const apply = await r3.request('POST', '/samples/1/recusal', { form: { reason: '与供应商有业务往来' } });
    assert.equal(apply.status, 302);

    const admin = client(s.base);
    await admin.login('admin', 'admin123');
    const list = await admin.follow('/admin/recusals');
    assert.match(list.body, /与供应商有业务往来/);
    const recusalId = s.db.prepare(
      "SELECT id FROM recusals WHERE sample_id=1 AND reviewer_id=(SELECT id FROM users WHERE username='r3')").get().id;
    await admin.request('POST', `/admin/recusals/${recusalId}/decide`, { form: { approve: '1' } });

    // r3 评分被拦截
    const blocked = await r3.request('POST', '/review/1/scores',
      { form: { taste: '8', spec: '8', cooking_loss: '10' } });
    const blockedPage = await r3.follow(blocked.location);
    assert.match(blockedPage.body, /已获准回避/);
    assert.equal(s.db.prepare('SELECT COUNT(*) n FROM scores WHERE sample_id=1').get().n, 0);
  } finally { await s.stop(); }
});

test('HTTP 揭盲竞态：8 个并发两人揭盲请求只产生一次结果', async () => {
  const s = await startServer();
  try {
    // 两名评审先评分（种子里 sample 1 无评分；r2 对 004 有回避，001 没有）
    const r1 = client(s.base); await r1.login('r1', 'rev123');
    await r1.request('POST', '/review/1/scores', { form: { taste: '8', spec: '7', cooking_loss: '12.5' } });
    const r3 = client(s.base); await r3.login('r3', 'rev123');
    await r3.request('POST', '/review/1/scores', { form: { taste: '9', spec: '8', cooking_loss: '10' } });

    // 第二人口令错误 → 拒绝
    const admin = client(s.base); await admin.login('admin', 'admin123');
    const badSecond = await admin.request('POST', '/reveal/1',
      { form: { second_user: 'buyer', second_pass: 'WRONG' } });
    const badPage = await admin.follow(badSecond.location);
    assert.match(badPage.body, /第二揭盲人用户名或口令错误/);
    assert.equal(s.db.prepare('SELECT COUNT(*) n FROM revelations WHERE sample_id=1').get().n, 0);

    // 同一会话无法模拟两个浏览器，故用 8 个独立 admin 会话并发
    const racers = await Promise.all(Array.from({ length: 8 }, async () => {
      const c = client(s.base);
      await c.login('admin', 'admin123');
      return c.request('POST', '/reveal/1', { form: { second_user: 'buyer', second_pass: 'buyer123' } });
    }));
    for (const r of racers) assert.equal(r.status, 302);

    assert.equal(s.db.prepare('SELECT COUNT(*) n FROM revelations WHERE sample_id=1').get().n, 1,
      '揭盲结果唯一');
    assert.equal(s.db.prepare("SELECT COUNT(*) n FROM notifications WHERE type='sample_revealed'").get().n, 1);
    assert.equal(s.db.prepare('SELECT status FROM samples WHERE id=1').get().status, 'revealed');

    // 揭盲后评审人结果页可见供应商与意见对照
    const result = await r1.follow('/result/1');
    assert.match(result.body, /绿源农场合作社/);
    assert.match(result.body, /平均口感/);
    // 复核
    const rr = await r1.request('POST', '/result/1/review',
      { form: { agrees: '1', opinion: '综合表现良好，同意选用' } });
    assert.equal(rr.status, 302);
    const again = await r1.request('POST', '/result/1/review',
      { form: { agrees: '0', opinion: '重复复核应被拒绝' } });
    const againPage = await r1.follow(again.location);
    assert.match(againPage.body, /您已复核过该样品/);
  } finally { await s.stop(); }
});

test('HTTP 封签破损样例随启动存在，且不能评分/揭盲', async () => {
  const s = await startServer();
  try {
    const broken = s.db.prepare("SELECT * FROM samples WHERE code='SP-2026-002'").get();
    assert.equal(broken.status, 'broken');
    assert.ok(s.db.prepare("SELECT COUNT(*) n FROM events WHERE sample_id=? AND type='seal_broken'")
      .get(broken.id).n >= 1);

    const r1 = client(s.base); await r1.login('r1', 'rev123');
    const tryScore = await r1.request('POST', `/review/${broken.id}/scores`,
      { form: { taste: '5', spec: '5', cooking_loss: '10' } });
    const page = await r1.follow(tryScore.location);
    assert.match(page.body, /不接受评分|状态为「封签破损」/);
  } finally { await s.stop(); }
});
