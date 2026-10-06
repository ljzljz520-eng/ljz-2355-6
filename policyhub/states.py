"""Three distinct lifecycle states - never interchangeable:

    1. acknowledgement  知悉签收 (you read and signed for THIS version)
    2. training         培训完成 (you completed training for THIS version)
    3. authorization    作业授权 (a manager authorized you to do the work,
                         for a bounded interval, independently revocable)

Reading the document is only a prerequisite for #1. A single "read" click can
never satisfy #2 or #3.
"""
from __future__ import annotations

import secrets
import sqlite3

from . import db
from . import engine as eng
from . import org as orgmod
from .catalog import get_version

READ_TTL = 3600
TRAINING_TTL = 7 * 24 * 3600


class StateError(Exception):
    pass


def _user(conn, username):
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        raise StateError(f"unknown user {username}")
    return row["id"]


# ------------------------------------------------------------------ reading
def open_read_session(conn, *, username, version_id, at, ttl=READ_TTL):
    """A read session proves the client actually opened the pinned version.
    It expires; acknowledgement must be submitted with the same content hash."""
    uid = _user(conn, username)
    v = get_version(conn, version_id)
    if v is None:
        raise StateError("unknown version")
    token = secrets.token_urlsafe(18)
    conn.execute(
        "INSERT INTO ack_sessions(token,user_id,policy_version_id,content_hash,"
        "issued_at,expires_at,consumed) VALUES(?,?,?,?,?,?,0)",
        (token, uid, version_id, v["content_hash"], at, at + ttl),
    )
    conn.commit()
    return {"token": token, "version_id": version_id,
            "content_hash": v["content_hash"], "expires_at": at + ttl}


def acknowledge(conn, *, username, token, at):
    """Sign acknowledgement. Rejected STALE_VERSION if the signed version is no
    longer the current effective version (acceptance: 签收时版本更新), or if the
    read session expired/was reused/hashed against different content."""
    uid = _user(conn, username)
    sess = conn.execute("SELECT * FROM ack_sessions WHERE token=?", (token,)).fetchone()
    if sess is None:
        raise StateError("BAD_SESSION")
    if sess["user_id"] != uid:
        raise StateError("SESSION_OWNER_MISMATCH")
    if sess["consumed"]:
        raise StateError("SESSION_REUSED")
    if at >= sess["expires_at"]:
        raise StateError("SESSION_EXPIRED")
    v = get_version(conn, sess["policy_version_id"])
    if v is None or v["content_hash"] != sess["content_hash"]:
        raise StateError("CONTENT_MISMATCH")
    ver, _w, open_now = eng._pick_version(conn, v["policy_id"], at)
    if ver is None or ver["id"] != v["id"]:
        # The old acknowledgement is still recorded for traceability below, but
        # signing for an outdated text is rejected and must be re-done.
        current_no = ver["version_no"] if ver else None
        raise StateError(f"STALE_VERSION: v{v['version_no']} no longer current"
                         f"{'' if current_no is None else f' (now v{current_no})'}")
    conn.execute(
        "INSERT INTO policy_acks(user_id,policy_version_id,content_hash,acked_at)"
        " VALUES(?,?,?,?) ON CONFLICT(user_id,policy_version_id) DO UPDATE SET"
        " content_hash=excluded.content_hash, acked_at=excluded.acked_at",
        (uid, v["id"], v["content_hash"], at),
    )
    conn.execute("UPDATE ack_sessions SET consumed=1 WHERE token=?", (token,))
    conn.commit()
    return {"version_id": v["id"], "version_no": v["version_no"], "acked_at": at,
            "note": "知悉签收仅代表阅读，不代表培训完成或获得作业授权"}


# ----------------------------------------------------------------- training
def issue_training_link(conn, *, version_id, at, ttl=TRAINING_TTL):
    if get_version(conn, version_id) is None:
        raise StateError("unknown version")
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO training_links(token,version_id,issued_at,expires_at,consumed)"
        " VALUES(?,?,?,?,0)", (token, version_id, at, at + ttl))
    conn.commit()
    return {"token": token, "version_id": version_id, "expires_at": at + ttl}


def complete_training(conn, *, username, token, at):
    """Single-use, expiring training completion (acceptance: 培训链接失效)."""
    uid = _user(conn, username)
    link = conn.execute("SELECT * FROM training_links WHERE token=?", (token,)).fetchone()
    if link is None:
        raise StateError("TRAINING_LINK_INVALID")
    if link["consumed"]:
        raise StateError("TRAINING_LINK_USED")
    if at >= link["expires_at"]:
        raise StateError("TRAINING_LINK_EXPIRED")
    v = get_version(conn, link["version_id"])
    if v is None:
        raise StateError("TRAINING_LINK_INVALID")
    conn.execute(
        "INSERT INTO policy_training(user_id,version_id,completed_at,via_link)"
        " VALUES(?,?,?,?) ON CONFLICT(user_id,version_id) DO UPDATE SET"
        " completed_at=excluded.completed_at, via_link=excluded.via_link",
        (uid, v["id"], at, token),
    )
    conn.execute("UPDATE training_links SET consumed=1 WHERE token=?", (token,))
    conn.commit()
    return {"version_id": v["id"], "version_no": v["version_no"],
            "completed_at": at, "state": "TRAINED",
            "note": "培训完成不等于作业授权"}


# ------------------------------------------------------------ authorization
def grant_work(conn, *, username, policy_id, at, valid_to=None, granted_by="manager"):
    uid = _user(conn, username)
    cur = conn.execute(
        "INSERT INTO work_authorizations(user_id,policy_id,granted_by,valid_from,valid_to)"
        " VALUES(?,?,?,?,?)", (uid, policy_id, granted_by, at, valid_to))
    conn.commit()
    return cur.lastrowid


