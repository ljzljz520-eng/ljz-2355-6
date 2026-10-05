"""数据库模式：制度生效区间、适用规则与例外、签收/培训/授权、离线授权、预计算集。"""
import sqlite3

SCHEMA = """
CREATE TABLE positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES positions(id)      -- 岗位继承树
);

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',          -- active | departed
  position_id INTEGER NOT NULL REFERENCES positions(id),
  departed_at TEXT
);

CREATE TABLE position_history (                    -- 调岗区间，支撑历史时点追溯
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  position_id INTEGER NOT NULL REFERENCES positions(id),
  valid_from TEXT NOT NULL,
  valid_to TEXT
);

CREATE TABLE policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  is_public INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE policy_versions (                     -- 生效区间 [effective_from, effective_to)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id INTEGER NOT NULL REFERENCES policies(id),
  version_no INTEGER NOT NULL,
  content TEXT NOT NULL,
  effective_from TEXT NOT NULL,
  effective_to TEXT,                               -- NULL = 开口区间
  created_at TEXT NOT NULL,
  UNIQUE(policy_id, version_no)
);

CREATE TABLE rules (                               -- 适用规则与例外，自带有效期
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id INTEGER NOT NULL REFERENCES policies(id),
  subject_type TEXT NOT NULL CHECK(subject_type IN ('user','position')),
  subject_id INTEGER NOT NULL,
  effect TEXT NOT NULL CHECK(effect IN ('include','exclude')),
  kind TEXT NOT NULL CHECK(kind IN ('base','exception')),
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  note TEXT DEFAULT ''
);

CREATE TABLE documents (                           -- 文档站条目：岗位/设备/场景三维标签
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id INTEGER REFERENCES policies(id),
  title TEXT NOT NULL,
  equipment TEXT,
  scenario TEXT,
  position_id INTEGER REFERENCES positions(id),
  is_internal INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE training_resources (                  -- 培训链接，可失效
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id INTEGER NOT NULL REFERENCES policies(id),
  url TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE acks (                                -- 知悉签收：绑定具体版本
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  policy_version_id INTEGER NOT NULL REFERENCES policy_versions(id),
  status TEXT NOT NULL CHECK(status IN ('pending','signed','stale')),
  begun_at TEXT NOT NULL,
  signed_at TEXT
);

CREATE TABLE trainings (                           -- 培训完成：绑定培训资源
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  policy_id INTEGER NOT NULL REFERENCES policies(id),
  resource_id INTEGER NOT NULL REFERENCES training_resources(id),
  completed_at TEXT NOT NULL,
  score INTEGER
);

CREATE TABLE authorizations (                      -- 作业授权：独立状态，可撤销、可过期
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  policy_id INTEGER NOT NULL REFERENCES policies(id),
  status TEXT NOT NULL CHECK(status IN ('active','revoked','expired')),
  granted_at TEXT NOT NULL,
  expires_at TEXT,
  revoked_at TEXT
);

CREATE TABLE offline_grants (                      -- 离线附件有限期访问
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  document_id INTEGER NOT NULL REFERENCES documents(id),
  token TEXT UNIQUE NOT NULL,
  granted_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE precomputed_sets (                    -- 按岗位预计算的适用集（缓存）
  position_id INTEGER NOT NULL REFERENCES positions(id),
  policy_id INTEGER NOT NULL REFERENCES policies(id),
  computed_at TEXT NOT NULL,
  PRIMARY KEY (position_id, policy_id)
);

CREATE TABLE projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  equipment TEXT
);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
