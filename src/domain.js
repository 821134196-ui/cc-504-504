// 模拟通知适配器：接口与真实渠道（短信/钉钉/邮件）一致，这里落库 + 控制台输出
export function createNotifier(db) {
  return {
    async send(recipient, type, payload) {
      const info = db.prepare(`INSERT INTO notifications (ts, recipient, type, payload, delivered)
                               VALUES (?,?,?,?,1)`).run(
        new Date().toISOString(), recipient, type, JSON.stringify(payload));
      console.log(`[通知:模拟] -> ${recipient} [${type}] ${typeof payload === 'string' ? payload : JSON.stringify(payload)}`);
      return info.lastInsertRowid;
    },
  };
}

export class DomainError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const now = () => new Date().toISOString();

export function logAudit(db, { actor, action, targetType = null, targetId = null, allowed, detail = null }) {
  db.prepare(`INSERT INTO audit_log (ts, actor, action, target_type, target_id, allowed, detail)
              VALUES (?,?,?,?,?,?,?)`)
    .run(now(), actor, action, targetType, targetId, allowed ? 1 : 0, detail);
}

function logEvent(db, { sampleId = null, type, detail = null, actor = null }) {
  db.prepare(`INSERT INTO events (sample_id, type, detail, actor, created_at)
              VALUES (?,?,?,?,?)`).run(sampleId, type, detail, actor, now());
}

// 过期巡检：把已过保质期的封存样品置为 expired（幂等）
function sweepExpiry(db, notifier) {
  const rows = db.prepare(
    `SELECT id, code FROM samples WHERE is_current = 1 AND status IN ('sealed','overtemp') AND expires_at < ?`)
    .all(now());
  for (const r of rows) {
    db.prepare(`UPDATE samples SET status='expired', is_current=0 WHERE id=?`).run(r.id);
    logEvent(db, { sampleId: r.id, type: 'sample_expired', detail: '超过样品保质期', actor: 'system' });
    notifier.send('采购员', 'sample_expired', { code: r.code });
  }
}

const REVIEWABLE = new Set(['sealed']);

// 列表/查询：评审人在揭盲前看不到供应商身份
export function listSamples(db, viewer, notifier) {
  sweepExpiry(db, notifier);
  const rows = db.prepare(
    `SELECT s.*, sup.name AS supplier_name FROM samples s
     JOIN suppliers sup ON sup.id = s.supplier_id ORDER BY s.id DESC`).all();
  const blind = viewer.role === 'reviewer';
  for (const r of rows) {
    if (blind && r.status !== 'revealed') r.supplier_name = null;
  }
  return rows;
}

export function getSample(db, id, viewer, notifier) {
  sweepExpiry(db, notifier);
  const row = db.prepare(
    `SELECT s.*, sup.name AS supplier_name, sup.contact AS supplier_contact
     FROM samples s JOIN suppliers sup ON sup.id = s.supplier_id WHERE s.id=?`).get(id);
  if (!row) throw new DomainError(404, 'not_found', '样品不存在');
  if (viewer.role === 'reviewer' && row.status !== 'revealed') {
    row.supplier_name = null;
    row.supplier_contact = null;
  }
  return row;
}

// 评审人主动查询供应商身份：揭盲前一律拒绝并审计
export function probeIdentity(db, id, viewer, notifier) {
  const sample = getSample(db, id, viewer, notifier);
  if (viewer.role === 'reviewer' && sample.status !== 'revealed') {
    logAudit(db, {
      actor: viewer.username, action: 'probe_supplier_identity',
      targetType: 'sample', targetId: sample.code, allowed: false,
      detail: '揭盲前评审人查询供应商身份被拒',
    });
    throw new DomainError(403, 'identity_sealed', '揭盲前禁止查询供应商身份，本次访问已留痕');
  }
  logAudit(db, {
    actor: viewer.username, action: 'probe_supplier_identity',
    targetType: 'sample', targetId: sample.code, allowed: true,
  });
  return { code: sample.code, supplier: sample.supplier_name, contact: sample.supplier_contact };
}

