"""场景演示：冲突推导链、例外到期、三状态分离、离线有限期访问、公开页。"""
from datetime import datetime

from policy_hub import admin, db, documents, lifecycle, offline, precompute, resolver


def main():
    c = db.connect()
    mfg = admin.add_position(c, "MFG", "制造部")
    shop = admin.add_position(c, "WELD", "焊接车间", mfg)
    welder = admin.add_position(c, "WELDER", "焊工", shop)
    u = admin.add_user(c, "王焊", welder, datetime(2025, 6, 1))

    pol = admin.add_policy(c, "WELD-001", "焊接作业规程", "作业")
    admin.add_version(c, pol, 1, "v1 内容", datetime(2026, 1, 1), datetime(2026, 6, 1))
    admin.add_version(c, pol, 2, "v2 内容", datetime(2026, 3, 1))
    admin.add_rule(c, pol, "position", shop, "include", "base", datetime(2026, 1, 1))
    admin.add_rule(c, pol, "position", welder, "exclude", "exception",
                   datetime(2026, 1, 1), datetime(2026, 2, 1), note="停产检修豁免")

    print("=" * 70)
    print("1) 冲突裁决推导链（车间继承 include vs 本岗位例外 exclude，2026-01-15）")
    print("=" * 70)
    print(resolver.resolve_applicability(c, u, pol, "2026-01-15T00:00:00").explain())

    print()
    print("=" * 70)
    print("2) 例外到期后继承恢复（2026-03-01），且版本重叠裁定 v2")
    print("=" * 70)
    print(resolver.resolve_applicability(c, u, pol, "2026-03-01T00:00:00").explain())
    v, notes = resolver.resolve_version(c, pol, "2026-03-01T00:00:00")
    for s in notes:
        print(f"[{s.kind}] {s.text}")
    print(f"=> 生效版本：v{v['version_no']}")

    print()
    print("=" * 70)
    print("3) 三状态分离：签收 -> 培训 -> 授权，一步都不能省")
    print("=" * 70)
    now = datetime(2026, 4, 1)
    ack = lifecycle.begin_ack(c, u, pol, now)
    lifecycle.sign_ack(c, ack, now)
    print(f"仅签收后 check_authorization = {lifecycle.check_authorization(c, u, pol, now)}")
    rid = admin.add_training_resource(c, pol, "https://train.example/weld", datetime(2026, 1, 1))
    lifecycle.complete_training(c, u, pol, rid, now, score=92)
    lifecycle.grant_authorization(c, u, pol, now)
    print(f"签收+培训+授权后 check_authorization = {lifecycle.check_authorization(c, u, pol, now)}")
    admin.revoke_training_resource(c, rid)
    print(f"培训链接失效后 check_authorization = {lifecycle.check_authorization(c, u, pol, now)}")

    print()
    print("=" * 70)
    print("4) 离线附件有限期访问：离职即时作废")
    print("=" * 70)
    doc = admin.add_document(c, "焊接规程附件.pdf", policy_id=pol, equipment="焊机")
    token = offline.grant_offline(c, u, doc, now, ttl_seconds=3600)
    print(f"发放后校验：{offline.validate_offline(c, token, now)}")
    admin.depart_user(c, u, now)
    print(f"离职后校验：{offline.validate_offline(c, token, now)}")

    print()
    print("=" * 70)
    print("5) 预计算 vs 请求时求值（新增规则未重建时缓存变脏）")
    print("=" * 70)
    u2 = admin.add_user(c, "李焊", welder, datetime(2025, 6, 1))
    pol_new = admin.add_policy(c, "EHS-002", "受限空间作业规程", "作业")
    admin.add_version(c, pol_new, 1, "受限空间v1", datetime(2026, 1, 1))
    precompute.rebuild_precomputed(c, now)
    print(f"新鲜时差异：{precompute.compare(c, u2, now)['missing_in_precomputed'] or '无'}")
    admin.add_rule(c, pol_new, "position", welder, "include", "base", now)  # 变更未触发重建
    print(f"新增规则未重建，缓存漏算制度id：{precompute.compare(c, u2, now)['missing_in_precomputed']}")
    precompute.rebuild_precomputed(c, now)
    print(f"重建后差异：{precompute.compare(c, u2, now)['missing_in_precomputed'] or '无'}")

    print()
    print("=" * 70)
    print("6) 公开页只暴露白名单元数据")
    print("=" * 70)
    pub = admin.add_policy(c, "SAFE-001", "安全生产总则", "安全", is_public=True)
    admin.add_version(c, pub, 1, "总则", datetime(2025, 1, 1))
    for row in documents.public_catalog(c, now):
        print(row)


if __name__ == "__main__":
    main()
