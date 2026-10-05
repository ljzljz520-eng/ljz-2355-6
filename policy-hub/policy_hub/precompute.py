"""按岗位预计算适用集 vs 请求时求值：实现两种策略并提供一致性对比。

- 预计算集只覆盖岗位维度（含岗位级例外），用户级例外必须在请求时叠加；
- 规则/岗位/例外变更必须触发重建，否则缓存变脏（compare 可检测）；
- 列表页/目录用预计算提速，授权与离线校验等决策点必须走请求时求值。
"""
from .resolver import _resolve_core, resolve_applicability
from .util import S


def rebuild_precomputed(conn, now):
    now = S(now)
    conn.execute("DELETE FROM precomputed_sets")
    positions = conn.execute("SELECT * FROM positions").fetchall()
    policies = conn.execute("SELECT id FROM policies").fetchall()
    for pos in positions:
        for p in policies:
            res = _resolve_core(conn, p["id"], now, user=None,
                                position_id=pos["id"], chain=[])
            if res.applicable:
                conn.execute(
                    "INSERT OR REPLACE INTO precomputed_sets(position_id, policy_id, computed_at)"
                    " VALUES (?,?,?)", (pos["id"], p["id"], now))
    conn.commit()


def precomputed_set(conn, position_id):
    return {r["policy_id"] for r in conn.execute(
        "SELECT policy_id FROM precomputed_sets WHERE position_id=?", (position_id,))}


def request_time_set(conn, user_id, now):
    now = S(now)
    out = set()
    for p in conn.execute("SELECT id FROM policies").fetchall():
        if resolve_applicability(conn, user_id, p["id"], now).applicable:
            out.add(p["id"])
    return out


def compare(conn, user_id, now):
    """返回两策略的差异：missing_in_precomputed=缓存漏算，stale_in_precomputed=缓存脏数据。"""
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    pre = precomputed_set(conn, user["position_id"])
    req = request_time_set(conn, user_id, now)
    return {
        "precomputed": pre,
        "request_time": req,
        "missing_in_precomputed": req - pre,
        "stale_in_precomputed": pre - req,
    }