export function receiveSample(db, notifier, data, actor) {
  const required = ['code', 'category', 'origin', 'arrival_temp', 'temp_limit', 'batch_no', 'seal_no', 'supplier_id', 'expires_at'];
  for (const k of required) {
    if (data[k] === undefined || data[k] === null || String(data[k]).trim() === '') {
      throw new DomainError(400, 'missing_field', `收样信息缺项：${k}`);
    }
  }
  const arrival = Number(data.arrival_temp);
  const limit = Number(data.temp_limit);
  if (Number.isNaN(arrival) || Number.isNaN(limit)) {
    throw new DomainError(400, 'bad_temp', '到货温度必须为数字');
  }
  const supplier = db.prepare('SELECT id FROM suppliers WHERE id=?').get(Number(data.supplier_id));
  if (!supplier) throw new DomainError(400, 'bad_supplier', '供应商不存在');
  if (Number.isNaN(Date.parse(data.expires_at))) throw new DomainError(400, 'bad_expiry', '保质期格式不正确');

  const sealBroken = data.seal_broken === '1' || data.seal_broken === 'true';
  const overtemp = arrival > limit;
  const expired = new Date(data.expires_at).getTime() <= Date.now();
  const status = sealBroken ? 'broken' : expired ? 'expired' : overtemp ? 'overtemp' : 'sealed';

  let info;
  try {
    info = db.prepare(`INSERT INTO samples
      (code, category, origin, arrival_temp, temp_limit, batch_no, seal_no, supplier_id,
       storage_location, received_at, expires_at, status, is_current, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?, ?, ?, ?)`).run(
      data.code.trim(), data.category.trim(), data.origin.trim(), arrival, limit,
      data.batch_no.trim(), data.seal_no.trim(), supplier.id,
      data.storage_location?.trim() || null, now(), new Date(data.expires_at).toISOString(),
      status, status === 'sealed' || status === 'overtemp' ? 1 : 0, now());
  } catch (e) {
    if (String(e.message).includes('UNIQUE')) {
      if (String(e.message).includes('samples.code')) throw new DomainError(409, 'dup_code', '样品编号已存在');
      throw new DomainError(409, 'dup_seal', '一个封存编号只能关联一份当前样品：该封存编号已被占用');
    }
    throw e;
  }
  const id = info.lastInsertRowid;
  logEvent(db, { sampleId: id, type: 'sample_received', actor: actor.username,
    detail: `${data.category} / 批次 ${data.batch_no} / 到货 ${arrival}℃ / 封存 ${data.seal_no}` });
  logAudit(db, { actor: actor.username, action: 'receive_sample', targetType: 'sample', targetId: data.code, allowed: true });

  if (sealBroken) {
    logEvent(db, { sampleId: id, type: 'seal_broken', actor: actor.username, detail: '收样时发现封签破损，样品不予进入盲评' });
    notifier.send('评审组', 'seal_broken', { code: data.code, seal_no: data.seal_no });
  } else if (overtemp) {
    logEvent(db, { sampleId: id, type: 'arrival_overtemp', actor: actor.username,
      detail: `到货温度 ${arrival}℃ 超过上限 ${limit}℃` });
    notifier.send('评审组', 'arrival_overtemp', { code: data.code, arrival_temp: arrival, temp_limit: limit });
  }
  if (expired) {
    logEvent(db, { sampleId: id, type: 'sample_expired', actor: actor.username, detail: '收样时已过保质期' });
  }
  return getSample(db, id, { role: 'admin' }, notifier);
}

export function reportBrokenSeal(db, notifier, id, actor, note) {
  const s = mustExist(db, id);
  if (s.status === 'revealed') throw new DomainError(409, 'already_revealed', '样品已揭盲');
  db.prepare(`UPDATE samples SET status='broken', is_current=0 WHERE id=?`).run(id);
  logEvent(db, { sampleId: id, type: 'seal_broken', actor: actor.username, detail: note || '巡检发现封签破损' });
  logAudit(db, { actor: actor.username, action: 'report_seal_broken', targetType: 'sample', targetId: s.code, allowed: true });
  notifier.send('评审组', 'seal_broken', { code: s.code });
}

