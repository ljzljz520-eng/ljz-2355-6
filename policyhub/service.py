"""Facade: one place that enforces the visibility boundary and orchestrates
organisation changes (调岗 / 撤权 / 离职) across the state subsystems."""
from __future__ import annotations

import sqlite3

from . import catalog, engine as eng, offline as offmod, org, precompute, states
from .engine import EvalError
from .states import StateError
from .offline import OfflineError


class PolicyService:
    def __init__(self, conn):
        self.conn = conn

    # ------------------------------------------------------------ employee
    def my_policies(self, *, username, at, equipment=None, scene=None,
                    include_denied=False, client_snapshot_id=None):
        """Document library filtered by post/equipment/scene. Each item carries
        its own ordered derivation chain (never an undifferentiated clause bag).

        A client presenting a snapshot id from a previous rule epoch gets an
        explicit cache_stale flag so 调岗/修订/例外变更 invalidate the UI cache.
        """
        results, cache = precompute.evaluate_with_cache(
            self.conn, username=username, at=at, equipment=equipment, scene=scene)
        cache["cache_stale"] = bool(
            client_snapshot_id and cache.get("snapshot_id")
            and client_snapshot_id != cache["snapshot_id"])
        if not include_denied:
            results = [r for r in results if r["applicable"]]
        items = []
        for r in results:
            st = states.policy_status(self.conn, username=username,
                                      policy_id=r["policy_id"], at=at)
            items.append({
                "policy_id": r["policy_id"], "policy_code": r["policy_code"],
                "title": r["title"], "version_no": r["version_no"],
                "decision": r["decision"], "winning_source": r["winning_source"],
                "currently_effective": r.get("currently_effective"),
                "derivation_chain": r["chain"], "warnings": r["warnings"],
                "states": {k: st[k]["state"] for k in
                           ("acknowledgement", "training", "authorization")},
            })
        return {"at": at, "cache": cache, "items": items}

    def derivation(self, *, username, policy_id, at, equipment=None, scene=None):
        return eng.evaluate_policy(self.conn, username=username,
                                   policy_id=policy_id, at=at,
                                   equipment=equipment, scene=scene)

    def policy_body(self, *, username, policy_id, at):
        """Content is served only when live applicability says GRANT."""
        r = eng.evaluate_policy(self.conn, username=username,
                                policy_id=policy_id, at=at)
        if not r["applicable"]:
            raise PermissionError(f"FORBIDDEN ({r['winning_source']})")
        v = catalog.get_version(self.conn, r["version_id"])
        return {"policy_id": policy_id, "version_no": v["version_no"],
                "title": v["title"], "body": v["body"],
                "content_hash": v["content_hash"],
                "winning_source": r["winning_source"],
                "derivation_chain": r["chain"], "warnings": r["warnings"]}

    def attachments(self, *, username, policy_id, at):
        r = eng.evaluate_policy(self.conn, username=username,
                                policy_id=policy_id, at=at)
        if not r["applicable"]:
            raise PermissionError(f"FORBIDDEN ({r['winning_source']})")
        rows = self.conn.execute(
            "SELECT id,filename,content_hash FROM attachments WHERE policy_version_id=?",
            (r["version_id"],)).fetchall()
        return {"version_no": r["version_no"],
                "attachments": [dict(x) for x in rows]}

    # -------------------------------------------------- three-state actions
    def open_read(self, *, username, policy_id, at):
        r = eng.evaluate_policy(self.conn, username=username,
                                policy_id=policy_id, at=at)
        if not r["applicable"]:
            raise PermissionError(f"FORBIDDEN ({r['winning_source']})")
        return states.open_read_session(self.conn, username=username,
                                        version_id=r["version_id"], at=at)

    def acknowledge(self, *, username, token, at):
        return states.acknowledge(self.conn, username=username, token=token, at=at)

    def complete_training(self, *, username, token, at):
        # Training link completion does not itself re-derive employment scope;
        # it requires a valid issued link. Acting on the work still passes the
        # work gate, which enforces applicability independently.
        return states.complete_training(self.conn, username=username,
                                        token=token, at=at)

    def status(self, *, username, policy_id, at):
        return states.policy_status(self.conn, username=username,
                                    policy_id=policy_id, at=at)

    def work_gate(self, *, username, policy_id, at, equipment=None, scene=None):
        return states.work_gate(self.conn, username=username, policy_id=policy_id,
                                at=at, equipment=equipment, scene=scene)

    # ------------------------------------------------------------- offline
    def issue_offline(self, *, username, attachment_id, at, ttl=None):
        # must currently see the attachment
        att = self.conn.execute(
            "SELECT pv.policy_id FROM attachments a JOIN policy_versions pv "
            "ON pv.id=a.policy_version_id WHERE a.id=?",
            (attachment_id,)).fetchone()
        if att is None:
            raise OfflineError("unknown attachment")
        r = eng.evaluate_policy(self.conn, username=username,
                                policy_id=att["policy_id"], at=at)
        if not r["applicable"]:
            raise PermissionError(f"FORBIDDEN ({r['winning_source']})")
        kw = {"username": username, "attachment_id": attachment_id, "at": at}
        if ttl is not None:
            kw["ttl"] = ttl
        return offmod.issue(self.conn, **kw)

    def redeem_offline(self, *, username, token, at, equipment=None, scene=None):
        return offmod.redeem(self.conn, username=username, token=token, at=at,
                             equipment=equipment, scene=scene)

    def my_offline_grants(self, *, username, at):
        return offmod.list_grants(self.conn, username, at)

    # ------------------------------------------------------- org lifecycle
    def transfer(self, *, username, to_position_code, at, keep_policy_ids=None):
        """调岗: move post (closing old interval) and revoke authorizations not
        explicitly carried to the new post. Rule epoch bump invalidates caches."""
        keep_policy_ids = set(keep_policy_ids or [])
        # map kept policy ids to their open auth ids for this user
        kept_auth_ids = {r["id"] for r in self.conn.execute(
            "SELECT id FROM work_authorizations WHERE user_id=(SELECT id FROM users"
            " WHERE username=?) AND policy_id IN (%s) AND valid_from<=? "
            "AND (valid_to IS NULL OR valid_to>?)" %
            (",".join("?" * len(keep_policy_ids)) if keep_policy_ids else "NULL"),
            (username, *keep_policy_ids, at, at)).fetchall()} if keep_policy_ids else set()
        rev = states.transfer_works(self.conn, username=username,
                                    keep_policy_ids=kept_auth_ids, at=at)
        org.transfer(self.conn, username, to_position_code, at)
        return {"transferred_to": to_position_code, "revoked_auth_ids": rev}

    def leave(self, *, username, at):
        """离职: close assignments, revoke authorizations, kill offline tokens."""
        uid = self.conn.execute("SELECT id FROM users WHERE username=?",
                                (username,)).fetchone()["id"]
        org.leave(self.conn, username, at)
        for r in self.conn.execute(
                "SELECT id FROM work_authorizations WHERE user_id=? AND valid_from<=? "
                "AND (valid_to IS NULL OR valid_to>?)", (uid, at, at)).fetchall():
            states.revoke_work(self.conn, auth_id=r["id"], at=at,
                               reason="离职撤销作业授权")
        offmod.revoke_all_for_user(self.conn, username, at)
        return {"left": username, "at": at}

    # --------------------------------------------------------- admin tools
    def rebuild_sets(self, at):
        return precompute.rebuild(self.conn, at=at)

    def compare_sets(self, *, position_code, at):
        return precompute.compare(self.conn, position_code=position_code, at=at)

    def historical_view(self, *, username, policy_id, at):
        """Trace the OLD policy at a historical point in time (full chain/text)."""
        r = eng.evaluate_policy(self.conn, username=username,
                                policy_id=policy_id, at=at)
        if r.get("version_id"):
            v = catalog.get_version(self.conn, r["version_id"])
            r["historical_body"] = v["body"]
            r["historical_content_hash"] = v["content_hash"]
        r["historical"] = True
        return r
