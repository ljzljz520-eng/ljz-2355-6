import unittest
from tests._helpers import fresh, ts
from policyhub.service import PolicyService
from policyhub.offline import OfflineError


class TestTransferRevocationDeparture(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.svc = PolicyService(self.c)
        self.loto = self.ref["policies"]["LOTO"]

    def test_departed_user_old_cache_denied(self):
        """验收: 离职用户旧缓存 - 无有效任职即拒绝，不依赖客户端缓存。"""
        r = self.svc.derivation(username="dave", policy_id=self.loto,
                                at=ts("2026-10-06"), equipment="BREAKER",
                                scene="MAINTENANCE")
        self.assertEqual(r["decision"], "DENY")
        self.assertEqual(len(self.svc.my_policies(
            username="dave", at=ts("2026-10-06"))["items"]), 0)
        with self.assertRaises(PermissionError):
            self.svc.policy_body(username="dave", policy_id=self.loto,
                                 at=ts("2026-10-06"))

    def test_transfer_rederives_from_new_post(self):
        # alice transfers to OFFICE: loses inherited MAINT applicability to LOTO
        self.svc.transfer(username="alice", to_position_code="OFFICE",
                          at=ts("2026-11-10"))
        r = self.svc.derivation(username="alice", policy_id=self.loto,
                                at=ts("2026-11-11"), equipment="BREAKER",
                                scene="MAINTENANCE")
        self.assertEqual(r["decision"], "DENY")
        self.assertEqual(r["winning_source"], "NONE")

    def test_transfer_revokes_unkept_authorizations(self):
        from policyhub import states
        states.grant_work(self.c, username="alice", policy_id=self.loto,
                          at=ts("2026-09-01"))
        self.svc.transfer(username="alice", to_position_code="OFFICE",
                          at=ts("2026-11-10"))
        st = self.svc.status(username="alice", policy_id=self.loto,
                             at=ts("2026-11-11"))
        self.assertEqual(st["authorization"]["state"], "NONE")

    def test_cache_epoch_moves_on_transfer_and_revision(self):
        # build the per-position sets first -> request hits the precomputed cache
        self.svc.rebuild_sets(ts("2026-09-01"))
        before = self.svc.my_policies(username="alice", at=ts("2026-09-01"))["cache"]
        self.assertEqual(before["cache"], "hit")
        snap = before["snapshot_id"]
        # transfer changes the user's position, so the served snapshot id moves
        # from the ELEC set to the MECH set; the client's old id is stale.
        self.svc.transfer(username="alice", to_position_code="MECH",
                          at=ts("2026-09-15"))
        moved = self.svc.my_policies(username="alice", at=ts("2026-09-16"),
                                     client_snapshot_id=snap)["cache"]
        self.assertEqual(moved["cache"], "hit")
        self.assertTrue(moved["cache_stale"])
        self.assertEqual(moved["position_code"], "MECH")
        self.assertNotEqual(snap, moved["snapshot_id"])
        # a rule change (revision/exception) bumps the epoch; old sets go miss
        from policyhub import db
        old_epoch = int(db.get_meta(self.c, "rule_epoch"))
        self.svc.rebuild_sets(ts("2026-09-16"))
        newer = self.svc.my_policies(username="alice", at=ts("2026-09-16"),
                                     client_snapshot_id=moved["snapshot_id"])["cache"]
        self.assertEqual(newer["cache"], "hit")
        self.assertGreater(newer["rule_epoch"], old_epoch)


class TestOffline(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.svc = PolicyService(self.c)
        self.aid = self.ref["attachments"]["loto_v3_pdf"]

    def test_offline_redeem_within_window(self):
        g = self.svc.issue_offline(username="bob", attachment_id=self.aid,
                                   at=ts("2026-10-06"), ttl=30 * 24 * 3600)
        r = self.svc.redeem_offline(username="bob", token=g["token"],
                                    at=ts("2026-10-07"),
                                    equipment="MOTOR", scene="REPAIR")
        self.assertEqual(r["filename"], "LOTO-v3-checklist.pdf")

    def test_offline_expires(self):
        g = self.svc.issue_offline(username="bob", attachment_id=self.aid,
                                   at=ts("2026-10-06"), ttl=3600)
        with self.assertRaises(OfflineError) as cm:
            self.svc.redeem_offline(username="bob", token=g["token"],
                                    at=ts("2026-10-08"),
                                    equipment="MOTOR", scene="REPAIR")
        self.assertIn("EXPIRED", str(cm.exception))

    def test_offline_cut_by_visibility_change_even_if_token_valid(self):
        """离线附件限期访问: 例外失效/调岗后即便令牌未到期也拒绝。"""
        g = self.svc.issue_offline(username="bob", attachment_id=self.aid,
                                   at=ts("2026-10-06"), ttl=60 * 24 * 3600)
        # bob's personal GRANT ends 11-01; he is MECH (would still inherit MAINT).
        # move him to OFFICE first so inheritance also disappears
        self.svc.transfer(username="bob", to_position_code="OFFICE",
                          at=ts("2026-10-15"))
        with self.assertRaises(OfflineError) as cm:
            self.svc.redeem_offline(username="bob", token=g["token"],
                                    at=ts("2026-11-03"),
                                    equipment="MOTOR", scene="REPAIR")
        self.assertIn("NO_LONGER_VISIBLE", str(cm.exception))

    def test_offline_revoked_on_departure(self):
        g = self.svc.issue_offline(username="carol",
            attachment_id=self.ref["attachments"]["emg_map"],
            at=ts("2026-10-06"), ttl=999999)
        self.svc.leave(username="carol", at=ts("2026-10-10"))
        with self.assertRaises(OfflineError) as cm:
            self.svc.redeem_offline(username="carol", token=g["token"],
                                    at=ts("2026-10-11"))
        self.assertIn("REVOKED", str(cm.exception))

    def test_offline_token_bound_to_owner(self):
        g = self.svc.issue_offline(username="bob", attachment_id=self.aid,
                                   at=ts("2026-10-06"))
        with self.assertRaises(OfflineError):
            self.svc.redeem_offline(username="alice", token=g["token"],
                                    at=ts("2026-10-06"),
                                    equipment="MOTOR", scene="REPAIR")


if __name__ == "__main__":
    unittest.main()
