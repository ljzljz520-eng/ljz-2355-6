"""验收测试：例外到期、有效期重叠、签收时版本更新、离职旧缓存、培训链接失效、
历史时点追溯、公开页元数据最小化、项目不自动生成安全规则，
以及调岗/撤权、三状态分离、推导链、预计算与请求时求值对比。"""
import unittest
from datetime import datetime

from policy_hub import admin, db, documents, lifecycle, offline, precompute, projects, resolver


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()
        c = self.conn
        # 岗位树：制造部 -> {焊接车间 -> 焊工, 装配车间 -> 装配工}
        self.p_mfg = admin.add_position(c, "MFG", "制造部")
        self.p_weld_shop = admin.add_position(c, "WELD", "焊接车间", self.p_mfg)
        self.p_welder = admin.add_position(c, "WELDER", "焊工", self.p_weld_shop)
        self.p_asm_shop = admin.add_position(c, "ASM", "装配车间", self.p_mfg)
        self.p_assembler = admin.add_position(c, "ASSEMBLER", "装配工", self.p_asm_shop)
        hire = datetime(2025, 6, 1)
        self.u_welder = admin.add_user(c, "王焊", self.p_welder, hire)
        self.u_asm = admin.add_user(c, "李装", self.p_assembler, hire)
        self.u_temp = admin.add_user(c, "张临时", self.p_welder, hire)
        # 制度一：安全总则（公开，全制造部适用）
        self.pol_safe = admin.add_policy(c, "SAFE-001", "安全生产总则", "安全", is_public=True)
        admin.add_version(c, self.pol_safe, 1, "总则v1内容", datetime(2025, 1, 1))
        admin.add_rule(c, self.pol_safe, "position", self.p_mfg, "include", "base",
                       datetime(2025, 1, 1))
        # 制度二：焊接作业规程（内部；v1/v2 有效期重叠）
        self.pol_weld = admin.add_policy(c, "WELD-001", "焊接作业规程", "作业")
        admin.add_version(c, self.pol_weld, 1, "焊接规程v1",
                          datetime(2026, 1, 1), datetime(2026, 6, 1))
        admin.add_version(c, self.pol_weld, 2, "焊接规程v2", datetime(2026, 3, 1))
        admin.add_rule(c, self.pol_weld, "position", self.p_weld_shop, "include", "base",
                       datetime(2026, 1, 1))
        # 制度三：内部保密制度（仅装配工）
        self.pol_secret = admin.add_policy(c, "SEC-001", "内部保密制度", "保密")
        admin.add_version(c, self.pol_secret, 1, "保密v1", datetime(2025, 1, 1))
        admin.add_rule(c, self.pol_secret, "position", self.p_assembler, "include", "base",
                       datetime(2025, 1, 1))

    def tearDown(self):
        self.conn.close()


class TestExceptionExpiry(Base):
    """验收①：例外到期后，岗位继承自动恢复，推导链保留例外痕迹。"""

    def test_exception_expires_and_inheritance_resumes(self):
        c = self.conn
        admin.add_rule(c, self.pol_weld, "user", self.u_temp, "exclude", "exception",
                       datetime(2026, 1, 1), datetime(2026, 2, 1), note="临时调岗豁免")
        during = resolver.resolve_applicability(c, self.u_temp, self.pol_weld,
                                                "2026-01-15T00:00:00")
        self.assertFalse(during.applicable)
        self.assertTrue(any(s.kind == "winner" and "例外" in s.text for s in during.chain))
        after = resolver.resolve_applicability(c, self.u_temp, self.pol_weld,
                                               "2026-03-01T00:00:00")
        self.assertTrue(after.applicable)
        self.assertTrue(any(s.kind == "expired" for s in after.chain))  # 过期例外仍可见于推导链


