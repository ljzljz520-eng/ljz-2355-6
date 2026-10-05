"""时间规范化：所有持久化时间统一为可字典序比较的 ISO 字符串。"""
from datetime import datetime


def S(t):
    if t is None:
        return None
    if isinstance(t, datetime):
        return t.isoformat(timespec="seconds")
    return t
