"""Finite-lived offline access to policy attachments.

An offline grant is a time-boxed, snapshot-pinned token. Redemption is
re-authorized at use time against *current* facts:

* the grant window must be open and the grant not revoked;
* the user must still be employed (离职 old cache is refused);
* the version must still be in force and still visible to the user under the
  live priority rules (调岗/撤权 therefore cut access even mid-window);
* the redeemed bytes must hash to the snapshot pinned at issue time.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3

from . import engine as eng

OFFLINE_TTL = 7 * 24 * 3600


class OfflineError(Exception):
    pass


def _uid(conn, username):
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        raise OfflineError("unknown user")
    return row["id"]


def issue(conn, *, username, attachment_id, at, ttl=OFFLINE_TTL):
    uid = _uid(conn, username)
    att = conn.execute("SELECT * FROM attachments WHERE id=?",
                       (attachment_id,)).fetchone()
    if att is None:
        raise OfflineError("unknown attachment")
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO offline_grants(token,user_id,attachment_id,snapshot_hash,"
        "issued_at,expires_at,revoked) VALUES(?,?,?,?,?,?,0)",
        (token, uid, attachment_id, att["content_hash"], at, at + ttl),
    )
    conn.commit()
    return {"token": token, "attachment_id": attachment_id,
            "filename": att["filename"], "snapshot_hash": att["content_hash"],
            "expires_at": at + ttl}


def revoke(conn, token, at):
    conn.execute("UPDATE offline_grants SET revoked=1 WHERE token=?", (token,))
    conn.commit()


def revoke_all_for_user(conn, username, at):
    """Called on 离职: every held offline token dies immediately."""
    uid = _uid(conn, username)
    conn.execute("UPDATE offline_grants SET revoked=1 WHERE user_id=? AND revoked=0",
                 (uid,))
    conn.commit()


def redeem(conn, *, username, token, at, equipment=None, scene=None):
    uid = _uid(conn, username)
    g = conn.execute("SELECT * FROM offline_grants WHERE token=?",
                     (token,)).fetchone()
    if g is None:
        raise OfflineError("OFFLINE_TOKEN_INVALID")
    if g["user_id"] != uid:
        raise OfflineError("OFFLINE_TOKEN_OWNER_MISMATCH")
    if g["revoked"]:
        raise OfflineError("OFFLINE_REVOKED")
    if at < g["issued_at"]:
        raise OfflineError("OFFLINE_NOT_STARTED")
    if at >= g["expires_at"]:
        raise OfflineError("OFFLINE_EXPIRED")

    att = conn.execute("SELECT * FROM attachments WHERE id=?",
                       (g["attachment_id"],)).fetchone()
    if att is None:
        raise OfflineError("ATTACHMENT_GONE")
    ver = conn.execute("SELECT * FROM policy_versions WHERE id=?",
                       (att["policy_version_id"],)).fetchone()
    # current employment + live applicability decide, not the old grant alone
    deriv = eng.evaluate_policy(conn, username=username, policy_id=ver["policy_id"],
                                at=at, equipment=equipment, scene=scene)
    if not deriv["applicable"]:
        raise OfflineError(f"OFFLINE_NO_LONGER_VISIBLE ({deriv['winning_source']})")
    if deriv["version_id"] != ver["id"]:
        raise OfflineError("OFFLINE_VERSION_SUPERSEDED")
    if hashlib.sha256(att["content"]).hexdigest() != g["snapshot_hash"].split(":", 1)[-1]:
        raise OfflineError("OFFLINE_SNAPSHOT_TAMPERED")
    return {"filename": att["filename"], "content": att["content"],
            "content_hash": g["snapshot_hash"],
            "version_no": ver["version_no"]}


def list_grants(conn, username, at):
    uid = _uid(conn, username)
    rows = conn.execute(
        "SELECT g.token, g.attachment_id, a.filename, g.issued_at, g.expires_at,"
        " g.revoked FROM offline_grants g JOIN attachments a ON a.id=g.attachment_id"
        " WHERE g.user_id=? ORDER BY g.issued_at", (uid,)).fetchall()
    out = []
    for r in rows:
        status = "REVOKED" if r["revoked"] else ("EXPIRED" if at >= r["expires_at"] else "ACTIVE")
        out.append({"token": r["token"], "attachment_id": r["attachment_id"],
                    "filename": r["filename"], "issued_at": r["issued_at"],
                    "expires_at": r["expires_at"], "status": status})
    return out
