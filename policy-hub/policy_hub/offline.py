"""离线附件的有限期访问：令牌带 TTL，校验时重估在职状态与可见性（调岗/撤权即时生效）。"""
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from .documents import document_visible_to
from .util import S


@dataclass
class Verdict:
    allowed: bool
    reason: str


def grant_offline(conn, user_id, document_id, now, ttl_seconds=3600):
    now_dt = now if isinstance(now, datetime) else datetime.fromisoformat(now)
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if doc is None:
        raise KeyError(f"文档#{document_id} 不存在")
    if not document_visible_to(conn, user, doc, now_dt):
        raise PermissionError("当前无权查看该文档，不能发放离线附件")
    token = secrets.token_urlsafe(16)
    expires = now_dt + timedelta(seconds=ttl_seconds)
    conn.execute(
        "INSERT INTO offline_grants(user_id, document_id, token, granted_at, expires_at)"
        " VALUES (?,?,?,?,?)",
        (user_id, document_id, token, S(now_dt), S(expires)))
    conn.commit()
    return token


def validate_offline(conn, token, now):
    now = S(now)
    g = conn.execute("SELECT * FROM offline_grants WHERE token=?", (token,)).fetchone()
    if g is None:
        return Verdict(False, "令牌不存在")
    if g["revoked_at"] is not None:
        return Verdict(False, "令牌已被撤销")
    if now >= g["expires_at"]:
        return Verdict(False, "令牌已过有限期，需重新申请")
    user = conn.execute("SELECT * FROM users WHERE id=?", (g["user_id"],)).fetchone()
    if user["status"] != "active":
        return Verdict(False, "用户已离职，历史缓存即时作废")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (g["document_id"],)).fetchone()
    if not document_visible_to(conn, user, doc, now):
        return Verdict(False, "调岗或规则变更导致可见性已回收")
    return Verdict(True, "ok")


def revoke_offline(conn, token, now):
    conn.execute("UPDATE offline_grants SET revoked_at=? WHERE token=?", (S(now), token))
    conn.commit()
