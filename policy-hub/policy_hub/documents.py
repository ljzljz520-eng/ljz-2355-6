"""文档站：按岗位/设备/场景过滤 + 权限可见范围；公开页只暴露白名单元数据。"""
from .resolver import position_at, resolve_applicability
from .util import S

PUBLIC_FIELDS = ("code", "title", "category", "effective_from")


def document_visible_to(conn, user, doc, now):
    now = S(now)
    if user["status"] != "active":
        return False
    if doc["policy_id"] is not None:
        return resolve_applicability(conn, user["id"], doc["policy_id"], now).applicable
    if doc["position_id"] is not None:
        return position_at(conn, user["id"], now) == doc["position_id"]
    return True


def search_documents(conn, user_id, now, position_id=None, equipment=None, scenario=None):
    """先按三维标签过滤，再按权限服务计算可见范围。"""
    sql, params = "SELECT * FROM documents WHERE 1=1", []
    if position_id is not None:
        sql += " AND (position_id IS NULL OR position_id=?)"
        params.append(position_id)
    if equipment is not None:
        sql += " AND equipment=?"
        params.append(equipment)
    if scenario is not None:
        sql += " AND scenario=?"
        params.append(scenario)
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return [d for d in conn.execute(sql, params).fetchall()
            if document_visible_to(conn, user, d, now)]


def public_catalog(conn, now):
    """公开页：只输出公开制度的白名单元数据，绝不泄露规则、例外、签收等内部数据。"""
    now = S(now)
    rows = conn.execute(
        """SELECT p.code, p.title, p.category, v.effective_from
           FROM policies p JOIN policy_versions v ON v.policy_id = p.id
           WHERE p.is_public=1 AND v.effective_from<=? AND (v.effective_to IS NULL OR v.effective_to>?)
           ORDER BY p.code, v.effective_from DESC""",
        (now, now)).fetchall()
    seen, catalog = set(), []
    for r in rows:  # 每个制度只保留当前生效版本
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        catalog.append({k: r[k] for k in PUBLIC_FIELDS})
    return catalog
