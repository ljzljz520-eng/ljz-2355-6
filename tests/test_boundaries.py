import os
import unittest
from tests._helpers import fresh, ts
from policyhub.publicsite import public_listing
from policyhub.service import PolicyService


class TestPublicBoundary(unittest.TestCase):
    def setUp(self):
        self.c, self.ref = fresh()
        self.svc = PolicyService(self.c)
        self.at = ts("2026-10-06")

    def test_public_page_only_whitelisted_metadata(self):
        """公开页只呈现有权看到的元数据：仅 EMG，无正文/范围/例外/附件。"""
        items = public_listing(self.c, at=self.at)
        codes = {i["code"] for i in items}
        self.assertEqual(codes, {"EMG-03"})
        for i in items:
            self.assertEqual(set(i.keys()),
                             {"code", "title", "category", "version_no",
                              "valid_from", "valid_to"})
            self.assertNotIn("body", i)
            self.assertNotIn("content_hash", i)

    def test_non_public_policy_body_forbidden_even_with_meta_endpoint(self):
        with self.assertRaises(PermissionError):
            self.svc.policy_body(username="carol",
                                 policy_id=self.ref["policies"]["LOTO"],
                                 at=self.at)

    def test_visible_items_carry_chain_not_undifferentiated_clauses(self):
        res = self.svc.my_policies(username="bob", at=self.at,
                                   equipment="MOTOR", scene="REPAIR")
        loto = next(i for i in res["items"]
                    if i["policy_id"] == self.ref["policies"]["LOTO"])
        self.assertTrue(loto["derivation_chain"])
        self.assertTrue(any(s.get("won") for s in loto["derivation_chain"]))


class TestNoAutoGeneration(unittest.TestCase):
    """项目不自动生成新的作业安全规则。"""

    SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_no_generation_endpoint_exists(self):
        import pathlib
        api = pathlib.Path(self.SOURCE_DIR, "policyhub", "api.py").read_text()
        for needle in ("auto", "generate", "generate_rule", "synthesize",
                       "infer_rule", "ai_", "llm"):
            self.assertNotIn(needle, api.lower().replace("generation_endpoint", ""),
                             f"API must not expose rule generation ({needle})")

    def test_every_policy_records_human_author(self):
        from policyhub import db
        c, ref = fresh()
        rows = c.execute(
            "SELECT code, created_by, published_by FROM policies p "
            "JOIN policy_versions pv ON pv.policy_id=p.id").fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r["created_by"])
            self.assertTrue(r["published_by"])
            self.assertNotIn("auto", r["created_by"].lower())

    def test_catalog_is_only_insert_path_for_policy_content(self):
        import pathlib
        mods = ["engine.py", "states.py", "offline.py", "precompute.py",
                "publicsite.py", "api.py"]
        for m in mods:
            txt = pathlib.Path(self.SOURCE_DIR, "policyhub", m).read_text()
            self.assertNotIn("INSERT INTO policies", txt,
                             f"{m} must not author policy rows")
            self.assertNotIn("INSERT INTO policy_versions", txt,
                             f"{m} must not author versions")


if __name__ == "__main__":
    unittest.main()
