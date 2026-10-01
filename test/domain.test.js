import test from 'node:test';
import assert from 'node:assert/strict';
import * as D from '../src/domain.js';
import { makeEnv, makeSealedSample } from './helpers.js';

test('匿名隔离：评审人揭盲前看不到、也不能查供应商身份，揭盲后才可见', () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const sample = db.prepare("SELECT id FROM samples WHERE code='SP-2026-001'").get();

    // 评审人列表视图抹掉供应商
    const blindList = D.listSamples(db, users.r1, notifier);
    const row = blindList.find((s) => s.id === sample.id);
    assert.equal(row.supplier_name, null, '揭盲前列表不得出现供应商');

    // 评审人主动探测身份 → 拒绝 + 审计
    assert.throws(() => D.probeIdentity(db, sample.id, users.r1, notifier),
      (e) => e.code === 'identity_sealed' && e.status === 403);
    const denied = db.prepare(
      "SELECT COUNT(*) n FROM audit_log WHERE action='probe_supplier_identity' AND allowed=0").get().n;
    assert.ok(denied >= 1, '身份探测被拒必须写审计');

    // 采购员/管理员可以看到（采购环节需要）
    assert.ok(D.listSamples(db, users.buyer, notifier).find((s) => s.id === sample.id).supplier_name);
    assert.ok(D.getSample(db, sample.id, users.admin, notifier).supplier_name);

    // 揭盲后评审人可见供应商
    D.submitScore(db, sample.id, users.r1, { taste: 8, spec: 8, cooking_loss: 12 });
    D.reveal(db, notifier, sample.id, users.admin, users.buyer);
    assert.equal(
      D.getSample(db, sample.id, users.r1, notifier).supplier_name, '绿源农场合作社',
      '揭盲后应公开供应商');
  } finally { cleanup(); }
});

test('采购员不能替评审人填分', () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const { id } = makeSealedSample(db);
    assert.throws(() => D.submitScore(db, id, users.buyer,
      { taste: 9, spec: 9, cooking_loss: 5 }), (e) => e.code === 'not_reviewer');
    const audits = db.prepare(
      "SELECT * FROM audit_log WHERE action='submit_score' AND allowed=0 AND detail LIKE '%非评审人%'").all();
    assert.ok(audits.length >= 1, '采购员填分必须留痕');
    assert.equal(db.prepare('SELECT COUNT(*) n FROM scores WHERE sample_id=?').get(id).n, 0, '不得写入评分');
  } finally { cleanup(); }
});

test('评分完整性：漏项留痕并拒绝，补齐后可提交，提交后不可改', () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const { id } = makeSealedSample(db);
    // 漏口感
    assert.throws(() => D.submitScore(db, id, users.r1, { spec: 7, cooking_loss: 10 }),
      (e) => e.code === 'score_incomplete');
    // 越界
    assert.throws(() => D.submitScore(db, id, users.r1, { taste: 0, spec: 7, cooking_loss: 10 }),
      (e) => e.code === 'bad_range');
    assert.throws(() => D.submitScore(db, id, users.r1, { taste: 10, spec: 7, cooking_loss: 100 }),
      (e) => e.code === 'bad_range');
    const missEvents = db.prepare("SELECT * FROM events WHERE type='score_missing_items'").all();
    assert.ok(missEvents.length >= 1, '漏项必须写事件留痕');
    assert.equal(db.prepare('SELECT COUNT(*) n FROM scores WHERE sample_id=?').get(id).n, 0);

    // 补齐提交
    D.submitScore(db, id, users.r1, { taste: 8, spec: 7, cooking_loss: 12.5, remark: 'ok' });
    assert.throws(() => D.submitScore(db, id, users.r1, { taste: 5, spec: 5, cooking_loss: 20 }),
      (e) => e.code === 'score_exists', '一人一样品只能评一次');
  } finally { cleanup(); }
});

test('回避：批准后评分被拦截并留痕，未批准不影响', () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const a = makeSealedSample(db);
    // 仅有申请、未批准 → 可以评分
    D.requestRecusal(db, notifier, a.id, users.r1, '潜在利益关系');
    D.submitScore(db, a.id, users.r1, { taste: 6, spec: 6, cooking_loss: 9 });

    const b = makeSealedSample(db);
    D.requestRecusal(db, notifier, b.id, users.r2, '近亲属在供应商处任职');
    const recusalId = db.prepare("SELECT id FROM recusals WHERE sample_id=? AND reviewer_id=?")
      .get(b.id, users.r2.id).id;
    D.decideRecusal(db, notifier, recusalId, true, users.admin);
    assert.throws(() => D.submitScore(db, b.id, users.r2, { taste: 8, spec: 8, cooking_loss: 8 }),
      (e) => e.code === 'recused');
    assert.ok(db.prepare(
      "SELECT COUNT(*) n FROM events WHERE type='score_blocked_recusal'").get().n >= 1,
      '已回避评分要单独留痕');
    assert.equal(db.prepare('SELECT COUNT(*) n FROM scores WHERE sample_id=?').get(b.id).n, 0);
  } finally { cleanup(); }
});

