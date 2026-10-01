"""SQLite 连接与建表。评分、揭盲结果、访问审计全部持久化。"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    display     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('admin','buyer','reviewer'))
);

CREATE TABLE IF NOT EXISTS suppliers (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    contact TEXT
);

-- 样品：封存编号 seal_code 对当前（未撤样）样品全局唯一
CREATE TABLE IF NOT EXISTS samples (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,           -- 样品编号，评审页只显示它
    category        TEXT NOT NULL,                  -- 品类
    origin          TEXT NOT NULL,                  -- 产地
    arrival_temp    REAL NOT NULL,                  -- 到货温度 ℃
    temp_limit      REAL NOT NULL DEFAULT 4.0,      -- 到货温度上限
    batch_no        TEXT NOT NULL,                  -- 批次
    seal_code       TEXT NOT NULL,                  -- 封存编号
    storage_loc     TEXT NOT NULL,                  -- 封样库位
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(id),
    photo_path      TEXT,
    received_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    sample_date     TEXT NOT NULL,                  -- 样品生产日期(判过期用)
    shelf_days      INTEGER NOT NULL DEFAULT 3,     -- 保质期天数
    status          TEXT NOT NULL DEFAULT 'sealed'
        CHECK (status IN ('sealed','evaluating','revealed','decision',
                          'withdrawn','seal_broken','over_temp','expired','rejected')),
    exclude_reason  TEXT
);

-- 一份封存编号只能关联一份当前样品：撤样后该封存编号方可释放复用
CREATE UNIQUE INDEX IF NOT EXISTS idx_seal_active
    ON samples(seal_code) WHERE status != 'withdrawn';

-- 全流程留痕：收样/封签破损/超温/过期/撤样/状态流转…
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL REFERENCES samples(id),
    kind        TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT 'system',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 评审人回避申请
CREATE TABLE IF NOT EXISTS recusals (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL REFERENCES samples(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sample_id, user_id)
);

-- 盲评记录：评审人对样品的一条意见（口感/规格/烹调损耗分区）
CREATE TABLE IF NOT EXISTS scores (
    id              INTEGER PRIMARY KEY,
    sample_id       INTEGER NOT NULL REFERENCES samples(id),
    user_id         INTEGER NOT NULL REFERENCES users(id),
    taste           INTEGER NOT NULL CHECK (taste  BETWEEN 1 AND 10),
    spec            INTEGER NOT NULL CHECK (spec   BETWEEN 1 AND 10),
    loss_measured   REAL    NOT NULL CHECK (loss_measured >= 0 AND loss_measured < 100), -- 实测损耗%
    comment         TEXT NOT NULL DEFAULT '',
    submitted_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sample_id, user_id)
);

-- 揭盲需要两人同时确认，只产生一次结果
CREATE TABLE IF NOT EXISTS unveil_confirmations (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL REFERENCES samples(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sample_id, user_id)
);

CREATE TABLE IF NOT EXISTS unveils (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL UNIQUE REFERENCES samples(id), -- 每样品仅一次
    confirmer_a INTEGER NOT NULL REFERENCES users(id),
    confirmer_b INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (confirmer_a <> confirmer_b)
);

-- 揭盲后统一形成的选用意见
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL UNIQUE REFERENCES samples(id),
    result      TEXT NOT NULL CHECK (result IN ('select','reject')),
    opinion     TEXT NOT NULL DEFAULT '',
    decided_by  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 结果复核
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL REFERENCES samples(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    passed      INTEGER NOT NULL CHECK (passed IN (0,1)),
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sample_id, user_id)
);

-- 访问审计：揭盲前查询供应商身份、评分提交、揭盲、复核等全部留痕
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER,
    username    TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    allowed     INTEGER NOT NULL DEFAULT 1,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 模拟通知适配器的落库记录
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    channel     TEXT NOT NULL DEFAULT 'mock',
    to_user     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
