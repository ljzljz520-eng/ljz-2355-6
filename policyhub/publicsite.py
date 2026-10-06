"""Anonymous public page.

Only policies explicitly flagged public_meta are exposed, and only *metadata*
(title/code/category/version window). Body, scope, exceptions, attachments and
employee state are never leaked here. Expired revisions are not advertised.
"""
from __future__ import annotations


def public_listing(conn, *, at):
    rows = conn.execute(
        """
        SELECT p.code, p.title, p.category, pv.version_no,
               pv.valid_from, pv.valid_to
        FROM policies p
        JOIN policy_versions pv ON pv.policy_id = p.id
        WHERE p.public_meta = 1
          AND pv.valid_from <= ? AND (pv.valid_to IS NULL OR pv.valid_to > ?)
        ORDER BY p.code, pv.version_no
        """,
        (at, at),
    ).fetchall()
    return [{"code": r["code"], "title": r["title"], "category": r["category"],
             "version_no": r["version_no"], "valid_from": r["valid_from"],
             "valid_to": r["valid_to"]} for r in rows]
