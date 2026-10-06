import unittest
from tests._helpers import fresh, ts
from policyhub import catalog, org
from policyhub.engine import (USER_DENY, USER_GRANT, POSITION_DENY,
                              POSITION_GRANT, DIRECT, INHERITED, NONE)


class TestPriorityAndChain(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.loto = self.ref["policies"]["LOTO"]
        self.ppe = self.ref["policies"]["PPE"]

    def test_direct_vs_inherited_nearest_ancestor(self):
        # ELEC inherits LOTO from MAINT (distance 1)
        r = catalog.current_version  # import touch
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        res = svc.derivation(username="alice", policy_id=self.loto,
                             at=ts("2026-09-01"), equipment="BREAKER",
                             scene="MAINTENANCE")
        self.assertEqual(res["winning_source"], "INHERITED")
        self.assertTrue(any(s.get("won") for s in res["chain"]))
        self.assertIn("维修部", " ".join(s.get("detail", "") for s in res["chain"]))

    def test_position_deny_beats_inherited_grant(self):
        # carol (OFFICE): PPE inherited from ROOT but OFFICE has POSITION_DENY
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        res = svc.derivation(username="carol", policy_id=self.ppe,
                             at=ts("2026-10-25"),  # personal grant expired
                             equipment="CHEMICAL", scene="HANDLING")
        self.assertEqual(res["decision"], "DENY")
        self.assertEqual(res["winning_source"], "POSITION_DENY")

    def test_personal_grant_is_explicit_narrow_exception_over_position_deny(self):
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        res = svc.derivation(username="carol", policy_id=self.ppe,
                             at=ts("2026-10-06"),
                             equipment="CHEMICAL", scene="HANDLING")
        self.assertEqual(res["decision"], "GRANT")
        self.assertEqual(res["winning_source"], "USER_GRANT")
        # chain shows the POSITION_DENY it defeated, with ordering
        sources = [s["source"] for s in res["chain"] if isinstance(s["source"], int)
                   and s["source"] < 90]
        self.assertEqual(sources, sorted(sources))
        self.assertIn(POSITION_DENY, sources)

    def test_personal_deny_highest(self):
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        res = svc.derivation(username="alice", policy_id=self.loto,
                             at=ts("2026-10-06"), equipment="BREAKER",
                             scene="MAINTENANCE")
        self.assertEqual(res["decision"], "DENY")
        self.assertEqual(res["winning_source"], "USER_DENY")

    def test_chain_is_complete_not_just_winner(self):
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        res = svc.derivation(username="carol", policy_id=self.ppe,
                             at=ts("2026-10-25"),
                             equipment="CHEMICAL", scene="HANDLING")
        details = " ".join(s["detail"] for s in res["chain"])
        # loser inherited grant is still explained, not hidden
        self.assertIn("岗位继承", details)
        self.assertEqual(sum(1 for s in res["chain"] if s.get("won")), 1)


class TestExceptionsAndWindows(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()

    def test_exception_expiry_acceptance(self):
        """验收: 例外到期 - 到期前形入，到期后回落到岗位规则。"""
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        ppe = self.ref["policies"]["PPE"]
        before = svc.derivation(username="carol", policy_id=ppe,
                                at=ts("2026-10-19"),
                                equipment="CHEMICAL", scene="HANDLING")
        after = svc.derivation(username="carol", policy_id=ppe,
                               at=ts("2026-10-20"),  # half-open: valid_to day
                               equipment="CHEMICAL", scene="HANDLING")
        self.assertEqual(before["winning_source"], "USER_GRANT")
        self.assertEqual(after["winning_source"], "POSITION_DENY")
        self.assertTrue(any("已过生效区间" in s["detail"] for s in after["chain"]))

    def test_revoked_exception_stops_applying(self):
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        ex = self.c.execute(
            "SELECT e.id FROM policy_exceptions e JOIN users u ON u.id=e.subject_id"
            " WHERE u.username='alice' AND e.grant=0").fetchone()["id"]
        catalog.revoke_exception(self.c, ex, ts("2026-10-10"))
        r = svc.derivation(username="alice", policy_id=self.ref["policies"]["LOTO"],
                           at=ts("2026-10-11"), equipment="BREAKER",
                           scene="MAINTENANCE")
        self.assertEqual(r["decision"], "GRANT")
        self.assertEqual(r["winning_source"], "INHERITED")

    def test_overlapping_validity_warns_and_picks_latest(self):
        """验收: 制度有效期重叠 - 选最新版本并给出冲突警告，而非静默拼接。"""
        p = catalog.create_policy(self.c, code="T-OVERLAP", title="重叠测试",
                                  category="t")
        a = catalog.add_version(self.c, policy_id=p, title="a", body="A",
                                valid_from=ts("2026-01-01"),
                                valid_to=ts("2026-12-31"), now=ts("2026-01-01"))
        b = catalog.add_version(self.c, policy_id=p, title="b", body="B",
                                valid_from=ts("2026-06-01"), valid_to=None,
                                now=ts("2026-06-01"))
        catalog.add_scope(self.c, version_id=a, position_code="ROOT")
        catalog.add_scope(self.c, version_id=b, position_code="ROOT")
        org.add_user(self.c, "u9", "用户九")
        org.assign(self.c, "u9", "ROOT", ts("2025-01-01"))
        from policyhub.engine import evaluate_policy
        r = evaluate_policy(self.c, username="u9", policy_id=p,
                            at=ts("2026-10-01"))
        self.assertEqual(r["version_no"], 2)
        self.assertTrue(any("有效期重叠" in w for w in r["warnings"]))

    def test_equipment_scene_filters(self):
        from policyhub.service import PolicyService
        svc = PolicyService(self.c)
        # v3 scope does not list LATHE
        hit = svc.derivation(username="alice", policy_id=self.ref["policies"]["LOTO"],
                             at=ts("2026-09-01"), equipment="BREAKER",
                             scene="MAINTENANCE")
        miss = svc.derivation(username="alice", policy_id=self.ref["policies"]["LOTO"],
                              at=ts("2026-09-01"), equipment="LATHE",
                              scene="MAINTENANCE")
        self.assertTrue(hit["applicable"])
        self.assertFalse(miss["applicable"])
        self.assertTrue(any("过滤不匹配" in s["detail"] for s in miss["chain"]))


if __name__ == "__main__":
    unittest.main()
