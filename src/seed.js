import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { createNotifier } from './domain.js';

const now = () => new Date().toISOString();
const daysFromNow = (d) => new Date(Date.now() + d * 86400000).toISOString();
const daysAgo = (d) => new Date(Date.now() - d * 86400000).toISOString();

// 本地占位照片（SVG，浏览器可直接显示；真实场景替换为收样拍摄的 JPG）
function placeholderPhoto(dir, code, label) {
  const path = join(dir, `${code}.svg`);
  writeFileSync(path, `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200">
  <rect width="320" height="200" fill="#f4f5f2"/>
  <rect x="8" y="8" width="304" height="184" fill="none" stroke="#b9bdb4" stroke-width="2" stroke-dasharray="6 5"/>
  <text x="160" y="96" font-family="sans-serif" font-size="20" fill="#3a3f44" text-anchor="middle">样品照片</text>
  <text x="160" y="126" font-family="sans-serif" font-size="15" fill="#5a6058" text-anchor="middle">${label} · ${code}</text>
</svg>`);
  return `photos/${code}.svg`;
}

export function seed(db, photoDir) {
  const existing = db.prepare('SELECT COUNT(*) AS n FROM users').get();
  if (existing.n > 0) return false;
  mkdirSync(photoDir, { recursive: true });
  const notifier = createNotifier(db);
  const t = now();

  const insUser = db.prepare(
    `INSERT INTO users (username, pass, display_name, role) VALUES (?,?,?,?)`);
  const users = {
    admin: insUser.run('admin', 'admin123', '系统管理员', 'admin').lastInsertRowid,
    buyer: insUser.run('buyer', 'buyer123', '采购员老周', 'buyer').lastInsertRowid,
    r1: insUser.run('r1', 'rev123', '评审甲·王师傅', 'reviewer').lastInsertRowid,
    r2: insUser.run('r2', 'rev123', '评审乙·李营养师', 'reviewer').lastInsertRowid,
    r3: insUser.run('r3', 'rev123', '评审丙·张厨师长', 'reviewer').lastInsertRowid,
  };

  const insSup = db.prepare(`INSERT INTO suppliers (name, contact, created_at) VALUES (?,?,?)`);
  const sup = {
    green: insSup.run('绿源农场合作社', '刘经理 138-0000-1001', t).lastInsertRowid,
    feng: insSup.run('丰禾蔬菜配送', '陈经理 138-0000-2002', t).lastInsertRowid,
    meat: insSup.run('润民肉业', '赵经理 138-0000-3003', t).lastInsertRowid,
    egg: insSup.run('晨曦禽蛋', '孙经理 138-0000-4004', t).lastInsertRowid,
  };

  const ins = db.prepare(`INSERT INTO samples
    (code, category, origin, arrival_temp, temp_limit, batch_no, seal_no, supplier_id,
     storage_location, photo_path, received_at, expires_at, status, is_current, created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);

  const rows = [
    // 1 正常封存样：可进入盲评
    ['SP-2026-001', '番茄', '山东寿光', 4.1, 7.0, 'B20260928-A', 'FC-260001', sup.green,
     '冷库 A-01', daysFromNow(7), 'sealed', 1],
    // 2 封签破损样（启动即自带的样例）：不予盲评
    ['SP-2026-002', '土豆', '内蒙古乌兰察布', 8.5, 12.0, 'B20260928-B', 'FC-260002', sup.feng,
     '待处理区 Q-00', daysFromNow(20), 'broken', 0],
    // 3 到货超温样：留痕，不进入盲评
    ['SP-2026-003', '冷鲜猪前腿肉', '河南漯河', 6.4, 4.0, 'B20260929-C', 'FC-260003', sup.meat,
     '待处理区 Q-01', daysFromNow(3), 'overtemp', 1],
    // 4 正常封存样：评审乙已申请回避
    ['SP-2026-004', '鲜鸡蛋', '河北保定', 12.0, 15.0, 'B20260929-D', 'FC-260004', sup.egg,
     '常温库 C-03', daysFromNow(15), 'sealed', 1],
    // 5 收样时已过期
    ['SP-2026-005', '生菜', '云南昆明', 3.0, 7.0, 'B20260920-E', 'FC-260005', sup.feng,
     '待处理区 Q-02', daysAgo(2), 'expired', 0],
    // 6 第二份正常样，用于多供应商揭盲对照
    ['SP-2026-006', '番茄', '辽宁朝阳', 5.2, 7.0, 'B20260930-F', 'FC-260006', sup.egg,
     '冷库 A-02', daysFromNow(6), 'sealed', 1],
  ];

  const ids = {};
  for (const r of rows) {
    const photo = placeholderPhoto(photoDir, r[0], r[1]);
    const info = ins.run(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], photo,
      daysAgo(1), r[9], r[10], r[11], t);
    ids[r[0]] = info.lastInsertRowid;
  }

  const logEvent = (sampleId, type, detail, actor) =>
    db.prepare(`INSERT INTO events (sample_id, type, detail, actor, created_at) VALUES (?,?,?,?,?)`)
      .run(sampleId, type, detail, actor, t);

  logEvent(ids['SP-2026-001'], 'sample_received', '番茄 / 批次 B20260928-A / 到货 4.1℃ / 封存 FC-260001', 'buyer');
  logEvent(ids['SP-2026-002'], 'sample_received', '土豆 / 批次 B20260928-B / 到货 8.5℃ / 封存 FC-260002', 'buyer');
  logEvent(ids['SP-2026-002'], 'seal_broken', '收样时发现封签破损，样品不予进入盲评', 'buyer');
  logEvent(ids['SP-2026-003'], 'sample_received', '冷鲜猪前腿肉 / 批次 B20260929-C / 到货 6.4℃', 'buyer');
  logEvent(ids['SP-2026-003'], 'arrival_overtemp', '到货温度 6.4℃ 超过上限 4℃', 'buyer');
  logEvent(ids['SP-2026-004'], 'sample_received', '鲜鸡蛋 / 批次 B20260929-D', 'buyer');
  logEvent(ids['SP-2026-005'], 'sample_received', '生菜 / 批次 B20260920-E（收样时已过期）', 'buyer');
  logEvent(ids['SP-2026-005'], 'sample_expired', '收样时已过保质期', 'buyer');
  logEvent(ids['SP-2026-006'], 'sample_received', '番茄 / 批次 B20260930-F', 'buyer');

  // 评审乙对 004 提出回避（已批准）
  db.prepare(`INSERT INTO recusals (sample_id, reviewer_id, reason, status, created_at)
              VALUES (?,?,?,?,?)`)
    .run(ids['SP-2026-004'], users.r2, '本人近亲属为晨曦禽蛋配送员，申请回避', 'approved', t);
  logEvent(ids['SP-2026-004'], 'recusal_requested', '本人近亲属为晨曦禽蛋配送员，申请回避', 'r2');
  logEvent(ids['SP-2026-004'], 'recusal_approved', '管理员批准回避', 'admin');

  // 预置一份已揭盲并完成选用意见的样例（结果复核页有内容可看）
  const oldId = ins.run('SP-2025-118', '白萝卜', '江苏徐州', 6.0, 9.0, 'B20250918-Z',
    'FC-259018', sup.green, '冷库 A-09', null, daysAgo(20), daysAgo(5), 'revealed', 0, t).lastInsertRowid;
  db.prepare(`INSERT INTO scores (sample_id, reviewer_id, taste, spec, cooking_loss, remark, submitted_at)
              VALUES (?,?,?,?,?,?,?)`).run(oldId, users.r1, 8, 8, 12.5, '清甜，规格齐整', t);
  db.prepare(`INSERT INTO scores (sample_id, reviewer_id, taste, spec, cooking_loss, remark, submitted_at)
              VALUES (?,?,?,?,?,?,?)`).run(oldId, users.r3, 7, 8, 14.0, '损耗略高，可接受', t);
  const avg = (a, b) => ((a + b) / 2).toFixed(2);
  const result = {
    code: 'SP-2025-118', supplier_id: sup.green,
    average_taste: Number(avg(8, 7)), average_spec: 8, average_cooking_loss: Number(avg(12.5, 14)),
    opinions: [
      { reviewer: '评审甲·王师傅', taste: 8, spec: 8, cooking_loss: 12.5, remark: '清甜，规格齐整' },
      { reviewer: '评审丙·张厨师长', taste: 7, spec: 8, cooking_loss: 14, remark: '损耗略高，可接受' },
    ],
    revealed_at: t,
  };
  db.prepare(`INSERT INTO revelations (sample_id, result_json, actor_a, actor_b, created_at)
              VALUES (?,?,?,?,?)`).run(oldId, JSON.stringify(result), users.admin, users.buyer, t);
  db.prepare(`INSERT INTO result_reviews (sample_id, reviewer_id, agrees, opinion, created_at)
              VALUES (?,?,?,?,?)`).run(oldId, users.r1, 1, '综合表现稳定，同意选用', t);
  logEvent(oldId, 'sample_revealed', '两人同时揭盲', 'admin+buyer');
  logEvent(oldId, 'result_reviewed', '同意选用：综合表现稳定，同意选用', 'r1');

  console.log('[seed] 已写入演示数据：含 1 份封签破损样例 SP-2026-002');
  notifier.send('admin', 'seed_done', { broken_sample: 'SP-2026-002' });
  return true;
}