def revoke_work(conn, *, auth_id, at, reason=""):
    row = conn.execute("SELECT id FROM work_authorizations WHERE id=?",
                       (auth_id,)).fetchone()
    if row is None:
        raise StateError("unknown authorization")
    exists = conn.execute("SELECT 1 FROM auth_revocations WHERE auth_id=?",
                          (auth_id,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO auth_revocations(auth_id,revoked_at,reason) VALUES(?,?,?)",
            (auth_id, at, reason))
        conn.commit()


def transfer_works(conn, *, username, keep_policy_ids, at):
    """调岗/撤权: revoke open authorizations not explicitly carried over."""
    uid = _user(conn, username)
    rows = conn.execute(
        "SELECT id FROM work_authorizations WHERE user_id=? AND valid_from<=? "
        "AND (valid_to IS NULL OR valid_to>?)", (uid, at, at)).fetchall()
    revoked = []
    for r in rows:
        if r["id"] in keep_policy_ids:
            continue
        had = conn.execute("SELECT 1 FROM auth_revocations WHERE auth_id=?",
                           (r["id"],)).fetchone()
        if not had:
            conn.execute(
                "INSERT INTO auth_revocations(auth_id,revoked_at,reason) VALUES(?,?,?)",
                (r["id"], at, "调岗/撤权时未保留的作业授权"))
            revoked.append(r["id"])
    conn.commit()
    return revoked


def _auth_active(conn, uid, policy_id, at):
    rows = conn.execute(
        "SELECT * FROM work_authorizations WHERE user_id=? AND policy_id=? "
        "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
        (uid, policy_id, at, at)).fetchall()
    for a in rows:
        rev = conn.execute("SELECT * FROM auth_revocations WHERE auth_id=? "
                           "AND revoked_at<=?", (a["id"], at)).fetchone()
        if rev is None:
            return a
    return None


# ----------------------------------------------------------------- statuses
def policy_status(conn, *, username, policy_id, at):
    """Aggregate the three states for one policy at one point in time.

    NONE  = never done; CURRENT/ACKED/TRAINED = done for the version in force;
    STALE = done for an older revision (must redo after 修订)."""
    uid = _user(conn, username)
    ver, warnings, open_now = eng._pick_version(conn, policy_id, at)
    vid = ver["id"] if ver else None

    ack = conn.execute(
        "SELECT * FROM policy_acks WHERE user_id=? AND policy_version_id=?",
        (uid, vid if vid else -1)).fetchone() if vid else None
    if ack is None:
        stale_ack = conn.execute(
            "SELECT 1 FROM policy_acks a JOIN policy_versions pv "
            "ON pv.id=a.policy_version_id WHERE a.user_id=? AND pv.policy_id=? LIMIT 1",
            (uid, policy_id)).fetchone()
        ack_state, ack_at = ("STALE", None) if stale_ack else ("NONE", None)
    else:
        ack_state, ack_at = "ACKED", ack["acked_at"]

    tr = conn.execute(
        "SELECT * FROM policy_training WHERE user_id=? AND version_id=?",
        (uid, vid if vid else -1)).fetchone() if vid else None
    if tr is None:
        stale_tr = conn.execute(
            "SELECT 1 FROM policy_training t JOIN policy_versions pv ON pv.id=t.version_id"
            " WHERE t.user_id=? AND pv.policy_id=? LIMIT 1",
            (uid, policy_id)).fetchone()
        tr_state, tr_at = ("STALE", None) if stale_tr else ("NONE", None)
    else:
        tr_state, tr_at = "TRAINED", tr["completed_at"]

    auth = _auth_active(conn, uid, policy_id, at)
    return {
        "policy_id": policy_id,
        "version_no": ver["version_no"] if ver else None,
        "currently_effective": open_now,
        "acknowledgement": {"state": ack_state, "at": ack_at},
        "training": {"state": tr_state, "at": tr_at},
        "authorization": {"state": "AUTHORIZED" if auth else "NONE",
                          "auth_id": auth["id"] if auth else None,
                          "valid_to": auth["valid_to"] if auth else None},
        "warnings": warnings,
    }


def work_gate(conn, *, username, policy_id, at, equipment=None, scene=None):
    """Can the employee actually perform the regulated work right now?
    Returns each gate independently - no single click bypasses any of them."""
    deriv = eng.evaluate_policy(conn, username=username, policy_id=policy_id,
                                at=at, equipment=equipment, scene=scene)
    status = policy_status(conn, username=username, policy_id=policy_id, at=at)
    gates = {
        "applicable_version": {"ok": deriv["applicable"], "detail":
                               f"{deriv['winning_source']} → v{deriv['version_no']}"},
        "acknowledged": {"ok": status["acknowledgement"]["state"] == "ACKED",
                         "detail": status["acknowledgement"]["state"]},
        "trained": {"ok": status["training"]["state"] == "TRAINED",
                    "detail": status["training"]["state"]},
        "authorized": {"ok": status["authorization"]["state"] == "AUTHORIZED",
                       "detail": status["authorization"]["state"]},
    }
    allowed = deriv["applicable"] and all(g["ok"] for g in gates.values())
    return {"allowed": allowed, "gates": gates,
            "winning_source": deriv["winning_source"],
            "version_no": deriv["version_no"],
            "derivation_chain": deriv["chain"], "warnings": deriv["warnings"]}
