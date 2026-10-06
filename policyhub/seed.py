"""Demo / acceptance fixture. Human-authored rules only; nothing here is
auto-generated. All windows are anchored around 2026-10-06."""
from __future__ import annotations

from . import catalog, db, org, states
from .clock import ts, Clock


def build(conn, *, now=ts("2026-10-06")):
    # --- position tree (岗位继承) ---
    org.add_position(conn, "ROOT", "公司", None)
    org.add_position(conn, "OPS", "运营中心", "ROOT")
    org.add_position(conn, "MAINT", "维修部", "OPS")
    org.add_position(conn, "ELEC", "电气班组", "MAINT")
    org.add_position(conn, "MECH", "机械班组", "MAINT")
    org.add_position(conn, "OFFICE", "行政办公室", "ROOT")

    # --- users ---
    org.add_user(conn, "alice", "张爱电")     # electrician
    org.add_user(conn, "bob", "李机修")       # mechanic
    org.add_user(conn, "carol", "王行政")     # office
    org.add_user(conn, "dave", "赵离职")      # left 2026-09-01

    org.assign(conn, "alice", "ELEC", ts("2025-01-01"))
    org.assign(conn, "bob", "MECH", ts("2025-01-01"))
    org.assign(conn, "carol", "OFFICE", ts("2025-01-01"))
    org.assign(conn, "dave", "ELEC", ts("2025-01-01"),
               valid_to=ts("2026-09-01"))  # 离职

    # --- policy 1: LOTO, three revisions (old ones retained for history) ---
    p1 = catalog.create_policy(conn, code="LOTO-01", title="上锁挂牌作业安全制度",
                               category="作业安全", is_system=True)
    v1 = catalog.add_version(
        conn, policy_id=p1, title="上锁挂牌 v1",
        body="v1: 断电、上锁、挂牌后作业（2025 初版，文字从简）。",
        valid_from=ts("2025-01-01"), valid_to=ts("2026-01-01"),
        author="safety.zhang", now=ts("2025-01-01"))
    catalog.add_scope(conn, version_id=v1, position_code="MAINT",
                      equipment_codes=["*"], scene_codes=["MAINTENANCE"])
    v2 = catalog.add_version(
        conn, policy_id=p1, title="上锁挂牌 v2",
        body="v2: 增加双人复核与钥匙集中管理要求。",
        valid_from=ts("2026-01-01"), valid_to=ts("2026-08-01"),
        author="safety.zhang", now=ts("2026-01-01"))
    catalog.add_scope(conn, version_id=v2, position_code="MAINT",
                      equipment_codes=["*"], scene_codes=["MAINTENANCE"])
    v3 = catalog.add_version(
        conn, policy_id=p1, title="上锁挂牌 v3",
        body="v3: 能量隔离清单 + 双人复核 + 试启动验证 + 恢复确认。",
        valid_from=ts("2026-08-01"), valid_to=None,
        author="safety.zhang", now=ts("2026-08-01"))
    catalog.add_scope(conn, version_id=v3, position_code="MAINT",
                      equipment_codes=["BREAKER", "MOTOR", "PRESS"],
                      scene_codes=["MAINTENANCE", "REPAIR"])
    # attachment on the current version
    a1 = catalog.add_attachment(conn, version_id=v3, filename="LOTO-v3-checklist.pdf",
                                content=b"%PDF-1.4 fake checklist v3")
    # bounded exceptions on v3
    catalog.add_exception(conn, version_id=v3, subject_type="USER",
                          subject_code="bob", grant=1,
                          reason="跨岗支援电气检修的临时形入，限 10 月",
                          valid_from=ts("2026-10-01"),
                          valid_to=ts("2026-11-01"))
    catalog.add_exception(conn, version_id=v3, subject_type="USER",
                          subject_code="alice", grant=0,
                          reason="再培训完成前暂停高压柜作业（限期）",
                          valid_from=ts("2026-10-01"),
                          valid_to=ts("2026-10-31"))

    # --- policy 2: PPE for chemical handling; office denied at post level ---
    p2 = catalog.create_policy(conn, code="PPE-02", title="化学品搬运防护制度",
                               category="职业健康")
    pv = catalog.add_version(
        conn, policy_id=p2, title="PPE v1",
        body="v1: 搬运化学品须穿戴防化手套、护目镜与防护服。",
        valid_from=ts("2025-06-01"), valid_to=None,
        author="safety.li", now=ts("2025-06-01"))
    catalog.add_scope(conn, version_id=pv, position_code="ROOT",
                      equipment_codes=["CHEMICAL"], scene_codes=["HANDLING"])
    catalog.add_exception(conn, version_id=pv, subject_type="POSITION",
                          subject_code="OFFICE", grant=0,
                          reason="行政岗位不承担化学品搬运，排除以防误签收",
                          valid_from=ts("2025-06-01"), valid_to=None)
    catalog.add_exception(conn, version_id=pv, subject_type="USER",
                          subject_code="carol", grant=1,
                          reason="carol 本月兼任应急物资盘点，需查阅 PPE（限期）",
                          valid_from=ts("2026-10-01"),
                          valid_to=ts("2026-10-20"))

    # --- policy 3: public metadata only (body still gated) ---
    p3 = catalog.create_policy(conn, code="EMG-03", title="应急疏散指引",
                               category="应急", public_meta=True)
    ev = catalog.add_version(
        conn, policy_id=p3, title="应急疏散 v1",
        body="v1: 听到警报后按就近疏散通道撤离，于集合点点名。",
        valid_from=ts("2025-01-01"), valid_to=None,
        author="safety.wang", now=ts("2025-01-01"))
    catalog.add_scope(conn, version_id=ev, position_code="ROOT",
                      equipment_codes=["*"], scene_codes=["*"])
    a2 = catalog.add_attachment(conn, version_id=ev, filename="evacuation-map.png",
                                content=b"\x89PNG fake evacuation map")

    # --- existing lifecycle facts for alice on the OLD LOTO v2 ---
    states.open_read_session(conn, username="alice", version_id=v2,
                             at=ts("2026-03-01"), ttl=90*24*3600)
    tok = conn.execute(
        "SELECT token FROM ack_sessions WHERE user_id=(SELECT id FROM users"
        " WHERE username='alice') AND policy_version_id=?", (v2,)).fetchone()["token"]
    states.acknowledge(conn, username="alice", token=tok, at=ts("2026-03-02"))
    link = states.issue_training_link(conn, version_id=v2, at=ts("2026-03-02"))
    states.complete_training(conn, username="alice", token=link["token"],
                             at=ts("2026-03-03"))
    states.grant_work(conn, username="alice", policy_id=p1,
                      at=ts("2026-03-05"), granted_by="ops.head")

    db.set_meta(conn, "seed_now", str(now))
    return {
        "policies": {"LOTO": p1, "PPE": p2, "EMG": p3},
        "versions": {"loto_v1": v1, "loto_v2": v2, "loto_v3": v3,
                     "ppe": pv, "emg": ev},
        "attachments": {"loto_v3_pdf": a1, "emg_map": a2},
        "now": now,
    }