class TestVersionOverlap(Base):
    """验收②：制度有效期重叠时确定性裁定，且历史时点取历史版本。"""

    def test_overlap_resolved_deterministically(self):
        v, notes = resolver.resolve_version(self.conn, self.pol_weld, "2026-04-01T00:00:00")
        self.assertEqual(v["version_no"], 2)
        self.assertTrue(any("重叠" in s.text for s in notes))
        v1, _ = resolver.resolve_version(self.conn, self.pol_weld, "2026-02-01T00:00:00")
        self.assertEqual(v1["version_no"], 1)


class TestAckVersionChange(Base):
    """验收③：签收途中版本更新，旧签收作废，必须基于新版本重新签收。"""

    def test_sign_after_new_version_is_rejected(self):
        c = self.conn
        ack_id = lifecycle.begin_ack(c, self.u_asm, self.pol_secret, datetime(2026, 2, 15))
        v1 = c.execute("SELECT * FROM policy_versions WHERE policy_id=? AND version_no=1",
                       (self.pol_secret,)).fetchone()
        admin.add_version(c, self.pol_secret, 2, "保密v2", datetime(2026, 3, 1))
        admin.set_version_effective_to(c, v1["id"], datetime(2026, 3, 1))
        with self.assertRaises(lifecycle.VersionChangedError):
            lifecycle.sign_ack(c, ack_id, datetime(2026, 3, 2))
        self.assertEqual(c.execute("SELECT status FROM acks WHERE id=?",
                                   (ack_id,)).fetchone()["status"], "stale")
        ack2 = lifecycle.begin_ack(c, self.u_asm, self.pol_secret, datetime(2026, 3, 2))
        lifecycle.sign_ack(c, ack2, datetime(2026, 3, 3))
        self.assertEqual(c.execute("SELECT status FROM acks WHERE id=?",
                                   (ack2,)).fetchone()["status"], "signed")


