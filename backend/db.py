"""SQLite 连接与表结构。

核心建模：一次交换 = 一份契约(swaps) + 若干条「谁教谁什么」的边(swap_legs)。
  双向互换 = 2 条边，三角互换 = 3 条边，时间币约课 / 授课赚币 = 1 条边。
所有交换形态共用同一套状态流转和打卡逻辑，不为每种形态单独建表。
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("SKILLSWAP_DB", os.path.join(os.path.dirname(__file__), "skillswap.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  token    TEXT UNIQUE,
  name     TEXT NOT NULL,
  city     TEXT NOT NULL DEFAULT '上海',
  mode     TEXT NOT NULL DEFAULT '均可',          -- 线上 / 线下 / 均可
  times    TEXT NOT NULL DEFAULT '',              -- 逗号分隔：工作日晚,周末白天,周末晚
  bio      TEXT NOT NULL DEFAULT '',
  verified INTEGER NOT NULL DEFAULT 0,
  is_demo  INTEGER NOT NULL DEFAULT 0,            -- 种子用户：自动确认邀请、自动回消息
  rating   REAL NOT NULL DEFAULT 5.0,
  swaps_done INTEGER NOT NULL DEFAULT 0,
  coins    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS skills (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind    TEXT NOT NULL CHECK (kind IN ('teach','learn')),
  name    TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '其他',
  level   TEXT NOT NULL DEFAULT '',
  detail  TEXT NOT NULL DEFAULT '',
  UNIQUE (user_id, kind, name)
);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(kind, name);
CREATE TABLE IF NOT EXISTS swaps (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  type    TEXT NOT NULL CHECK (type IN ('mutual','chain','coin','earn')),
  status  TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','done','cancelled')),
  total   INTEGER NOT NULL DEFAULT 4,             -- 每条边约定的节数
  pace    TEXT NOT NULL DEFAULT '每周 1 次',
  mode    TEXT NOT NULL DEFAULT '线上视频',
  trial   INTEGER NOT NULL DEFAULT 1,             -- 是否含 15 分钟试课
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS swap_legs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  swap_id    INTEGER NOT NULL REFERENCES swaps(id) ON DELETE CASCADE,
  teacher_id INTEGER NOT NULL REFERENCES users(id),
  learner_id INTEGER NOT NULL REFERENCES users(id),
  skill      TEXT NOT NULL,
  done       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS swap_parties (
  swap_id  INTEGER NOT NULL REFERENCES swaps(id) ON DELETE CASCADE,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  accepted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (swap_id, user_id)
);
CREATE TABLE IF NOT EXISTS reviews (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  swap_id  INTEGER NOT NULL REFERENCES swaps(id),
  from_id  INTEGER NOT NULL REFERENCES users(id),
  to_id    INTEGER NOT NULL REFERENCES users(id),
  stars    INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
  text     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (swap_id, from_id, to_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id INTEGER NOT NULL REFERENCES users(id),
  to_id   INTEGER NOT NULL REFERENCES users(id),
  text    TEXT NOT NULL,
  system  INTEGER NOT NULL DEFAULT 0,
  read    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_msg_pair ON messages(from_id, to_id);
CREATE TABLE IF NOT EXISTS ledger (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  delta   INTEGER NOT NULL,
  reason  TEXT NOT NULL,
  swap_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def add_coins(conn, user_id, delta, reason, swap_id=None):
    """时间币只通过这里变动，保证余额和账本永远对得上。"""
    conn.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (delta, user_id))
    conn.execute("INSERT INTO ledger (user_id, delta, reason, swap_id) VALUES (?,?,?,?)",
                 (user_id, delta, reason, swap_id))
