import http from 'node:http';
import { randomBytes } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, normalize, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import { openDb } from './db.js';
import { seed } from './seed.js';
import * as D from './domain.js';
import * as V from './views.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = process.env.DATA_DIR || join(__dirname, '..', 'data');
const PHOTO_DIR = join(DATA_DIR, 'photos');
const DB_PATH = process.env.DB_PATH || join(DATA_DIR, 'app.db');
const PORT = Number(process.env.PORT || 3000);

// ---------- 会话（内存；重启需重新登录） ----------
const sessions = new Map(); // sid -> { uid, flash }

function parseCookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i > -1) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function getUser(db, req) {
  const sid = parseCookies(req).sid;
  if (!sid || !sessions.has(sid)) return null;
  const uid = sessions.get(sid).uid;
  return db.prepare('SELECT id, username, display_name, role FROM users WHERE id=?').get(uid) || null;
}

function setFlash(req, kind, text) {
  const sid = parseCookies(req).sid;
  if (sessions.has(sid)) sessions.get(sid).flash = { kind, text };
}

function takeFlash(req) {
  const sid = parseCookies(req).sid;
  const s = sessions.get(sid);
  if (!s) return null;
  const f = s.flash || null;
  s.flash = null;
  return f;
}

// ---------- 请求体解析 ----------
function readBody(req, limit = 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) { reject(new Error('请求体过大')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

async function parseForm(req) {
  const raw = (await readBody(req)).toString('utf8');
  const body = {};
  for (const pair of raw.split('&')) {
    if (!pair) continue;
    const i = pair.indexOf('=');
    const k = decodeURIComponent((i === -1 ? pair : pair.slice(0, i)).replace(/\+/g, ' '));
    const v = i === -1 ? '' : decodeURIComponent(pair.slice(i + 1).replace(/\+/g, ' '));
    body[k] = v;
  }
  return body;
}

// 极简 multipart 解析，仅支持单文件 + 普通字段
function parseMultipart(buf, contentType) {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
  if (!m) throw new Error('缺少 multipart boundary');
  const boundary = '--' + (m[1] || m[2]);
  const fields = {};
  const files = {};
  let start = buf.indexOf(boundary);
  while (start !== -1) {
    start += boundary.length;
    if (buf[start] === 0x2d && buf[start + 1] === 0x2d) break; // 结束 --
    if (buf[start] === 0x0d && buf[start + 1] === 0x0a) start += 2;
    const headerEnd = buf.indexOf('\r\n\r\n', start);
    if (headerEnd === -1) break;
    const headers = buf.slice(start, headerEnd).toString('utf8');
    const bodyStart = headerEnd + 4;
    const next = buf.indexOf(boundary, bodyStart);
    if (next === -1) break;
    let bodyEnd = next - 2; // 去掉 CRLF
    const nameM = /name="([^"]+)"/i.exec(headers);
    const fileM = /filename="([^"]*)"/i.exec(headers);
    if (nameM) {
      const content = buf.slice(bodyStart, bodyEnd);
      if (fileM && fileM[1]) {
        files[nameM[1]] = { filename: fileM[1], content,
          contentType: /Content-Type:\s*([^\r\n]+)/i.exec(headers)?.[1]?.trim() || 'application/octet-stream' };
      } else {
        fields[nameM[1]] = content.toString('utf8');
      }
    }
    start = next;
  }
  return { fields, files };
}

