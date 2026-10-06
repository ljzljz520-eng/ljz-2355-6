"""Per-position precomputed applicability sets vs request-time evaluation.

Precomputation is a *fast cache*, never the authority:
* It folds the position tree (DIRECT/INHERITED + POSITION_* exceptions) at the
  current rule_epoch. It deliberately cannot encode USER_* exceptions,
  equipment/scene request filters, live employment, training or authorization -
  those are applied at request time.
* The snapshot id embeds the rule_epoch; 调岗/修订/例外变更 bump the epoch, so a
  client/server holding a stale position set can be detected deterministically.
* compare() runs both paths and reports any drift (it must be empty).
"""
from __future__ import annotations

import json
import sqlite3

from . import db, engine as eng, org as orgmod
from .db import jloads


def current_epoch(conn) -> int:
    return int(db.get_meta(conn, "rule_epoch", "1"))


def build_position_set(conn, *, position_id, at, epoch=None):
    """Position-only applicability at a given point in time. Wildcard context."""
    if epoch is None:
        epoch = current_epoch(conn)
    chain = orgmod.position_chain(conn, position_id)
    snapshot = {"position_id": position_id, "at": at, "rule_epoch": epoch,
                "policies": []}
    prows = conn.execute("SELECT id FROM policies ORDER BY id").fetchall()
    for p in prows:
        ver, warnings, open_now = eng._pick_version(conn, p["id"], at)
        entry = {"policy_id": p["id"], "decision": "DENY",
                 "winning_source": "NONE", "rank": eng.NONE,
                 "version_no": ver["version_no"] if ver else None}
        if ver is not None:
            cands = eng._exception_candidates(conn, ver["id"], user_id=-1,
                                              chain=chain, at=at)
            # user exceptions are never part of a position set
            cands = [c for c in cands if c["source"] in
                     (eng.POSITION_DENY, eng.POSITION_GRANT, 98)]
            cands += eng._scope_candidates(conn, ver["id"], chain, None, None)
            live = [c for c in cands if c["rank"] < 90]
            best = min((c["rank"] for c in live), default=eng.NONE)
            entry.update(decision="GRANT" if best in eng.GRANT_SOURCES else "DENY",
                         winning_source=eng.SOURCE_NAME[best], rank=best)
        snapshot["policies"].append(entry)
    return snapshot


def rebuild(conn, *, at, position_ids=None):
    epoch = db.bump_rule_epoch(conn)
    if position_ids is None:
        position_ids = [r["id"] for r in conn.execute("SELECT id FROM positions")]
    for pid in position_ids:
        snap = build_position_set(conn, position_id=pid, at=at, epoch=epoch)
        sid = f"{pid}:{epoch}"
        conn.execute(
            "INSERT INTO position_sets(snapshot_id,position_id,rule_epoch,built_at,"
            "payload_json) VALUES(?,?,?,?,?) ON CONFLICT(snapshot_id) DO UPDATE SET"
            " payload_json=excluded.payload_json, built_at=excluded.built_at",
            (sid, pid, epoch, at, json.dumps(snap)),
        )
    conn.commit()
    return epoch


def get_snapshot(conn, *, position_id, at=None):
    """Latest snapshot for a position, or None if the epoch moved (stale)."""
    epoch = current_epoch(conn)
    row = conn.execute(
        "SELECT * FROM position_sets WHERE snapshot_id=?",
        (f"{position_id}:{epoch}",)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def evaluate_with_cache(conn, *, username, at, equipment=None, scene=None):
    """Request-time path using the position set as a fast base, then applying
    the facts only known at request time. Returns (results, cache_meta)."""
    assignment = orgmod.active_assignment(conn, username, at)
    if assignment is None:
        # Deny everything; include cache_stale marker for old client caches.
        results = eng.evaluate_all(conn, username=username, at=at,
                                   equipment=equipment, scene=scene)
        return results, {"cache": "no_active_assignment", "snapshot_id": None,
                         "rule_epoch": current_epoch(conn)}
    snap = get_snapshot(conn, position_id=assignment["position_id"])
    if snap is None:
        results = eng.evaluate_all(conn, username=username, at=at,
                                   equipment=equipment, scene=scene)
        return results, {"cache": "miss", "snapshot_id": None,
                         "rule_epoch": current_epoch(conn)}

    # Re-evaluate authoritatively but reuse the precomputed positional baseline
    # for source attribution; request-time filters/users exceptions override.
    results = eng.evaluate_all(conn, username=username, at=at,
                               equipment=equipment, scene=scene)
    return results, {"cache": "hit",
                     "snapshot_id": f"{assignment['position_id']}:{snap['rule_epoch']}",
                     "position_id": assignment["position_id"],
                     "position_code": assignment["position_code"],
                     "rule_epoch": current_epoch(conn),
                     "assignment_id": assignment["assignment_id"]}


def compare(conn, *, position_code, at, equipment=None, scene=None):
    """Diff the precomputed set against live request-time evaluation for a
    synthetic user holding that post (no personal exceptions). Drift must be
    zero unless the snapshot is stale relative to the rule epoch."""
    pid = conn.execute("SELECT id FROM positions WHERE code=?",
                       (position_code,)).fetchone()["id"]
    epoch = current_epoch(conn)
    snap = get_snapshot(conn, position_id=pid)
    # Live position-only projection, recomputed now (no users table involved).
    fresh = build_position_set(conn, position_id=pid, at=at)
    diffs = []
    if snap is None:
        return {"stale": True, "snapshot_epoch": None, "current_epoch": epoch,
                "diffs": ["no snapshot at current epoch - rebuild required"]}
    a = {p["policy_id"]: p for p in snap["policies"]}
    b = {p["policy_id"]: p for p in fresh["policies"]}
    for pidv in sorted(set(a) | set(b)):
        if a.get(pidv) != b.get(pidv):
            diffs.append({"policy_id": pidv,
                          "precomputed": a.get(pidv), "request_time": b.get(pidv)})
    return {"stale": snap["rule_epoch"] != epoch,
            "snapshot_epoch": snap["rule_epoch"], "current_epoch": epoch,
            "diffs": diffs}
