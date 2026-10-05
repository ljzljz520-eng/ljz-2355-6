"""知悉签收 / 培训完成 / 作业授权：三个相互独立的状态机。

一次“点击阅读”只产生签收记录，绝不隐含培训完成或作业授权；
授权存续期间前置条件（当前版本签收、培训链接有效）必须持续成立。
"""
from .resolver import resolve_version
from .util import S


class VersionChangedError(Exception):
    """签收途中制度版本更新，原签收作废。"""


class PrerequisiteError(Exception):
    """前置条件不满足。"""


def begin_ack(conn, user_id, policy_id, now):
    now = S(now)
    v, _ = resolve_version(conn, policy_id, now)
    if v is None:
        raise PrerequisiteError("当前无生效版本，无法发起签收")
    cur = conn.execute(
        "INSERT INTO acks(user_id, policy_version_id, status, begun_at) VALUES (?,?, 'pending', ?)",
        (user_id, v["id"], now))
    conn.commit()
    return cur.lastrowid


def sign_ack(conn, ack_id, now):
    """签署时校验版本未变；若已发布新版本，原签收置为 stale 并拒绝。"""
    now = S(now)
    ack = conn.execute("SELECT * FROM acks WHERE id=?", (ack_id,)).fetchone()
    if ack is None:
        raise KeyError(f"签收单#{ack_id} 不存在")
    if ack["status"] != "pending":
        raise PrerequisiteError(f"签收单状态为 {ack['status']}，不可签署")
    ver = conn.execute("SELECT * FROM policy_versions WHERE id=?",
                       (ack["policy_version_id"],)).fetchone()
    cur, _ = resolve_version(conn, ver["policy_id"], now)
    if cur is None or cur["id"] != ver["id"]:
        conn.execute("UPDATE acks SET status='stale' WHERE id=?", (ack_id,))
        conn.commit()
        raise VersionChangedError(
            f"阅读的是 v{ver['version_no']}，但当前生效版本已是 "
            f"v{cur['version_no'] if cur else '无'}，本次签收作废，需重新阅读新版本")
    conn.execute("UPDATE acks SET status='signed', signed_at=? WHERE id=?", (now, ack_id))
    conn.commit()


def complete_training(conn, user_id, policy_id, resource_id, now, score=None):
    now = S(now)
    r = conn.execute("SELECT * FROM training_resources WHERE id=?", (resource_id,)).fetchone()
    if r is None or r["policy_id"] != policy_id:
        raise PrerequisiteError("培训资源与制度不匹配")
    if r["revoked"]:
        raise PrerequisiteError("培训链接已失效（已撤销）")
    if not (r["valid_from"] <= now and (r["valid_to"] is None or now < r["valid_to"])):
        raise PrerequisiteError("培训链接已失效（不在有效期）")
    cur = conn.execute(
        "INSERT INTO trainings(user_id, policy_id, resource_id, completed_at, score)"
        " VALUES (?,?,?,?,?)",
        (user_id, policy_id, resource_id, now, score))
    conn.commit()
    return cur.lastrowid


def _ack_current(conn, user_id, policy_id, now):
    cur, _ = resolve_version(conn, policy_id, now)
    if cur is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM acks WHERE user_id=? AND policy_version_id=? AND status='signed'",
        (user_id, cur["id"])).fetchone()
    return row is not None


def _training_current(conn, user_id, policy_id, now):
    rows = conn.execute(
        """SELECT r.revoked, r.valid_from, r.valid_to FROM trainings t
           JOIN training_resources r ON r.id = t.resource_id
           WHERE t.user_id=? AND t.policy_id=?""",
        (user_id, policy_id)).fetchall()
    return any((not r["revoked"]) and r["valid_from"] <= now
               and (r["valid_to"] is None or now < r["valid_to"]) for r in rows)


def grant_authorization(conn, user_id, policy_id, now, expires_at=None):
    """作业授权是独立动作：必须同时具备当前版本签收 + 有效培训。"""
    now = S(now)
    if not _ack_current(conn, user_id, policy_id, now):
        raise PrerequisiteError("缺少【当前生效版本】的知悉签收，不能授权")
    if not _training_current(conn, user_id, policy_id, now):
        raise PrerequisiteError("培训未完成或培训链接已失效，不能授权")
    cur = conn.execute(
        "INSERT INTO authorizations(user_id, policy_id, status, granted_at, expires_at)"
        " VALUES (?,?, 'active', ?, ?)",
        (user_id, policy_id, now, S(expires_at) if expires_at else None))
    conn.commit()
    return cur.lastrowid


def revoke_authorization(conn, auth_id, now):
    conn.execute("UPDATE authorizations SET status='revoked', revoked_at=? WHERE id=?",
                 (S(now), auth_id))
    conn.commit()


def check_authorization(conn, user_id, policy_id, now):
    """作业前校验：授权有效，且签收（当前版本）与培训（链接有效）持续成立。"""
    now = S(now)
    rows = conn.execute(
        "SELECT * FROM authorizations WHERE user_id=? AND policy_id=? AND status='active'",
        (user_id, policy_id)).fetchall()
    for a in rows:
        if a["expires_at"] is not None and now >= a["expires_at"]:
            continue
        if _ack_current(conn, user_id, policy_id, now) and \
                _training_current(conn, user_id, policy_id, now):
            return True
    return False
