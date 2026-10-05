"""管理端写入接口：岗位、用户、制度版本、规则、培训资源。时间统一 ISO 存储。"""
from .util import S


def add_position(conn, code, name, parent_id=None):
    cur = conn.execute("INSERT INTO positions(code, name, parent_id) VALUES (?,?,?)",
                       (code, name, parent_id))
    conn.commit()
    return cur.lastrowid


def add_user(conn, name, position_id, hired_at):
    cur = conn.execute("INSERT INTO users(name, position_id) VALUES (?,?)", (name, position_id))
    uid = cur.lastrowid
    conn.execute("INSERT INTO position_history(user_id, position_id, valid_from) VALUES (?,?,?)",
                 (uid, position_id, S(hired_at)))
    conn.commit()
    return uid


def transfer_user(conn, user_id, new_position_id, now):
    """调岗：关闭原岗位区间，开启新区间。请求时求值立即生效。"""
    now = S(now)
    conn.execute("UPDATE position_history SET valid_to=? WHERE user_id=? AND valid_to IS NULL",
                 (now, user_id))
    conn.execute("INSERT INTO position_history(user_id, position_id, valid_from) VALUES (?,?,?)",
                 (user_id, new_position_id, now))
    conn.execute("UPDATE users SET position_id=? WHERE id=?", (new_position_id, user_id))
    conn.commit()


def depart_user(conn, user_id, now):
    """离职：状态置 departed 并关闭在岗区间，历史缓存即时作废。"""
    now = S(now)
    conn.execute("UPDATE users SET status='departed', departed_at=? WHERE id=?", (now, user_id))
    conn.execute("UPDATE position_history SET valid_to=? WHERE user_id=? AND valid_to IS NULL",
                 (now, user_id))
    conn.commit()


def add_policy(conn, code, title, category, is_public=False):
    cur = conn.execute("INSERT INTO policies(code, title, category, is_public) VALUES (?,?,?,?)",
                       (code, title, category, 1 if is_public else 0))
    conn.commit()
    return cur.lastrowid


def add_version(conn, policy_id, version_no, content, effective_from, effective_to=None,
                created_at=None):
    cur = conn.execute(
        "INSERT INTO policy_versions(policy_id, version_no, content, effective_from, effective_to, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (policy_id, version_no, content, S(effective_from),
         S(effective_to) if effective_to else None, S(created_at or effective_from)))
    conn.commit()
    return cur.lastrowid


def set_version_effective_to(conn, version_id, effective_to):
    conn.execute("UPDATE policy_versions SET effective_to=? WHERE id=?",
                 (S(effective_to), version_id))
    conn.commit()


def add_rule(conn, policy_id, subject_type, subject_id, effect, kind, valid_from,
             valid_to=None, note=""):
    cur = conn.execute(
        "INSERT INTO rules(policy_id, subject_type, subject_id, effect, kind, valid_from, valid_to, note)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (policy_id, subject_type, subject_id, effect, kind, S(valid_from),
         S(valid_to) if valid_to else None, note))
    conn.commit()
    return cur.lastrowid


def add_document(conn, title, policy_id=None, equipment=None, scenario=None,
                 position_id=None, is_internal=True):
    cur = conn.execute(
        "INSERT INTO documents(policy_id, title, equipment, scenario, position_id, is_internal)"
        " VALUES (?,?,?,?,?,?)",
        (policy_id, title, equipment, scenario, position_id, 1 if is_internal else 0))
    conn.commit()
    return cur.lastrowid


def add_training_resource(conn, policy_id, url, valid_from, valid_to=None):
    cur = conn.execute(
        "INSERT INTO training_resources(policy_id, url, valid_from, valid_to) VALUES (?,?,?,?)",
        (policy_id, url, S(valid_from), S(valid_to) if valid_to else None))
    conn.commit()
    return cur.lastrowid


def revoke_training_resource(conn, resource_id):
    conn.execute("UPDATE training_resources SET revoked=1 WHERE id=?", (resource_id,))
    conn.commit()
