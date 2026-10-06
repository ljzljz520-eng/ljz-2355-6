"""Request-time applicability evaluator.

Resolution rules (documented, not guessed by the employee):

    rank 0  USER_DENY       personal exclusion (highest, safety first)
    rank 1  USER_GRANT      personal inclusion, overrides positional rules
    rank 2  POSITION_DENY   exclusion attached to self or an ancestor post
    rank 3  POSITION_GRANT  inclusion attached to self or an ancestor post
    rank 4  DIRECT          scope names the employee's own post
    rank 5  INHERITED       scope matches an ancestor post (nearest wins)
    rank 6  NONE            default: not applicable

* DENY always beats GRANT at a broader level (a deny on self/ancestor cannot be
  escaped by an inherited grant), except an explicit *personal* GRANT which is
  a deliberate human exception narrower than a positional deny.
* Every candidate that fired is returned in an ordered derivation chain; the
  winning source and every source it defeated are shown with reasons.
"""
from __future__ import annotations

import sqlite3

from . import org
from .db import jloads

USER_DENY, USER_GRANT = 0, 1
POSITION_DENY, POSITION_GRANT = 2, 3
DIRECT, INHERITED, NONE = 4, 5, 6
SOURCE_NAME = {
    0: "USER_DENY", 1: "USER_GRANT", 2: "POSITION_DENY", 3: "POSITION_GRANT",
    4: "DIRECT", 5: "INHERITED", 6: "NONE",
}
GRANT_SOURCES = {USER_GRANT, POSITION_GRANT, DIRECT, INHERITED}
DENY_SOURCES = {USER_DENY, POSITION_DENY}


class EvalError(Exception):
    pass


def _active(valid_from, valid_to, at) -> bool:
    if valid_from is not None and at < valid_from:
        return False
    if valid_to is not None and at >= valid_to:
        return False
    return True


def _codeset(text):
    vals = jloads(text)
    return set(vals) if vals else set()


def _constraint_ok(required: set, requested) -> bool:
    """Wildcard semantics: no request filter or '*' scope => pass."""
    if requested is None:
        return True
    if "*" in required:
        return True
    return requested in required


def _scope_candidates(conn, version_id, chain, equipment, scene):
    """DIRECT / INHERITED candidates contributed by scope rows."""
    out = []
    rows = conn.execute(
        "SELECT * FROM policy_scope WHERE policy_version_id=?", (version_id,)
    ).fetchall()
    chain_ids = {n.id: n for n in chain}
    for row in rows:
        if row["position_id"] not in chain_ids:
            continue
        node = chain_ids[row["position_id"]]
        equips = _codeset(row["equipment_codes"])
        scenes = _codeset(row["scene_codes"])
        eq_ok = _constraint_ok(equips, equipment)
        sc_ok = _constraint_ok(scenes, scene)
        if not (eq_ok and sc_ok):
            out.append({
                "source": INHERITED if node.distance else DIRECT,
                "rank": 99,  # considered but not a winner (filter mismatch)
                "won": False,
                "position": node.code,
                "distance": node.distance,
                "detail": f"岗位命中但设备/场景过滤不匹配 "
                          f"(scope equipment={sorted(equips)}, scene={sorted(scenes)}; "
                          f"request equipment={equipment!r}, scene={scene!r})",
            })
            continue
        rank = DIRECT if node.distance == 0 else INHERITED
        out.append({
            "source": rank,
            "rank": rank,
            "won": False,
            "position": node.code,
            "distance": node.distance,
            "detail": ("制度范围直接覆盖本岗位" if rank == DIRECT
                       else f"经岗位继承链命中祖先岗位 '{node.name}'（距离 {node.distance}）"),
        })
    return out


def _exception_candidates(conn, version_id, user_id, chain, at):
    out = []
    rows = conn.execute(
        "SELECT * FROM policy_exceptions WHERE policy_version_id=?", (version_id,)
    ).fetchall()
    chain_ids = {n.id: n for n in chain}
    for row in rows:
        if not _active(row["valid_from"], row["valid_to"], at):
            out.append({
                "source": 98, "rank": 98, "won": False,
                "detail": f"例外(id={row['id']}, {row['subject_type']}, "
                          f"{'GRANT' if row['grant'] else 'DENY'}) 已过生效区间，忽略",
            })
            continue
        if row["subject_type"] == "USER":
            if row["subject_id"] != user_id:
                continue
            rank = USER_DENY if not row["grant"] else USER_GRANT
            out.append({
                "source": rank, "rank": rank, "won": False,
                "exception_id": row["id"],
                "detail": f"针对本人的{'排除(DENY)' if not row['grant'] else '形入(GRANT)'}例外: {row['reason']}",
            })
        else:
            node = chain_ids.get(row["subject_id"])
            if node is None:
                continue
            rank = POSITION_DENY if not row["grant"] else POSITION_GRANT
            out.append({
                "source": rank, "rank": rank, "won": False,
                "exception_id": row["id"],
                "position": node.code, "distance": node.distance,
                "detail": f"岗位 '{node.name}' 的{'排除(DENY)' if not row['grant'] else '形入(GRANT)'}"
                          f"例外（继承距离 {node.distance}）: {row['reason']}",
            })
    return out


