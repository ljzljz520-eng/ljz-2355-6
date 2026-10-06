import unittest
from tests._helpers import fresh, ts
from policyhub import catalog, precompute
from policyhub.service import PolicyService


class TestPrecomputeVsRequestTime(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.svc = PolicyService(self.c)

    def test_no_drift_after_rebuild(self):
        """按岗位预计算适用集 vs 请求时求值：一致（零漂移）。"""
        at = ts("2026-10-06")
        self.svc.rebuild_sets(at)
        for code in ("ELEC", "MECH", "OFFICE", "MAINT", "ROOT"):
            rep = self.svc.compare_sets(position_code=code, at=at)
            self.assertEqual(rep["diffs"], [], f"drift for {code}: {rep['diffs']}")
            self.assertFalse(rep["stale"])

    def test_stale_snapshot_detected_after_rule_change(self):
        at = ts("2026-10-06")
        self.svc.rebuild_sets(at)
        # publish a brand new policy -> epoch bumps -> old snapshot stale
        p = catalog.create_policy(self.c, code="NEW-9", title="新制度", category="x")
        v = catalog.add_version(self.c, policy_id=p, title="n", body="n",
                                valid_from=ts("2026-10-01"), now=ts("2026-10-01"))
        catalog.add_scope(self.c, version_id=v, position_code="ROOT")
        rep = self.svc.compare_sets(position_code="ROOT", at=at)
        self.assertTrue(rep["stale"])
        self.svc.rebuild_sets(at)
        self.assertFalse(self.svc.compare_sets(position_code="ROOT", at=at)["stale"])

    def test_precompute_cannot_encode_personal_exceptions(self):
        """预计算只折叠岗位事实；个人 USER_* 例外只在请求时出现。"""
        at = ts("2026-10-06")
        self.svc.rebuild_sets(at)
        # position-only baseline for OFFICE on PPE = POSITION_DENY
        snap = precompute.get_snapshot(
            self.c, position_id=self.c.execute(
                "SELECT id FROM positions WHERE code='OFFICE'").fetchone()["id"])
        ppe = next(p for p in snap["policies"] if p["policy_id"]
                   == self.ref["policies"]["PPE"])
        self.assertEqual(ppe["decision"], "DENY")
        # but carol at request time is granted via her USER_GRANT exception
        live = self.svc.derivation(username="carol",
                                   policy_id=self.ref["policies"]["PPE"], at=at,
                                   equipment="CHEMICAL", scene="HANDLING")
        self.assertEqual(live["decision"], "GRANT")
        self.assertEqual(live["winning_source"], "USER_GRANT")


if __name__ == "__main__":
    unittest.main()