test('揭盲竞态：多事务并发只产生一次结果，重复揭盲返回同一条', async () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const { id } = makeSealedSample(db);
    D.submitScore(db, id, users.r1, { taste: 7, spec: 8, cooking_loss: 11 });
    D.submitScore(db, id, users.r2, { taste: 9, spec: 7, cooking_loss: 9.5 });

    // 20 个揭盲事务并发冲击同一样品
    const results = await Promise.all(
      Array.from({ length: 20 }, () => Promise.resolve().then(() => {
        try { return D.reveal(db, notifier, id, users.admin, users.buyer); }
        catch (e) { return { error: e.code || e.message }; }
      })));
    const errors = results.filter((r) => r.error);
    assert.equal(errors.length, 0, '竞态不应产生数据库错误（只串行化）');
    assert.equal(db.prepare('SELECT COUNT(*) n FROM revelations WHERE sample_id=?').get(id).n, 1);
    assert.equal(db.prepare("SELECT COUNT(*) n FROM notifications WHERE type='sample_revealed'").get().n, 1,
      '只发一次通知');
    const winners = results.filter((r) => r && !r.alreadyRevealed);
    assert.equal(winners.length, 1, '只有一次真正写入');
    assert.ok(results.every((r) => r && r.revelation), '其余请求拿到同一条结果');
    assert.equal(db.prepare('SELECT status FROM samples WHERE id=?').get(id).status, 'revealed');
  } finally { cleanup(); }
});

test('收样规则：封签破损/超温/过期分别标记并留痕；一个封存编号仅一份当前样品', () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const future = new Date(Date.now() + 5 * 86400000).toISOString();
    const mk = (over) => D.receiveSample(db, notifier, over, users.buyer);

    const broken = mk({ code: 'X-1', category: '姜', origin: 'A', arrival_temp: 5, temp_limit: 7,
      batch_no: 'b1', seal_no: 'SEAL-X1', supplier_id: 1, expires_at: future, seal_broken: '1' });
    assert.equal(broken.status, 'broken');
    assert.equal(broken.is_current, 0);

    const hot = mk({ code: 'X-2', category: '肉', origin: 'B', arrival_temp: 9, temp_limit: 4,
      batch_no: 'b2', seal_no: 'SEAL-X2', supplier_id: 1, expires_at: future });
    assert.equal(hot.status, 'overtemp');

    const past = new Date(Date.now() - 86400000).toISOString();
    const expired = mk({ code: 'X-3', category: '菜', origin: 'C', arrival_temp: 3, temp_limit: 7,
      batch_no: 'b3', seal_no: 'SEAL-X3', supplier_id: 1, expires_at: past });
    assert.equal(expired.status, 'expired');

    for (const [code, type] of [['X-1', 'seal_broken'], ['X-2', 'arrival_overtemp'], ['X-3', 'sample_expired']]) {
      const sid = db.prepare('SELECT id FROM samples WHERE code=?').get(code).id;
      assert.ok(db.prepare('SELECT COUNT(*) n FROM events WHERE sample_id=? AND type=?').get(sid, type).n >= 1,
        `${code} 应有 ${type} 留痕`);
    }

    // 封存中/超温样品仍占封存编号：同号第二份当前样品 → 拒绝
    assert.throws(() => mk({ code: 'X-4', category: '蒜', origin: 'D', arrival_temp: 4, temp_limit: 7,
      batch_no: 'b4', seal_no: 'SEAL-X2', supplier_id: 1, expires_at: future }),
      (e) => e.code === 'dup_seal');

    // 封存编号被破损样释放后（is_current=0）可重新使用
    const reuse = mk({ code: 'X-5', category: '蒜', origin: 'D', arrival_temp: 4, temp_limit: 7,
      batch_no: 'b5', seal_no: 'SEAL-X1', supplier_id: 1, expires_at: future });
    assert.equal(reuse.status, 'sealed');
  } finally { cleanup(); }
});

test('撤样与破损上报：状态迁移、留痕、通知', () => {
  const { db, notifier, users, cleanup } = makeEnv();
  try {
    const { id } = makeSealedSample(db);
    D.withdrawSample(db, notifier, id, users.buyer, '供应商撤样');
    assert.equal(db.prepare('SELECT status,is_current FROM samples WHERE id=?').get(id).status, 'withdrawn');
    assert.throws(() => D.submitScore(db, id, users.r1, { taste: 1, spec: 1, cooking_loss: 1 }),
      (e) => e.code === 'sample_not_reviewable');
    assert.throws(() => D.reveal(db, notifier, id, users.admin, users.buyer), (e) => e.code === 'not_sealed');
  } finally { cleanup(); }
});