def _pick_version(conn, policy_id, at):
    """Choose the policy version effective at ``at``; flag overlapping windows."""
    rows = conn.execute(
        "SELECT * FROM policy_versions WHERE policy_id=? ORDER BY version_no",
        (policy_id,),
    ).fetchall()
    active = [r for r in rows if _active(r["valid_from"], r["valid_to"], at)]
    warnings = []
    if len(active) > 1:
        warnings.append(
            "制度有效期重叠: 版本 "
            + ", ".join(f"v{r['version_no']}" for r in active)
            + f" 在该时点同时生效；按最新版本 v{max(r['version_no'] for r in active)} 适用，"
              "请管理员修正生效区间。"
        )
    if not active:
        # Historical traceability: pick the newest version published at/before
        # this point even if already closed. Querying an old point in time must
        # reconstruct exactly the rules (and text) in force then. The chosen row
        # is a historical (already closed / not yet open) version, never the
        # "currently effective" one.
        past = [r for r in rows if r["valid_from"] <= at]
        chosen = max(past, key=lambda r: r["version_no"], default=None)
        return chosen, warnings, False
    chosen = max(active, key=lambda r: r["version_no"])
    # The chosen row is the one live at ``at``; "currently effective" means its
    # window is still open at the actual present (valid_to IS NULL/open).
    open_now = chosen["valid_to"] is None
    return chosen, warnings, open_now


def evaluate_policy(conn, *, username, policy_id, at, equipment=None, scene=None):
    from . import clock as _clock  # noqa: F401 (timestamps supplied by caller)
    urow = conn.execute("SELECT id, username, name FROM users WHERE username=?",
                        (username,)).fetchone()
    if urow is None:
        raise EvalError(f"unknown user {username}")
    assignment = org.active_assignment(conn, username, at)
    chain = []
    if assignment:
        chain = org.position_chain(conn, assignment["position_id"])

    prow = conn.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
    if prow is None:
        raise EvalError(f"unknown policy {policy_id}")
    version, warnings, currently_open = _pick_version(conn, policy_id, at)

    chain_steps = []
    if assignment is None:
        chain_steps.append({
            "source": USER_DENY, "rank": USER_DENY, "won": False,
            "detail": "该用户在请求时点没有有效任职（离职或尚未入职），默认拒绝（旧缓存无效）",
        })
    if version is None:
        return {
            "policy_id": policy_id, "policy_code": prow["code"], "title": prow["title"],
            "version_no": None, "decision": "DENY", "applicable": False,
            "winning_source": "NONE", "rank": NONE,
            "chain": chain_steps + [{"source": NONE, "rank": NONE, "won": True,
                                     "detail": "该时点没有任何已发布版本"}],
            "warnings": warnings, "at": at,
        }

    if assignment:
        chain_steps.extend(_exception_candidates(conn, version["id"], urow["id"], chain, at))
        chain_steps.extend(_scope_candidates(conn, version["id"], chain, equipment, scene))
    # rank 98/99 = considered-but-inert, never win
    winners = [c for c in chain_steps if c["rank"] < 90]
    best = min((c["rank"] for c in winners), default=NONE)
    for c in chain_steps:
        if c["rank"] == best:
            c["won"] = True
            break
    applicable = best in GRANT_SOURCES
    # ordering: effective priority first, inert notes last
    chain_steps.sort(key=lambda c: (c["rank"] >= 90, c["rank"]))
    return {
        "policy_id": policy_id, "policy_code": prow["code"], "title": prow["title"],
        "version_id": version["id"], "version_no": version["version_no"],
        "content_hash": version["content_hash"],
        "decision": "GRANT" if applicable else "DENY",
        "applicable": applicable,
        "winning_source": SOURCE_NAME[best], "rank": best,
        "currently_effective": currently_open,
        "chain": chain_steps, "warnings": warnings,
        "version_valid_from": version["valid_from"],
        "version_valid_to": version["valid_to"], "at": at,
    }


def evaluate_all(conn, *, username, at, equipment=None, scene=None,
                 include_denied=True):
    """Resolve every policy at one point in time, with a derivation chain each."""
    rows = conn.execute("SELECT id FROM policies ORDER BY id").fetchall()
    results = []
    for r in rows:
        res = evaluate_policy(conn, username=username, policy_id=r["id"], at=at,
                              equipment=equipment, scene=scene)
        if include_denied or res["applicable"]:
            results.append(res)
    return results
