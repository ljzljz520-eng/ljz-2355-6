"""Thin JSON HTTP layer. No business rules live here - it delegates to
PolicyService; errors map to stable codes used by the acceptance tests."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .clock import ts as parse_ts
from .service import PolicyService
from .publicsite import public_listing
from .states import StateError
from .offline import OfflineError
from .catalog import CatalogError
from .engine import EvalError


def make_handler(conn, clock):
    svc = PolicyService(conn)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def _at(self, q, body):
            raw = q.get("at", [None])[0] or body.get("at")
            return parse_ts(raw) if isinstance(raw, str) else (raw or clock.now())

        # GET routes
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)  # keep lists; _at/q.get(...,[None])[0] read them
            try:
                if u.path == "/public/policies":
                    return self._send(200, {"items": public_listing(
                        conn, at=self._at(q, {}))})
                if u.path == "/me/policies":
                    return self._send(200, svc.my_policies(
                        username=q["username"][0], at=self._at(q, {}),
                        equipment=q.get("equipment",[None])[0], scene=q.get("scene",[None])[0],
                        include_denied=q.get("include_denied",[""])[0] == "1",
                        client_snapshot_id=q.get("client_snapshot_id",[None])[0]))
                if u.path == "/me/policy":
                    return self._send(200, svc.policy_body(
                        username=q["username"][0], policy_id=int(q["policy_id"][0]),
                        at=self._at(q, {})))
                if u.path == "/me/derivation":
                    return self._send(200, svc.derivation(
                        username=q["username"][0], policy_id=int(q["policy_id"][0]),
                        at=self._at(q, {}), equipment=q.get("equipment",[None])[0],
                        scene=q.get("scene",[None])[0]))
                if u.path == "/me/status":
                    return self._send(200, svc.status(
                        username=q["username"][0], policy_id=int(q["policy_id"][0]),
                        at=self._at(q, {})))
                if u.path == "/me/work-gate":
                    return self._send(200, svc.work_gate(
                        username=q["username"][0], policy_id=int(q["policy_id"][0]),
                        at=self._at(q, {}), equipment=q.get("equipment",[None])[0],
                        scene=q.get("scene",[None])[0]))
                if u.path == "/me/attachments":
                    return self._send(200, svc.attachments(
                        username=q["username"][0], policy_id=int(q["policy_id"][0]),
                        at=self._at(q, {})))
                if u.path == "/me/offline":
                    return self._send(200, {"items": svc.my_offline_grants(
                        username=q["username"][0], at=self._at(q, {}))})
                if u.path == "/admin/compare":
                    return self._send(200, svc.compare_sets(
                        position_code=q["position_code"][0], at=self._at(q, {})))
                if u.path == "/admin/history":
                    return self._send(200, svc.historical_view(
                        username=q["username"][0], policy_id=int(q["policy_id"][0]),
                        at=self._at(q, {})))
                return self._send(404, {"error": "NOT_FOUND"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except (StateError, OfflineError, CatalogError, EvalError) as e:
                return self._send(409, {"error": str(e)})
            except KeyError as e:
                return self._send(400, {"error": f"missing param {e}"})

        # POST routes
        def do_POST(self):
            u = urlparse(self.path)
            body = self._body()
            try:
                at = self._at({}, body)
                if u.path == "/me/read-session":
                    return self._send(200, svc.open_read(
                        username=body["username"], policy_id=int(body["policy_id"]), at=at))
                if u.path == "/me/ack":
                    return self._send(200, svc.acknowledge(
                        username=body["username"], token=body["token"], at=at))
                if u.path == "/me/training-complete":
                    return self._send(200, svc.complete_training(
                        username=body["username"], token=body["token"], at=at))
                if u.path == "/me/offline/issue":
                    return self._send(200, svc.issue_offline(
                        username=body["username"],
                        attachment_id=int(body["attachment_id"]), at=at,
                        ttl=body.get("ttl")))
                if u.path == "/me/offline/redeem":
                    return self._send(200, {k: (v.hex() if k == "content" else v)
                        for k, v in svc.redeem_offline(
                        username=body["username"], token=body["token"], at=at,
                        equipment=body.get("equipment"),
                        scene=body.get("scene")).items()})
                if u.path == "/admin/grant-work":
                    from . import states
                    auth_id = states.grant_work(
                        conn, username=body["username"],
                        policy_id=int(body["policy_id"]), at=at,
                        valid_to=parse_ts(body["valid_to"])
                        if isinstance(body.get("valid_to"), str) else body.get("valid_to"),
                        granted_by=body.get("granted_by", "manager"))
                    return self._send(200, {"auth_id": auth_id})
                if u.path == "/admin/revoke-work":
                    from . import states
                    states.revoke_work(conn, auth_id=int(body["auth_id"]), at=at,
                                       reason=body.get("reason", ""))
                    return self._send(200, {"revoked": body["auth_id"]})
                if u.path == "/admin/issue-training":
                    from . import states
                    return self._send(200, states.issue_training_link(
                        conn, version_id=int(body["version_id"]), at=at,
                        ttl=body.get("ttl", 7 * 24 * 3600)))
                if u.path == "/admin/transfer":
                    return self._send(200, svc.transfer(
                        username=body["username"],
                        to_position_code=body["to_position_code"], at=at,
                        keep_policy_ids=body.get("keep_policy_ids", [])))
                if u.path == "/admin/leave":
                    return self._send(200, svc.leave(
                        username=body["username"], at=at))
                if u.path == "/admin/rebuild-sets":
                    return self._send(200, {"rule_epoch": svc.rebuild_sets(at)})
                return self._send(404, {"error": "NOT_FOUND"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except (StateError, OfflineError, CatalogError, EvalError) as e:
                return self._send(409, {"error": str(e)})
            except KeyError as e:
                return self._send(400, {"error": f"missing param {e}"})

    return Handler


def serve(conn, clock, host="127.0.0.1", port=8080):
    # The threaded server shares one connection; allow cross-thread use. Callers
    # should pass a connection opened with db.connect(..., check_same_thread=False).
    httpd = ThreadingHTTPServer((host, port), make_handler(conn, clock))
    httpd.daemon_threads = True
    return httpd
