import unittest
from tests._helpers import fresh, ts
from policyhub import catalog, states
from policyhub.service import PolicyService
from policyhub.states import StateError


class TestThreeStates(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.svc = PolicyService(self.c)
        self.loto = self.ref["policies"]["LOTO"]
        self.v3 = self.ref["versions"]["loto_v3"]

    def test_reading_does_not_grant_training_or_work(self):
        # bob has oct grant; just opening+ack must not mark trained/authorized
        sess = self.svc.open_read(username="bob", policy_id=self.loto,
                                  at=ts("2026-10-06"))
        ack = self.svc.acknowledge(username="bob", token=sess["token"],
                                   at=ts("2026-10-06"))
        st = self.svc.status(username="bob", policy_id=self.loto,
                             at=ts("2026-10-06"))
        self.assertEqual(st["acknowledgement"]["state"], "ACKED")
        self.assertEqual(st["training"]["state"], "NONE")
        self.assertEqual(st["authorization"]["state"], "NONE")
        gate = self.svc.work_gate(username="bob", policy_id=self.loto,
                                  at=ts("2026-10-06"),
                                  equipment="MOTOR", scene="REPAIR")
        self.assertFalse(gate["allowed"])
        self.assertFalse(gate["gates"]["trained"]["ok"])
        self.assertFalse(gate["gates"]["authorized"]["ok"])

    def test_full_path_opens_gate(self):
        at = ts("2026-11-05")  # alice's personal deny over
        # redo for v3
        sess = self.svc.open_read(username="alice", policy_id=self.loto, at=at)
        self.svc.acknowledge(username="alice", token=sess["token"], at=at)
        link = states.issue_training_link(self.c, version_id=self.v3, at=at)
        self.svc.complete_training(username="alice", token=link["token"], at=at)
        gate = self.svc.work_gate(username="alice", policy_id=self.loto, at=at,
                                  equipment="BREAKER", scene="REPAIR")
        self.assertTrue(gate["allowed"], gate)

    def test_ack_at_signing_time_version_updated_is_rejected(self):
        """验收: 签收时版本更新 - 用旧版本读会话签收必须 STALE_VERSION。"""
        # employee opens the-then-current v3 reading session
        sess = self.svc.open_read(username="alice", policy_id=self.loto,
                                  at=ts("2026-11-05"))
        # before she submits the signature, v4 is published (revision)
        v4 = catalog.add_version(
            self.c, policy_id=self.loto, title="v4", body="新文本",
            valid_from=ts("2026-11-05"), valid_to=None,
            author="safety", now=ts("2026-11-05"))
        catalog.add_scope(self.c, version_id=v4, position_code="MAINT",
                          equipment_codes=["*"], scene_codes=["*"])
        with self.assertRaises(StateError) as cm:
            self.svc.acknowledge(username="alice", token=sess["token"],
                                 at=ts("2026-11-05") + 60)
        self.assertIn("STALE_VERSION", str(cm.exception))
        # must re-sign for v4
        sess2 = self.svc.open_read(username="alice", policy_id=self.loto,
                                   at=ts("2026-11-05") + 120)
        out = self.svc.acknowledge(username="alice", token=sess2["token"],
                                   at=ts("2026-11-05") + 120)
        self.assertEqual(out["version_no"], 4)

    def test_revised_version_makes_old_ack_and_training_stale(self):
        # alice acked+trained v2 in seed; at 2026-10 v3 is current
        st = self.svc.status(username="alice", policy_id=self.loto,
                             at=ts("2026-10-06"))
        self.assertEqual(st["acknowledgement"]["state"], "STALE")
        self.assertEqual(st["training"]["state"], "STALE")

    def test_read_session_single_use_and_expiry(self):
        sess = self.svc.open_read(username="bob", policy_id=self.loto,
                                  at=ts("2026-10-06"))
        self.svc.acknowledge(username="bob", token=sess["token"],
                             at=ts("2026-10-06"))
        with self.assertRaises(StateError):
            self.svc.acknowledge(username="bob", token=sess["token"],
                                 at=ts("2026-10-06"))

    def test_training_link_single_use_expiry_invalid(self):
        """验收: 培训链接失效 - 过期/已用/伪造均拒绝，且不产生培训完成。"""
        link = states.issue_training_link(self.c, version_id=self.v3,
                                          at=ts("2026-10-06"), ttl=3600)
        with self.assertRaises(StateError):
            self.svc.complete_training(username="bob", token=link["token"],
                                       at=ts("2026-10-07"))
        self.assertEqual(
            self.svc.status(username="bob", policy_id=self.loto,
                            at=ts("2026-10-07"))["training"]["state"], "NONE")
        # forged token
        with self.assertRaises(StateError):
            self.svc.complete_training(username="bob", token="nope",
                                       at=ts("2026-10-06"))

    def test_authorization_independently_revocable(self):
        states.grant_work(self.c, username="bob", policy_id=self.loto,
                          at=ts("2026-10-06"))
        auth = self.c.execute(
            "SELECT id FROM work_authorizations WHERE user_id=(SELECT id FROM users"
            " WHERE username='bob')").fetchone()["id"]
        states.revoke_work(self.c, auth_id=auth, at=ts("2026-10-08"),
                           reason="暂停")
        st = self.svc.status(username="bob", policy_id=self.loto,
                             at=ts("2026-10-09"))
        self.assertEqual(st["authorization"]["state"], "NONE")


if __name__ == "__main__":
    unittest.main()