export function withdrawSample(db, notifier, id, actor, reason) {
  const s = mustExist(db, id);
  if (s.status === 'revealed') throw new DomainError(409, 'already_revealed', '样品已揭盲，不能撤样');
  if (s.status === 'withdrawn') throw new DomainError(409, 'already_withdrawn', '样品已撤样');
  db.prepare(`UPDATE samples SET status='withdrawn', is_current=0, withdraw_reason=? WHERE id=?`)
    .run(reason || '供应商撤样', id);
  logEvent(db, { sampleId: id, type: 'sample_withdrawn', actor: actor.username, detail: reason || '供应商撤样' });
  logAudit(db, { actor: actor.username, action: 'withdraw_sample', targetType: 'sample', targetId: s.code, allowed: true,
    detail: reason || '供应商撤样' });
  notifier.send('评审组', 'sample_withdrawn', { code: s.code, reason: reason || '供应商撤样' });
}

export function setStorage(db, id, actor, location) {
  const s = mustExist(db, id);
  db.prepare('UPDATE samples SET storage_location=? WHERE id=?').run(location.trim() || null, id);
  logEvent(db, { sampleId: id, type: 'storage_assigned', actor: actor.username, detail: location });
  return s;
}

export function setPhoto(db, id, actor, relPath) {
  const s = mustExist(db, id);
  db.prepare('UPDATE samples SET photo_path=? WHERE id=?').run(relPath, id);
  logEvent(db, { sampleId: id, type: 'photo_uploaded', actor: actor.username, detail: relPath });
  return s;
}

// ---------- 回避 ----------
export function requestRecusal(db, notifier, id, reviewer, reason) {
  const s = mustExist(db, id);
  if (!reason || !reason.trim()) throw new DomainError(400, 'missing_reason', '回避申请必须填写理由');
  try {
    db.prepare(`INSERT INTO recusals (sample_id, reviewer_id, reason, status, created_at)
                VALUES (?,?,?,?,?)`).run(id, reviewer.id, reason.trim(), 'requested', now());
  } catch (e) {
    if (String(e.message).includes('UNIQUE')) throw new DomainError(409, 'recusal_exists', '已提交过回避申请');
    throw e;
  }
  logEvent(db, { sampleId: id, type: 'recusal_requested', actor: reviewer.username, detail: reason });
  logAudit(db, { actor: reviewer.username, action: 'request_recusal', targetType: 'sample', targetId: s.code, allowed: true });
  notifier.send('管理员', 'recusal_requested', { code: s.code, reviewer: reviewer.display_name, reason });
}

export function decideRecusal(db, notifier, recusalId, approve, actor) {
  const r = db.prepare(`SELECT rc.*, s.code FROM recusals rc JOIN samples s ON s.id=rc.sample_id WHERE rc.id=?`).get(recusalId);
  if (!r) throw new DomainError(404, 'not_found', '回避申请不存在');
  if (r.status !== 'requested') throw new DomainError(409, 'decided', '该申请已审批');
  db.prepare(`UPDATE recusals SET status=? WHERE id=?`).run(approve ? 'approved' : 'revoked', recusalId);
  logEvent(db, { sampleId: r.sample_id, type: approve ? 'recusal_approved' : 'recusal_rejected',
    actor: actor.username, detail: `评审人#${r.reviewer_id}` });
  const reviewer = db.prepare('SELECT username FROM users WHERE id=?').get(r.reviewer_id);
  notifier.send(reviewer.username, 'recusal_decided', { code: r.code, approved: !!approve });
}

function activeRecusal(db, sampleId, reviewerId) {
  return db.prepare(`SELECT id FROM recusals WHERE sample_id=? AND reviewer_id=? AND status='approved'`)
    .get(sampleId, reviewerId);
}

