"""项目登记。硬性约束：项目绝不自动生成新的作业安全规则或授权。

项目所需的制度适用关系必须由制度管理员显式维护（rules 表），
避免“建了项目就默认具备作业资格”的隐式放权。
"""


def create_project(conn, name, equipment=None):
    cur = conn.execute("INSERT INTO projects(name, equipment) VALUES (?,?)", (name, equipment))
    conn.commit()
    return cur.lastrowid
