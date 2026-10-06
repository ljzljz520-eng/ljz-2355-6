import unittest
from tests._helpers import fresh, ts
from policyhub.service import PolicyService


class TestHistoricalTraceability(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.svc = PolicyService(self.c)
        self.loto = self.ref["policies"]["LOTO"]

    def test_old_policy_reconstructed_at_historical_point(self):
        """旧制度仍能按历史时点追溯: 2026-03 看到 v2 文本，2026-10 看到 v3。"""
        old = self.svc.historical_view(username="alice", policy_id=self.loto,
                                       at=ts("2026-03-15"))
        self.assertEqual(old["version_no"], 2)
        self.assertIn("v2", old["historical_body"])
        self.assertFalse(old["currently_effective"])

        cur = self.svc.historical_view(username="alice", policy_id=self.loto,
                                       at=ts("2026-10-06"))
        self.assertEqual(cur["version_no"], 3)
        self.assertIn("v3", cur["historical_body"])

    def test_historical_scope_and_exceptions_also_point_in_time(self):
        # carol's PPE personal grant only exists in October 2026
        before = self.svc.derivation(username="carol",
                                     policy_id=self.ref["policies"]["PPE"],
                                     at=ts("2026-09-01"),
                                     equipment="CHEMICAL", scene="HANDLING")
        during = self.svc.derivation(username="carol",
                                     policy_id=self.ref["policies"]["PPE"],
                                     at=ts("2026-10-06"),
                                     equipment="CHEMICAL", scene="HANDLING")
        self.assertEqual(before["winning_source"], "POSITION_DENY")
        self.assertEqual(during["winning_source"], "USER_GRANT")


if __name__ == "__main__":
    unittest.main()
