"""Organisation model: position tree (岗位继承) and time-bounded assignments."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


def add_position(conn, code, name, parent_code=None):
    parent_id = None
    if parent_code:
        row = conn.execute("SELECT id FROM positions WHERE code=?", (parent_code,)).fetchone()
        parent_id = row["id"]
    cur = conn.execute(
        "INSERT INTO positions(code,name,parent_id) VALUES(?,?,?)",
        (code, name, parent_id),
    )
    conn.commit()
    return cur.lastrowid


def add_user(conn, username, name):
    cur = conn.execute("INSERT INTO users(username,name) VALUES(?,?)", (username, name))
    conn.commit()
    return cur.lastrowid


def assign(conn, username, position_code, valid_from, valid_to=None):
    """Record a user-position assignment interval (used for 调岗 history)."""
    uid = _uid(conn, username)
    pid = _pid(conn, position_code)
    cur = conn.execute(
        "INSERT INTO user_assignments(user_id,position_id,valid_from,valid_to)"
        " VALUES(?,?,?,?)",
        (uid, pid, valid_from, valid_to),
    )
    conn.commit()
    return cur.lastrowid


def transfer(conn, username, new_position_code, at):
    """调岗: close the currently open assignment at ``at`` and open a new one."""
    uid = _uid(conn, username)
    conn.execute(
        "UPDATE user_assignments SET valid_to=? WHERE user_id=? AND valid_to IS NULL",
        (at, uid),
    )
    row = conn.execute("SELECT id FROM positions WHERE code=?", (new_position_code,)).fetchone()
    cur = conn.execute(
        "INSERT INTO user_assignments(user_id,position_id,valid_from,valid_to)"
        " VALUES(?,?,?,NULL)",
        (uid, row["id"], at),
    )
    conn.commit()
    return cur.lastrowid


def leave(conn, username, at):
    """离职: close all open assignments (no active position afterwards)."""
    uid = _uid(conn, username)
    conn.execute(
        "UPDATE user_assignments SET valid_to=? WHERE user_id=? AND valid_to IS NULL",
        (at, uid),
    )
    conn.commit()


def active_assignment(conn, username, at):
    """Return (assignment_id, position_id, position_code) effective at ``at``.

    None => the person is not employed at that point in time, which forces a
    deny regardless of any stale client cache the person still holds.
    """
    row = conn.execute(
        """
        SELECT ua.id AS aid, p.id AS pid, p.code AS code, p.name AS name
        FROM user_assignments ua
        JOIN users u ON u.id = ua.user_id
        JOIN positions p ON p.id = ua.position_id
        WHERE u.username=? AND ua.valid_from <= ?
          AND (ua.valid_to IS NULL OR ua.valid_to > ?)
        ORDER BY ua.valid_from DESC LIMIT 1
        """,
        (username, at, at),
    ).fetchone()
    if not row:
        return None
    return {"assignment_id": row["aid"], "position_id": row["pid"],
            "position_code": row["code"], "position_name": row["name"]}


@dataclass
class PosNode:
    id: int
    code: str
    name: str
    distance: int  # 0 = self, 1 = parent, ...


def position_chain(conn, position_id: int) -> list[PosNode]:
    """Return [self, parent, grandparent, ...] following the position tree."""
    chain = []
    distance = 0
    pid = position_id
    while pid is not None:
        row = conn.execute("SELECT id,code,name,parent_id FROM positions WHERE id=?", (pid,)).fetchone()
        if row is None:
            break
        chain.append(PosNode(row["id"], row["code"], row["name"], distance))
        pid = row["parent_id"]
        distance += 1
    return chain


def descendant_position_ids(conn, ancestor_id: int) -> list[int]:
    """All positions under ``ancestor_id`` (inclusive) via recursive CTE."""
    rows = conn.execute(
        """
        WITH RECURSIVE sub(id) AS (
            SELECT ?
            UNION ALL
            SELECT p.id FROM positions p JOIN sub ON p.parent_id = sub.id
        )
        SELECT id FROM sub
        """,
        (ancestor_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _uid(conn, username):
    return conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]


def _pid(conn, code):
    return conn.execute("SELECT id FROM positions WHERE code=?", (code,)).fetchone()["id"]