// ---------- 响应辅助 ----------
function html(res, status, content) {
  res.writeHead(status, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(content);
}
function redirect(res, location) {
  res.writeHead(302, { Location: location });
  res.end();
}

export function createApp(db, { dataDir = DATA_DIR, photoDir = PHOTO_DIR } = {}) {
  const notifier = D.createNotifier(db);

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const p = url.pathname;
    const method = req.method;
    const user = getUser(db, req);

    try {
      // ---- 静态照片（须登录；防目录穿越） ----
      if (method === 'GET' && p.startsWith('/photos/')) {
        if (!user) return redirect(res, '/login');
        const name = decodeURIComponent(p.slice('/photos/'.length));
        const safe = normalize(name).replace(/^([./\\])+/, '');
        const full = join(photoDir, safe);
        if (!full.startsWith(photoDir) || !existsSync(full)) { res.writeHead(404); return res.end('not found'); }
        const data = await readFile(full);
        const type = extname(full).toLowerCase() === '.svg' ? 'image/svg+xml'
          : extname(full).toLowerCase() === '.png' ? 'image/png'
          : extname(full).toLowerCase() === '.jpg' || extname(full) === '.jpeg' ? 'image/jpeg'
          : 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': type });
        return res.end(data);
      }

      if (p === '/login' && method === 'GET') {
        if (user) return redirect(res, '/');
        return html(res, 200, V.loginView(takeFlash(req)));
      }
      if (p === '/login' && method === 'POST') {
        const body = await parseForm(req);
        const u = db.prepare('SELECT * FROM users WHERE username=? AND pass=?')
          .get(body.username?.trim(), body.password || '');
        if (!u) {
          D.logAudit(db, { actor: body.username || '?', action: 'login', allowed: false, detail: '口令错误' });
          res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
          return html(res, 200, V.loginView({ kind: 'err', text: '用户名或口令错误' }));
        }
        const sid = randomBytes(16).toString('hex');
        sessions.set(sid, { uid: u.id, flash: { kind: 'ok', text: `欢迎，${u.display_name}` } });
        D.logAudit(db, { actor: u.username, action: 'login', allowed: true });
        res.setHeader('Set-Cookie', `sid=${sid}; Path=/; HttpOnly; SameSite=Lax`);
        return redirect(res, '/');
      }
      if (p === '/logout' && method === 'POST') {
        const sid = parseCookies(req).sid;
        if (sid) sessions.delete(sid);
        res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
        return redirect(res, '/login');
      }

      if (!user) return redirect(res, '/login');
      const requireRole = (roles, where) => {
        if (!roles.includes(user.role)) {
          D.logAudit(db, { actor: user.username, action: 'access_denied', targetType: 'page',
            targetId: where, allowed: false, detail: `角色 ${user.role} 越权访问` });
          throw new D.DomainError(403, 'forbidden', '无权访问该页面/操作');
        }
      };

      const flash = () => takeFlash(req);
      const samples = () => D.listSamples(db, user, notifier);
      const sampleEvents = (id) => db.prepare('SELECT * FROM events WHERE sample_id=? ORDER BY id').all(id);

      // ---- 首页：收样登记 + 台账 ----
      if (p === '/' && method === 'GET') {
        const suppliers = db.prepare('SELECT id, name FROM suppliers ORDER BY id').all();
        return html(res, 200, V.homeView({ user, samples: samples(), suppliers, flash: flash() }));
      }
      if (p === '/samples/receive' && method === 'POST') {
        requireRole(['admin', 'buyer'], p);
        const body = await parseForm(req);
        D.receiveSample(db, notifier, body, user);
        setFlash(req, 'ok', `收样登记完成：${body.code}（封存编号 ${body.seal_no}）`);
        return redirect(res, '/');
      }

      // ---- 封样库位 / 破损 / 撤样 / 照片 ----
      if (p === '/storage' && method === 'GET') {
        return html(res, 200, V.storageView({ user, samples: samples(), flash: flash() }));
      }
      let mm;
      if ((mm = /^\/samples\/(\d+)\/storage$/.exec(p)) && method === 'POST') {
        requireRole(['admin', 'buyer'], p);
        const body = await parseForm(req);
        D.setStorage(db, Number(mm[1]), user, body.location || '');
        setFlash(req, 'ok', '库位已保存');
        return redirect(res, '/storage');
      }
      if ((mm = /^\/samples\/(\d+)\/seal-broken$/.exec(p)) && method === 'POST') {
        requireRole(['admin', 'buyer'], p);
        D.reportBrokenSeal(db, notifier, Number(mm[1]), user, '库内巡检发现封签破损');
        setFlash(req, 'err', '已登记封签破损，样品退出盲评并通知相关方');
        return redirect(res, '/storage');
      }
      if ((mm = /^\/samples\/(\d+)\/withdraw$/.exec(p)) && method === 'POST') {
        requireRole(['admin', 'buyer'], p);
        const body = await parseForm(req);
        D.withdrawSample(db, notifier, Number(mm[1]), user, body.reason);
        setFlash(req, 'err', '供应商撤样已登记并留痕');
        return redirect(res, '/storage');
      }
      if ((mm = /^\/samples\/(\d+)\/photo$/.exec(p)) && method === 'POST') {
        requireRole(['admin', 'buyer'], p);
        const buf = await readBody(req, 8 * 1024 * 1024);
        const { files } = parseMultipart(buf, req.headers['content-type'] || '');
        const f = files.photo;
        if (!f || !f.content.length) throw new D.DomainError(400, 'no_file', '未收到照片文件');
        const ext = (extname(f.filename).toLowerCase().match(/^\.(jpg|jpeg|png|gif|webp|svg)$/) || ['.jpg'])[0];
        if (!f.contentType.startsWith('image/')) throw new D.DomainError(400, 'bad_image', '只接受图片文件');
        await mkdir(photoDir, { recursive: true });
        const id = Number(mm[1]);
        const rel = `photos/${randomBytes(8).toString('hex')}${ext}`;
        await writeFile(join(dataDir, rel), f.content);
        D.setPhoto(db, id, user, rel);
        setFlash(req, 'ok', '样品照片已保存到本地');
        return redirect(res, '/storage');
      }

      // ---- 匿名身份探测（评审人揭盲前必拒） ----
      if ((mm = /^\/samples\/(\d+)\/identity-probe$/.exec(p)) && method === 'POST') {
        D.probeIdentity(db, Number(mm[1]), user, notifier);
        setFlash(req, 'ok', '样品已揭盲，供应商信息可在结果页查看');
        return redirect(res, `/result/${mm[1]}`);
      }

      // ---- 回避申请 ----
      if ((mm = /^\/samples\/(\d+)\/recusal$/.exec(p)) && method === 'POST') {
        requireRole(['reviewer'], p);
        const body = await parseForm(req);
        D.requestRecusal(db, notifier, Number(mm[1]), user, body.reason);
        setFlash(req, 'ok', '回避申请已提交，等待管理员审批');
        return redirect(res, `/review/${mm[1]}`);
      }
      if (p === '/admin/recusals' && method === 'GET') {
        requireRole(['admin'], p);
        const recusals = db.prepare(
          `SELECT rc.*, s.code, u.display_name AS reviewer_name FROM recusals rc
           JOIN samples s ON s.id=rc.sample_id JOIN users u ON u.id=rc.reviewer_id
           ORDER BY rc.id DESC`).all();
        return html(res, 200, V.recusalAdminView({ user, recusals, flash: flash() }));
      }
      if ((mm = /^\/admin\/recusals\/(\d+)\/decide$/.exec(p)) && method === 'POST') {
        requireRole(['admin'], p);
        const body = await parseForm(req);
        D.decideRecusal(db, notifier, Number(mm[1]), body.approve === '1', user);
        setFlash(req, 'ok', body.approve === '1' ? '已批准回避' : '已驳回回避申请');
        return redirect(res, '/admin/recusals');
      }

      // ---- 盲评 ----
      if (p === '/review' && method === 'GET') {
        requireRole(['reviewer'], p);
        const all = samples();
        const myScores = db.prepare('SELECT * FROM scores WHERE reviewer_id=?').all(user.id);
        const scoreMap = new Map(myScores.map((s) => [s.sample_id, s]));
        const recused = new Set(db.prepare(
          `SELECT sample_id FROM recusals WHERE reviewer_id=? AND status='approved'`).all(user.id).map((r) => r.sample_id));
        const viewSamples = all.map((s) => ({ ...s, my_score: scoreMap.get(s.id) || null }));
        return html(res, 200, V.reviewListView({ user, samples: viewSamples, recusedIds: recused, flash: flash() }));
      }
      if ((mm = /^\/review\/(\d+)$/.exec(p)) && method === 'GET') {
        const id = Number(mm[1]);
        const sample = D.getSample(db, id, user, notifier);
        const events = sampleEvents(id);
        const recusedRow = user.role === 'reviewer'
          ? db.prepare(`SELECT id, status FROM recusals WHERE sample_id=? AND reviewer_id=?`).get(id, user.id) : null;
        const myScore = user.role === 'reviewer'
          ? db.prepare('SELECT * FROM scores WHERE sample_id=? AND reviewer_id=?').get(id, user.id) : null;
        return html(res, 200, V.reviewView({
          user, sample, events, recused: recusedRow?.status === 'approved', myScore, flash: flash(),
        }));
      }
      if ((mm = /^\/review\/(\d+)\/scores$/.exec(p)) && method === 'POST') {
        const body = await parseForm(req);
        D.submitScore(db, Number(mm[1]), user, body);
        setFlash(req, 'ok', '评分已提交，封存至揭盲');
        return redirect(res, '/review');
      }

      // ---- 揭盲：操作限管理员/采购员，其他角色可只读查看 ----
      if (p === '/reveal' && method === 'GET') {
        const rows = db.prepare(
          `SELECT s.*, (SELECT COUNT(*) FROM scores sc WHERE sc.sample_id=s.id) AS score_count
           FROM samples s ORDER BY s.id DESC`).all();
        return html(res, 200, V.revealListView({ user, samples: rows, flash: flash() }));
      }
      if ((mm = /^\/reveal\/(\d+)$/.exec(p)) && method === 'GET') {
        const id = Number(mm[1]);
        const sample = D.getSample(db, id, user, notifier);
        const count = db.prepare('SELECT COUNT(*) AS n FROM scores WHERE sample_id=?').get(id).n;
        if (user.role !== 'admin' && user.role !== 'buyer') {
          return html(res, 200, V.revealViewReadonly({ user, sample, scoreCount: count, flash: flash() }));
        }
        return html(res, 200, V.revealView({ user, sample, scoreCount: count, flash: flash() }));
      }
      if ((mm = /^\/reveal\/(\d+)$/.exec(p)) && method === 'POST') {
        requireRole(['admin', 'buyer'], p);
        const id = Number(mm[1]);
        const body = await parseForm(req);
        const second = db.prepare('SELECT * FROM users WHERE username=?').get(body.second_user?.trim());
        if (!second || second.pass !== body.second_pass) {
          D.logAudit(db, { actor: user.username, action: 'reveal_second_auth',
            targetType: 'sample', targetId: String(id), allowed: false, detail: '第二揭盲人身份核验失败' });
          throw new D.DomainError(403, 'second_auth_failed', '第二揭盲人用户名或口令错误');
        }
        if (!['admin', 'buyer'].includes(second.role)) {
          throw new D.DomainError(403, 'second_role', '第二揭盲人须为管理员或采购员');
        }
        const { alreadyRevealed } = D.reveal(db, notifier, id, user, second);
        setFlash(req, alreadyRevealed ? 'err' : 'ok',
          alreadyRevealed ? '该样品已揭盲（竞态请求只生效一次），以下为既有结果' : '揭盲完成，供应商与评审意见已公开');
        return redirect(res, `/result/${id}`);
      }

      // ---- 结果与复核 ----
      if (p === '/results' && method === 'GET') {
        return html(res, 200, V.resultsListView({ user, samples: samples(), flash: flash() }));
      }
      if ((mm = /^\/result\/(\d+)$/.exec(p)) && method === 'GET') {
        const id = Number(mm[1]);
        const data = D.getResult(db, id);
        const sample = data?.sample || D.getSample(db, id, user, notifier);
        const myReview = user.role === 'reviewer'
          ? db.prepare('SELECT * FROM result_reviews WHERE sample_id=? AND reviewer_id=?').get(id, user.id) : null;
        return html(res, 200, V.resultView({
          user, data: data || { sample, reviews: [], result: null }, myReview,
          events: sampleEvents(id), flash: flash(),
        }));
      }
      if ((mm = /^\/result\/(\d+)\/review$/.exec(p)) && method === 'POST') {
        requireRole(['reviewer'], p);
        const body = await parseForm(req);
        D.submitResultReview(db, notifier, Number(mm[1]), user, body.agrees === '1', body.opinion);
        setFlash(req, 'ok', '选用意见已提交');
        return redirect(res, `/result/${mm[1]}`);
      }

      // ---- 审计 ----
      if (p === '/audit' && method === 'GET') {
        requireRole(['admin'], p);
        const logs = db.prepare('SELECT * FROM audit_log ORDER BY id DESC LIMIT 300').all();
        return html(res, 200, V.auditView({ user, logs, flash: flash() }));
      }

      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404 Not Found');
    } catch (e) {
      const status = e instanceof D.DomainError ? e.status : 500;
      if (status >= 500) console.error(e);
      const text = e instanceof D.DomainError ? e.message : '服务器内部错误';
      if (method === 'POST') {
        setFlash(req, 'err', text);
        return redirect(res, req.headers.referer?.replace(/^https?:\/\/[^/]+/, '') || '/');
      }
      html(res, status, `<h2>${status}</h2><p>${text}</p><p><a href="/">返回首页</a></p>`);
    }
  });

  return server;
}

export async function start() {
  await mkdir(PHOTO_DIR, { recursive: true });
  const db = openDb(DB_PATH);
  seed(db, PHOTO_DIR);
  const server = createApp(db, { dataDir: DATA_DIR, photoDir: PHOTO_DIR });
  server.listen(PORT, () => {
    console.log(`\n🥬 食材封样盲评系统已启动: http://localhost:${PORT}`);
    console.log(`   数据库: ${DB_PATH}`);
    console.log(`   账号: admin/admin123 · buyer/buyer123 · r1/rev123 · r2/rev123 · r3/rev123\n`);
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  start();
}