// ---------- 盲评评分 ----------
export function submitScore(db, sampleId, reviewer, body) {
  // 采购员（及任何非评审人）不能替评审人填分
  if (reviewer.role !== 'reviewer') {
    logAudit(db, { actor: reviewer.username, action: 'submit_score',
      targetType: 'sample', targetId: String(sampleId), allowed: false,
      detail: '非评审人试图填分' });
    throw new DomainError(403, 'not_reviewer', '只有评审人本人可以填写盲评表，采购员不得代填');
  }
  sweepExpirySilent(db);
  const s = db.prepare('SELECT * FROM samples WHERE id=?').get(sampleId);
  if (!s) throw new DomainError(404, 'not_found', '样品不存在');
  if (!REVIEWABLE.has(s.status)) {
    logAudit(db, { actor: reviewer.username, action: 'submit_score', targetType: 'sample', targetId: s.code,
      allowed: false, detail: `样品状态 ${s.status} 不可评分` });
    throw new DomainError(409, 'sample_not_reviewable', `样品当前状态（${s.status}）不接受评分`);
  }
  if (activeRecusal(db, sampleId, reviewer.id)) {
    logAudit(db, { actor: reviewer.username, action: 'submit_score', targetType: 'sample', targetId: s.code,
      allowed: false, detail: '该评审人已获准回避' });
    logEvent(db, { sampleId, type: 'score_blocked_recusal', actor: reviewer.username,
      detail: '已回避评审人试图评分被拦截' });
    throw new DomainError(403, 'recused', '您已获准回避该样品，不能评分');
  }

  // 评分漏项：逐项检查，缺失/越界一律留痕并拒绝
  const missing = [];
  const tasteRaw = body.taste === undefined || body.taste === '' ? null : Number(body.taste);
  const specRaw = body.spec === undefined || body.spec === '' ? null : Number(body.spec);
  const lossRaw = body.cooking_loss === undefined || body.cooking_loss === '' ? null : Number(body.cooking_loss);
  if (tasteRaw === null) missing.push('口感');
  if (specRaw === null) missing.push('规格');
  if (lossRaw === null) missing.push('烹调损耗');
  if (missing.length) {
    logEvent(db, { sampleId, type: 'score_missing_items', actor: reviewer.username,
      detail: `漏项：${missing.join('、')}` });
    logAudit(db, { actor: reviewer.username, action: 'submit_score', targetType: 'sample', targetId: s.code,
      allowed: false, detail: `评分漏项：${missing.join('、')}` });
    throw new DomainError(400, 'score_incomplete', `评分存在漏项（${missing.join('、')}），已留痕，请补齐后提交`);
  }
  if (!Number.isInteger(tasteRaw) || tasteRaw < 1 || tasteRaw > 10) {
    throw new DomainError(400, 'bad_range', '口感评分须为 1-10 的整数');
  }
  if (!Number.isInteger(specRaw) || specRaw < 1 || specRaw > 10) {
    throw new DomainError(400, 'bad_range', '规格评分须为 1-10 的整数');
  }
  if (Number.isNaN(lossRaw) || lossRaw < 0 || lossRaw >= 100) {
    throw new DomainError(400, 'bad_range', '烹调损耗须为 0-100 之间的百分数');
  }
  try {
    db.prepare(`INSERT INTO scores (sample_id, reviewer_id, taste, spec, cooking_loss, remark, submitted_at)
                VALUES (?,?,?,?,?,?,?)`)
      .run(sampleId, reviewer.id, tasteRaw, specRaw, lossRaw, body.remark?.trim() || null, now());
  } catch (e) {
    if (String(e).includes('UNIQUE')) throw new DomainError(409, 'score_exists', '该样品您已评分，评分不可更改');
    throw e;
  }
  logEvent(db, { sampleId, type: 'score_submitted', actor: reviewer.username,
    detail: `口感 ${tasteRaw} / 规格 ${specRaw} / 损耗 ${lossRaw}%` });
  logAudit(db, { actor: reviewer.username, action: 'submit_score', targetType: 'sample', targetId: s.code, allowed: true });
}

function sweepExpirySilent(db) {
  const rows = db.prepare(
    `SELECT id FROM samples WHERE is_current=1 AND status IN ('sealed','overtemp') AND expires_at < ?`).all(now());
  for (const r of rows) db.prepare(`UPDATE samples SET status='expired', is_current=0 WHERE id=?`).run(r.id);
}

