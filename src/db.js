import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export const SCHEMA = `
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pass TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','buyer','reviewer'))
);

CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  contact TEXT,
  created_at TEXT NOT NULL
);

-- 样品（盲样）：评审阶段只暴露 code / seal_no，不暴露 supplier_id
CREATE TABLE IF NOT EXISTS samples (
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,              -- 样品编号
  category TEXT NOT NULL,                 -- 品类
  origin TEXT NOT NULL,                   -- 产地
  arrival_temp REAL NOT NULL,             -- 到货温度 ℃
  temp_limit REAL NOT NULL,               -- 运输温度上限 ℃
  batch_no TEXT NOT NULL,                 -- 批次
  seal_no TEXT NOT NULL,                  -- 封存编号
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  storage_location TEXT,                  -- 封样库位
  photo_path TEXT,                        -- 本地照片路径
  received_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,               -- 样品保质期
  status TEXT NOT NULL CHECK (status IN
    ('sealed','overtemp','broken','expired','withdrawn','revealed')),
  is_current INTEGER NOT NULL DEFAULT 1,  -- 1=当前样品；破损/过期/撤样/揭盲后置 0
  withdraw_reason TEXT,
  created_at TEXT NOT NULL
);
-- 一个封存编号只能关联一份当前样品
CREATE UNIQUE INDEX IF NOT EXISTS idx_seal_current
  ON samples(seal_no) WHERE is_current = 1;

-- 评审人回避申请
CREATE TABLE IF NOT EXISTS recusals (
  id INTEGER PRIMARY KEY,
  sample_id INTEGER NOT NULL REFERENCES samples(id),
  reviewer_id INTEGER NOT NULL REFERENCES users(id),
  reason TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('requested','approved','revoked')),
  created_at TEXT NOT NULL,
  UNIQUE(sample_id, reviewer_id)
);

-- 评分：taste/spec 为感官项（1-10 整数），cooking_loss 为实测损耗（百分比）
CREATE TABLE IF NOT EXISTS scores (
  id INTEGER PRIMARY KEY,
  sample_id INTEGER NOT NULL REFERENCES samples(id),
  reviewer_id INTEGER NOT NULL REFERENCES users(id),
  taste INTEGER CHECK (taste IS NULL OR taste BETWEEN 1 AND 10),
  spec INTEGER CHECK (spec IS NULL OR spec BETWEEN 1 AND 10),
  cooking_loss REAL CHECK (cooking_loss IS NULL OR (cooking_loss >= 0 AND cooking_loss < 100)),
  remark TEXT,
  submitted_at TEXT NOT NULL,
  UNIQUE(sample_id, reviewer_id)
);

-- 揭盲结果：sample_id 唯一 + 揭盲事务保证两人同时揭盲只产生一次结果
CREATE TABLE IF NOT EXISTS revelations (
  id INTEGER PRIMARY KEY,
  sample_id INTEGER NOT NULL UNIQUE REFERENCES samples(id),
  result_json TEXT NOT NULL,
  actor_a INTEGER NOT NULL REFERENCES users(id),
  actor_b INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

-- 结果复核
CREATE TABLE IF NOT EXISTS result_reviews (
  id INTEGER PRIMARY KEY,
  sample_id INTEGER NOT NULL REFERENCES samples(id),
  reviewer_id INTEGER NOT NULL REFERENCES users(id),
  agrees INTEGER NOT NULL CHECK (agrees IN (0,1)),
  opinion TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(sample_id, reviewer_id)
);

-- 全流程留痕：收样/超温/破损/过期/撤样/回避/漏项/揭盲/复核
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  sample_id INTEGER REFERENCES samples(id),
  type TEXT NOT NULL,
  detail TEXT,
  actor TEXT,
  created_at TEXT NOT NULL
);

-- 访问审计
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  allowed INTEGER NOT NULL,
  detail TEXT
);

-- 模拟通知落库（适配器可替换为真实渠道）
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  recipient TEXT NOT NULL,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,
  delivered INTEGER NOT NULL DEFAULT 0
);
`;

export function openDb(path) {
  mkdirSync(dirname(path), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec(SCHEMA);
  return db;
}
