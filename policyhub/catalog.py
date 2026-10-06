"""Authoring boundary: policy content, revisions, scope and exceptions.

This is the *only* module that inserts policy content. There is deliberately no
auto-generation of work-safety rules: every rule is authored/reviewed by an
accountable human (policies.is_system marks foundational human rules; nothing
is synthesized from logs, prompts or other policies).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from . import db
from .engine import _pick_version


class CatalogError(Exception):
    pass


def _hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def create_policy(conn, *, code, title, category, public_meta=False,
                  is_system=False, author="admin"):
    try:
        cur = conn.execute(
            "INSERT INTO policies(code,title,category,public_meta,is_system,created_by)"
            " VALUES(?,?,?,?,?,?)",
            (code, title, category, int(public_meta), int(is_system), author),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise CatalogError(f"policy code conflict: {code}") from e
    return cur.lastrowid


def add_version(conn, *, policy_id, title, body, valid_from, valid_to=None,
                author="admin", now=None, bump_epoch=True):
    """Publish a new revision. Old rows are preserved untouched so point-in-time
    evaluation always sees the exact text/scope in force then."""
    if now is None:
        now = valid_from
    nxt = conn.execute(
        "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM policy_versions WHERE policy_id=?",
        (policy_id,),
    ).fetchone()["n"]
    cur = conn.execute(
        "INSERT INTO policy_versions"
        "(policy_id,version_no,title,body,content_hash,valid_from,valid_to,"
        "published_by,published_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (policy_id, nxt, title, body, _hash(body), valid_from, valid_to,
         author, now),
    )
    conn.commit()
    if bump_epoch:
        db.bump_rule_epoch(conn)
    return cur.lastrowid


def close_version(conn, version_id, valid_to):
    conn.execute("UPDATE policy_versions SET valid_to=? WHERE id=?",
                 (valid_to, version_id))
    conn.commit()
    db.bump_rule_epoch(conn)


def add_scope(conn, *, version_id, position_code, equipment_codes=None,
              scene_codes=None):
    pid = conn.execute("SELECT id FROM positions WHERE code=?",
                       (position_code,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO policy_scope(policy_version_id,position_id,equipment_codes,"
        "scene_codes) VALUES(?,?,?,?)",
        (version_id, pid, json.dumps(equipment_codes or []),
         json.dumps(scene_codes or [])),
    )
    conn.commit()


def add_exception(conn, *, version_id, subject_type, subject_code, grant,
                  reason, valid_from, valid_to=None):
    """Exceptions bind to a *specific* version; they never silently leak onto a
    future revision."""
    if subject_type == "USER":
        row = conn.execute("SELECT id FROM users WHERE username=?",
                           (subject_code,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM positions WHERE code=?",
                           (subject_code,)).fetchone()
    if row is None:
        raise CatalogError(f"unknown {subject_type}: {subject_code}")
    cur = conn.execute(
        "INSERT INTO policy_exceptions(policy_version_id,subject_type,subject_id,"
        "grant,reason,valid_from,valid_to) VALUES(?,?,?,?,?,?,?)",
        (version_id, subject_type, row["id"], int(grant), reason,
         valid_from, valid_to),
    )
    conn.commit()
    db.bump_rule_epoch(conn)
    return cur.lastrowid


def revoke_exception(conn, exception_id, at):
    """Acceptance: an expired/revoked exception stops applying."""
    conn.execute(
        "UPDATE policy_exceptions SET valid_to=? WHERE id=? AND "
        "(valid_to IS NULL OR valid_to>?)", (at, exception_id, at))
    conn.commit()
    db.bump_rule_epoch(conn)


def add_attachment(conn, *, version_id, filename, content: bytes):
    h = "sha256:" + hashlib.sha256(content).hexdigest()
    cur = conn.execute(
        "INSERT INTO attachments(policy_version_id,filename,content,content_hash)"
        " VALUES(?,?,?,?)",
        (version_id, filename, content, h),
    )
    conn.commit()
    return cur.lastrowid


def get_version(conn, version_id):
    return conn.execute("SELECT * FROM policy_versions WHERE id=?",
                        (version_id,)).fetchone()


def current_version(conn, policy_id, at):
    return _pick_version(conn, policy_id, at)
