"""适用性裁决：岗位继承与例外冲突的确定性优先级，并输出完整推导链。

优先级（高 -> 低）：
  1) 主体更具体者优先：用户级 > 本岗位 > 上级岗位（逐级衰减）
  2) 同主体层级：例外(exception) > 基础(base)
  3) 仍并列：valid_from 晚者优先；再按 id 大者优先
版本有效期重叠时：effective_from 新者优先，其次 version_no 高者优先。
裁决结果必须给出唯一结论 + 推导链，绝不把全部命中条款抛给员工自行判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .util import S

_KIND_RANK = {"exception": 0, "base": 1}  # 同级中例外优先


@dataclass
class Step:
    kind: str            # info | winner | superseded | expired
    text: str
    rule_id: int | None = None


@dataclass
class Resolution:
    applicable: bool
    chain: list = field(default_factory=list)

    def explain(self) -> str:
        lines = [f"[{s.kind}] {s.text}" for s in self.chain]
        lines.append(f"=> 结论：{'适用' if self.applicable else '不适用'}")
        return "\n".join(lines)


def position_ancestors(conn, position_id):
    """岗位继承链：[(row, depth)]，depth=0 为本岗位，逐级向上。"""
    chain, pid, depth = [], position_id, 0
    while pid is not None:
        row = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
        if row is None:
            break
        chain.append((row, depth))
        pid, depth = row["parent_id"], depth + 1
    return chain


def position_at(conn, user_id, as_of):
    """按调岗历史求 as_of 时点用户所在岗位（历史追溯的关键）。"""
    as_of = S(as_of)
    row = conn.execute(
        """SELECT position_id FROM position_history
           WHERE user_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)
           ORDER BY valid_from DESC LIMIT 1""",
        (user_id, as_of, as_of)).fetchone()
    return row["position_id"] if row else None


def resolve_applicability(conn, user_id, policy_id, as_of):
    as_of = S(as_of)
    chain = []
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        return Resolution(False, [Step("info", f"用户#{user_id} 不存在")])
    if user["status"] != "active" and (user["departed_at"] is None or as_of >= user["departed_at"]):
        return Resolution(False, [Step("info", f"用户 {user['name']} 已离职，制度不再适用")])
    pos_id = position_at(conn, user_id, as_of)
    if pos_id is None:
        return Resolution(False, [Step("info", f"{as_of} 时点无在岗记录，不适用")])
    return _resolve_core(conn, policy_id, as_of, user=user, position_id=pos_id, chain=chain)


def _resolve_core(conn, policy_id, as_of, user, position_id, chain):
    """user 为 None 时只按岗位链求值（供预计算使用，用户级例外留给请求时）。"""
    as_of = S(as_of)
    subjects = []
    if user is not None:
        subjects.append(("user", user["id"], 0, f"用户[{user['name']}]"))
    for pos, depth in position_ancestors(conn, position_id):
        label = "本岗位" if depth == 0 else f"上级岗位(第{depth}层)"
        subjects.append(("position", pos["id"], 1 + depth, f"{label}[{pos['name']}]"))

    candidates = []
    for stype, sid, scope, label in subjects:
        rows = conn.execute(
            "SELECT * FROM rules WHERE policy_id=? AND subject_type=? AND subject_id=?",
            (policy_id, stype, sid)).fetchall()
        for r in rows:
            effect_cn = "纳入" if r["effect"] == "include" else "排除"
            kind_cn = "例外" if r["kind"] == "exception" else "基础"
            desc = (f"规则#{r['id']} {label} {kind_cn}-{effect_cn} "
                    f"有效期[{r['valid_from']}~{r['valid_to'] or '∞'}]")
            if not (r["valid_from"] <= as_of and (r["valid_to"] is None or as_of < r["valid_to"])):
                chain.append(Step("expired", desc + " —— 不在有效期，不参与裁决", r["id"]))
                continue
            candidates.append((scope, _KIND_RANK[r["kind"]], r["valid_from"], r["id"], r, desc))

    if not candidates:
        chain.append(Step("info", "无任何规则命中，默认不适用"))
        return Resolution(False, chain)

    candidates.sort(key=lambda c: (c[0], c[1]))
    top_key = (candidates[0][0], candidates[0][1])
    top = [c for c in candidates if (c[0], c[1]) == top_key]
    top.sort(key=lambda c: (c[2], c[3]), reverse=True)
    winner = top[0]
    w_rule = winner[4]
    verdict = "适用" if w_rule["effect"] == "include" else "不适用"
    chain.append(Step("winner", winner[5] + f" —— 优先级最高，裁决胜出：【{verdict}】", w_rule["id"]))
    for c in candidates:
        if c[3] == w_rule["id"]:
            continue
        if (c[0], c[1]) == top_key:
            reason = f"与规则#{w_rule['id']} 同级，但生效更早/序号更小，让位"
        else:
            reason = f"优先级低于规则#{w_rule['id']}（主体更具体者优先；同级例外优先），让位"
        chain.append(Step("superseded", c[5] + f" —— {reason}", c[3]))
    return Resolution(w_rule["effect"] == "include", chain)


def resolve_version(conn, policy_id, as_of):
    """有效期重叠的确定性裁决：effective_from 新者优先，其次 version_no 高者优先。"""
    as_of = S(as_of)
    rows = conn.execute(
        """SELECT * FROM policy_versions
           WHERE policy_id=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
           ORDER BY effective_from DESC, version_no DESC""",
        (policy_id, as_of, as_of)).fetchall()
    if not rows:
        return None, [Step("info", f"制度#{policy_id} 在 {as_of} 无生效版本")]
    notes = []
    if len(rows) > 1:
        notes.append(Step("info", f"检测到 {len(rows)} 个版本有效期重叠，"
                                  "按“生效时间新者优先、版本号高者优先”裁定"))
        for r in rows[1:]:
            notes.append(Step("superseded", f"v{r['version_no']}（{r['effective_from']} 起生效）让位"))
    return rows[0], notes
