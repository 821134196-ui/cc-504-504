import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openDb } from '../src/db.js';
import { seed } from '../src/seed.js';
import { createNotifier } from '../src/domain.js';

export function makeEnv() {
  const dataDir = mkdtempSync(join(tmpdir(), 'bsr-test-'));
  const photoDir = join(dataDir, 'photos');
  const db = openDb(join(dataDir, 'app.db'));
  seed(db, photoDir);
  const notifier = createNotifier(db);
  const user = (username) => db.prepare('SELECT * FROM users WHERE username=?').get(username);
  return {
    db, notifier, dataDir, photoDir,
    users: {
      admin: user('admin'), buyer: user('buyer'),
      r1: user('r1'), r2: user('r2'), r3: user('r3'),
    },
    cleanup() { db.close(); rmSync(dataDir, { recursive: true, force: true }); },
  };
}

let seq = 0;
// 直接构造一份可评分的封存样品（绕过收样表单）
export function makeSealedSample(db, { sealNo, supplierId } = {}) {
  const t = new Date().toISOString();
  const code = `UT-${process.pid}-${++seq}`;
  const seal = sealNo || `FC-UT-${process.pid}-${seq}`;
  const sup = supplierId || db.prepare('SELECT id FROM suppliers LIMIT 1').get().id;
  const info = db.prepare(`INSERT INTO samples
    (code, category, origin, arrival_temp, temp_limit, batch_no, seal_no, supplier_id,
     received_at, expires_at, status, is_current, created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    code, '测试品类', '测试产地', 4, 7, `B-UT-${seq}`, seal, sup,
    t, new Date(Date.now() + 7 * 86400000).toISOString(), 'sealed', 1, t);
  return { id: info.lastInsertRowid, code, seal };
}