class TestThreeStatesDistinct(Base):
    """一次点击阅读 ≠ 培训完成 ≠ 作业授权。"""

    def test_click_read_does_not_imply_training_or_authorization(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        ack = lifecycle.begin_ack(c, self.u_welder, self.pol_weld, now)
        lifecycle.sign_ack(c, ack, now)
        self.assertFalse(lifecycle.check_authorization(c, self.u_welder, self.pol_weld, now))
        with self.assertRaises(lifecycle.PrerequisiteError):
            lifecycle.grant_authorization(c, self.u_welder, self.pol_weld, now)
        rid = admin.add_training_resource(c, self.pol_weld, "https://train.example/weld",
                                          datetime(2026, 1, 1))
        lifecycle.complete_training(c, self.u_welder, self.pol_weld, rid, now, score=95)
        lifecycle.grant_authorization(c, self.u_welder, self.pol_weld, now)
        self.assertTrue(lifecycle.check_authorization(c, self.u_welder, self.pol_weld, now))


class TestTrainingLinkInvalid(Base):
    """验收⑤：培训链接失效后，不能登记培训、不能新授权，已发授权作业校验立即失败。"""

    def test_dead_link_blocks_and_reclaims_authorization(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        rid = admin.add_training_resource(c, self.pol_weld, "https://train.example/weld",
                                          datetime(2026, 1, 1))
        ack = lifecycle.begin_ack(c, self.u_welder, self.pol_weld, now)
        lifecycle.sign_ack(c, ack, now)
        lifecycle.complete_training(c, self.u_welder, self.pol_weld, rid, now)
        lifecycle.grant_authorization(c, self.u_welder, self.pol_weld, now)
        self.assertTrue(lifecycle.check_authorization(c, self.u_welder, self.pol_weld, now))
        admin.revoke_training_resource(c, rid)
        self.assertFalse(lifecycle.check_authorization(c, self.u_welder, self.pol_weld, now))
        with self.assertRaises(lifecycle.PrerequisiteError):
            lifecycle.grant_authorization(c, self.u_welder, self.pol_weld, now)
        with self.assertRaises(lifecycle.PrerequisiteError):
            lifecycle.complete_training(c, self.u_asm, self.pol_weld, rid, now)


class TestOfflineGrant(Base):
    """验收④：离职用户旧缓存即时作废；离线令牌有有限期；调岗回收可见性。"""

    def setUp(self):
        super().setUp()
        self.doc = admin.add_document(self.conn, "焊接规程附件.pdf", policy_id=self.pol_weld,
                                      equipment="焊机", scenario="动火")

    def test_departed_user_token_rejected(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        token = offline.grant_offline(c, self.u_welder, self.doc, now, ttl_seconds=7 * 24 * 3600)
        self.assertTrue(offline.validate_offline(c, token, now).allowed)
        admin.depart_user(c, self.u_welder, now)
        v = offline.validate_offline(c, token, now)
        self.assertFalse(v.allowed)
        self.assertIn("离职", v.reason)

    def test_token_has_finite_ttl(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        token = offline.grant_offline(c, self.u_welder, self.doc, now, ttl_seconds=3600)
        self.assertFalse(offline.validate_offline(c, token, datetime(2026, 4, 2)).allowed)

    def test_transfer_reclaims_offline_visibility(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        token = offline.grant_offline(c, self.u_welder, self.doc, now, ttl_seconds=7 * 24 * 3600)
        admin.transfer_user(c, self.u_welder, self.p_assembler, now)
        self.assertFalse(offline.validate_offline(c, token, now).allowed)


class TestTransferAndHistory(Base):
    """调岗请求时立即生效；旧制度按历史时点追溯（当时岗位 + 当时版本）。"""

    def test_transfer_immediate_and_historical_trace(self):
        c = self.conn
        admin.transfer_user(c, self.u_welder, self.p_assembler, datetime(2026, 4, 1))
        self.assertFalse(resolver.resolve_applicability(
            c, self.u_welder, self.pol_weld, "2026-04-02T00:00:00").applicable)
        self.assertTrue(resolver.resolve_applicability(
            c, self.u_welder, self.pol_weld, "2026-03-01T00:00:00").applicable)
        v, _ = resolver.resolve_version(c, self.pol_weld, "2026-02-01T00:00:00")
        self.assertEqual(v["content"], "焊接规程v1")


class TestRevokeAuthorization(Base):
    """撤权立即生效。"""

    def test_revoke(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        rid = admin.add_training_resource(c, self.pol_weld, "https://train.example/weld",
                                          datetime(2026, 1, 1))
        ack = lifecycle.begin_ack(c, self.u_welder, self.pol_weld, now)
        lifecycle.sign_ack(c, ack, now)
        lifecycle.complete_training(c, self.u_welder, self.pol_weld, rid, now)
        auth = lifecycle.grant_authorization(c, self.u_welder, self.pol_weld, now)
        self.assertTrue(lifecycle.check_authorization(c, self.u_welder, self.pol_weld, now))
        lifecycle.revoke_authorization(c, auth, now)
        self.assertFalse(lifecycle.check_authorization(c, self.u_welder, self.pol_weld, now))


class TestPrecomputeVsRequest(Base):
    """预计算适用集与请求时求值：新鲜时一致；变更未重建时可检测脏缓存；
    用户级例外只在请求时生效（决策点必须走请求时）。"""

    def test_fresh_cache_matches_and_stale_detected(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        precompute.rebuild_precomputed(c, now)
        diff = precompute.compare(c, self.u_welder, now)
        self.assertEqual(diff["missing_in_precomputed"], set())
        self.assertEqual(diff["stale_in_precomputed"], set())
        admin.add_rule(c, self.pol_secret, "position", self.p_welder, "include", "base", now)
        diff2 = precompute.compare(c, self.u_welder, now)
        self.assertIn(self.pol_secret, diff2["missing_in_precomputed"])
        precompute.rebuild_precomputed(c, now)
        self.assertEqual(precompute.compare(c, self.u_welder, now)["missing_in_precomputed"], set())

    def test_user_exceptions_only_at_request_time(self):
        c = self.conn
        now = datetime(2026, 1, 15)
        admin.add_rule(c, self.pol_weld, "user", self.u_temp, "exclude", "exception",
                       datetime(2026, 1, 1), datetime(2026, 2, 1))
        precompute.rebuild_precomputed(c, now)
        diff = precompute.compare(c, self.u_temp, now)
        self.assertIn(self.pol_weld, diff["stale_in_precomputed"])


class TestDerivationChain(Base):
    """冲突必须裁出唯一结论并展示推导链，而不是把命中条款全抛给员工。"""

    def test_same_scope_exception_beats_base(self):
        c = self.conn
        admin.add_rule(c, self.pol_weld, "position", self.p_welder, "include", "base",
                       datetime(2026, 1, 1))
        admin.add_rule(c, self.pol_weld, "position", self.p_welder, "exclude", "exception",
                       datetime(2026, 1, 1), note="停产检修")
        res = resolver.resolve_applicability(c, self.u_welder, self.pol_weld,
                                             "2026-02-01T00:00:00")
        self.assertFalse(res.applicable)
        kinds = {s.kind for s in res.chain}
        self.assertIn("winner", kinds)
        self.assertIn("superseded", kinds)
        text = res.explain()
        self.assertIn("胜出", text)
        self.assertIn("让位", text)

    def test_user_level_beats_position_exception(self):
        c = self.conn
        admin.add_rule(c, self.pol_weld, "position", self.p_welder, "exclude", "exception",
                       datetime(2026, 1, 1))
        admin.add_rule(c, self.pol_weld, "user", self.u_welder, "include", "base",
                       datetime(2026, 1, 1))
        res = resolver.resolve_applicability(c, self.u_welder, self.pol_weld,
                                             "2026-02-01T00:00:00")
        self.assertTrue(res.applicable)


class TestPublicCatalog(Base):
    """验收⑦：公开页只呈现有权看到的白名单元数据。"""

    def test_only_public_metadata(self):
        cat = documents.public_catalog(self.conn, datetime(2026, 4, 1))
        codes = {row["code"] for row in cat}
        self.assertIn("SAFE-001", codes)
        self.assertNotIn("SEC-001", codes)
        self.assertNotIn("WELD-001", codes)
        for row in cat:
            self.assertEqual(set(row.keys()), {"code", "title", "category", "effective_from"})


class TestProjectNoAutoRules(Base):
    """验收⑧：项目不自动生成新的作业安全规则。"""

    def test_project_creation_generates_nothing(self):
        c = self.conn
        n_rules = c.execute("SELECT COUNT(*) n FROM rules").fetchone()["n"]
        n_auth = c.execute("SELECT COUNT(*) n FROM authorizations").fetchone()["n"]
        projects.create_project(c, "新项目-冲压线", equipment="焊机")
        self.assertEqual(c.execute("SELECT COUNT(*) n FROM rules").fetchone()["n"], n_rules)
        self.assertEqual(c.execute("SELECT COUNT(*) n FROM authorizations").fetchone()["n"], n_auth)


class TestDocSearch(Base):
    """文档站按岗位/设备/场景过滤，并叠加权限可见范围。"""

    def test_filter_and_visibility(self):
        c = self.conn
        now = datetime(2026, 4, 1)
        d1 = admin.add_document(c, "焊机点检表", policy_id=self.pol_weld, equipment="焊机",
                                scenario="动火", position_id=self.p_welder)
        d2 = admin.add_document(c, "装配手册", policy_id=self.pol_secret, equipment="扳手",
                                scenario="装配", position_id=self.p_assembler)
        docs = documents.search_documents(c, self.u_welder, now, equipment="焊机")
        self.assertEqual([d["id"] for d in docs], [d1])
        ids = {d["id"] for d in documents.search_documents(c, self.u_asm, now)}
        self.assertIn(d2, ids)
        self.assertNotIn(d1, ids)


if __name__ == "__main__":
    unittest.main()