// ---------- 揭盲（两人同时，竞态安全） ----------
export function reveal(db, notifier, sampleId, actorA, actorB) {
  if (!actorA || !actorB) throw new DomainError(400, 'two_persons', '揭盲需要两名授权人员');
  if (actorA.id === actorB.id) throw new DomainError(400, 'same_person', '两名揭盲人不能为同一人');
  const s = mustExist(db, sampleId);
  if (s.status !== 'sealed') {
    const existing = db.prepare('SELECT * FROM revelations WHERE sample_id=?').get(sampleId);
    if (existing) return { revelation: existing, alreadyRevealed: true };
    throw new DomainError(409, 'not_sealed', `样品状态为 ${s.status}，不能揭盲`);
  }

  // IMMEDIATE 立即取写锁：并发请求在数据库层串行化，唯一约束兜底
  db.exec('BEGIN IMMEDIATE');
  try {
    const existing = db.prepare('SELECT * FROM revelations WHERE sample_id=?').get(sampleId);
    if (existing) {
      db.exec('COMMIT');
      return { revelation: existing, alreadyRevealed: true };
    }
    const scores = db.prepare(
      `SELECT sc.*, u.display_name AS reviewer_name FROM scores sc
       JOIN users u ON u.id=sc.reviewer_id WHERE sc.sample_id=? ORDER BY u.id`).all(sampleId);
    const avg = (xs) => xs.length ? Number((xs.reduce((a, b) => a + b, 0) / xs.length).toFixed(2)) : null;
    const result = {
      code: s.code,
      supplier_id: s.supplier_id,
      average_taste: avg(scores.map((x) => x.taste)),
      average_spec: avg(scores.map((x) => x.spec)),
      average_cooking_loss: avg(scores.map((x) => x.cooking_loss)),
      opinions: scores.map((x) => ({ reviewer: x.reviewer_name, taste: x.taste, spec: x.spec,
        cooking_loss: x.cooking_loss, remark: x.remark })),
      revealed_at: now(),
    };
    const info = db.prepare(`INSERT INTO revelations (sample_id, result_json, actor_a, actor_b, created_at)
                             VALUES (?,?,?,?,?)`)
      .run(sampleId, JSON.stringify(result), actorA.id, actorB.id, now());
    db.prepare(`UPDATE samples SET status='revealed', is_current=0 WHERE id=?`).run(sampleId);
    logEvent(db, { sampleId, type: 'sample_revealed', actor: `${actorA.username}+${actorB.username}`,
      detail: '两人同时揭盲' });
    logAudit(db, { actor: actorA.username, action: 'reveal', targetType: 'sample', targetId: s.code, allowed: true,
      detail: `共同揭盲人 ${actorB.username}` });
    db.exec('COMMIT');
    notifier.send('评审组', 'sample_revealed', { code: s.code });
    const revelation = db.prepare('SELECT * FROM revelations WHERE id=?').get(info.lastInsertRowid);
    return { revelation, alreadyRevealed: false };
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

// ---------- 结果复核 ----------
export function submitResultReview(db, notifier, sampleId, reviewer, agrees, opinion) {
  const s = mustExist(db, sampleId);
  if (s.status !== 'revealed') throw new DomainError(409, 'not_revealed', '样品尚未揭盲');
  if (reviewer.role !== 'reviewer') throw new DomainError(403, 'not_reviewer', '仅评审人可复核');
  if (!opinion || !opinion.trim()) throw new DomainError(400, 'missing_opinion', '请填写复核意见');
  try {
    db.prepare(`INSERT INTO result_reviews (sample_id, reviewer_id, agrees, opinion, created_at)
                VALUES (?,?,?,?,?)`).run(sampleId, reviewer.id, agrees ? 1 : 0, opinion.trim(), now());
  } catch (e) {
    if (String(e).includes('UNIQUE')) throw new DomainError(409, 'review_exists', '您已复核过该样品');
    throw e;
  }
  logEvent(db, { sampleId, type: 'result_reviewed', actor: reviewer.username,
    detail: `${agrees ? '同意选用' : '不同意选用'}：${opinion.trim()}` });
  notifier.send('管理员', 'result_reviewed', { code: s.code, reviewer: reviewer.display_name, agrees: !!agrees });
}

export function getResult(db, id) {
  const s = db.prepare(
    `SELECT s.*, sup.name AS supplier_name FROM samples s JOIN suppliers sup ON sup.id=s.supplier_id WHERE s.id=?`).get(id);
  if (!s) throw new DomainError(404, 'not_found', '样品不存在');
  const rev = db.prepare('SELECT * FROM revelations WHERE sample_id=?').get(id);
  if (!rev) return null;
  const reviews = db.prepare(
    `SELECT rr.*, u.display_name AS reviewer_name FROM result_reviews rr
     JOIN users u ON u.id=rr.reviewer_id WHERE rr.sample_id=? ORDER BY rr.id`).all(id);
  return { sample: s, revelation: rev, result: JSON.parse(rev.result_json), reviews };
}

function mustExist(db, id) {
  const s = db.prepare('SELECT * FROM samples WHERE id=?').get(id);
  if (!s) throw new DomainError(404, 'not_found', '样品不存在');
  return s;
}
