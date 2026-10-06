import http.client
import json
import threading
import unittest
from tests._helpers import fresh, ts
from policyhub import api, states, catalog
from policyhub.clock import Clock


class ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.ref = fresh(shared=True)
        cls.httpd = api.serve(cls.conn, Clock(ts("2026-10-06")),
                              host="127.0.0.1", port=8099)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", 8099, timeout=5)
        data = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        c.request(method, path, data, headers)
        r = c.getresponse()
        payload = json.loads(r.read().decode())
        c.close()
        return r.status, payload

    def test_public(self):
        code, body = self.call("GET", "/public/policies?at=2026-10-06")
        self.assertEqual(code, 200)
        self.assertEqual({i["code"] for i in body["items"]}, {"EMG-03"})

    def test_filtered_listing_and_chain(self):
        code, body = self.call(
            "GET", "/me/policies?username=bob&equipment=MOTOR&scene=REPAIR&at=2026-10-06")
        self.assertEqual(code, 200)
        codes = {i["policy_code"] for i in body["items"]}
        self.assertIn("LOTO-01", codes)
        self.assertNotIn("PPE-02", codes)  # chem handling not this scene/equip
        loto = next(i for i in body["items"] if i["policy_code"] == "LOTO-01")
        self.assertEqual(loto["winning_source"], "USER_GRANT")

    def test_forbidden_body(self):
        code, body = self.call(
            "GET", f"/me/policy?username=carol&policy_id={self.ref['policies']['LOTO']}&at=2026-10-06")
        self.assertEqual(code, 403)

    def test_ack_flow_and_separation(self):
        pid = self.ref["policies"]["LOTO"]
        code, sess = self.call("POST", "/me/read-session",
                               {"username": "bob", "policy_id": pid,
                                "at": "2026-10-06"})
        self.assertEqual(code, 200)
        code, ack = self.call("POST", "/me/ack",
                              {"username": "bob", "token": sess["token"],
                               "at": "2026-10-06"})
        self.assertEqual(code, 200)
        code, gate = self.call(
            "GET", f"/me/work-gate?username=bob&policy_id={pid}"
                   "&equipment=MOTOR&scene=REPAIR&at=2026-10-06")
        self.assertFalse(gate["allowed"])
        self.assertFalse(gate["gates"]["authorized"]["ok"])

    def test_training_then_grant_opens_gate(self):
        pid = self.ref["policies"]["LOTO"]
        code, link = self.call("POST", "/admin/issue-training",
                               {"version_id": self.ref["versions"]["loto_v3"],
                                "at": "2026-10-06"})
        self.assertEqual(code, 200)
        code, _ = self.call("POST", "/me/training-complete",
                            {"username": "bob", "token": link["token"],
                             "at": "2026-10-06"})
        self.assertEqual(code, 200)
        code, g = self.call("POST", "/admin/grant-work",
                            {"username": "bob", "policy_id": pid,
                             "at": "2026-10-06"})
        self.assertEqual(code, 200)
        code, gate = self.call(
            "GET", f"/me/work-gate?username=bob&policy_id={pid}"
                   "&equipment=MOTOR&scene=REPAIR&at=2026-10-06")
        self.assertTrue(gate["allowed"], gate)

    def test_expired_training_link_rejected(self):
        code, link = self.call("POST", "/admin/issue-training",
                               {"version_id": self.ref["versions"]["loto_v3"],
                                "ttl": 60, "at": "2026-10-06"})
        code, body = self.call("POST", "/me/training-complete",
                               {"username": "bob", "token": link["token"],
                                "at": "2026-10-07"})
        self.assertEqual(code, 409)
        self.assertIn("EXPIRED", body["error"])

    def test_offline_issue_redeem_and_leave_revokes(self):
        code, g = self.call("POST", "/me/offline/issue",
                            {"username": "carol",
                             "attachment_id": self.ref["attachments"]["emg_map"],
                             "ttl": 999999, "at": "2026-10-06"})
        self.assertEqual(code, 200)
        code, r = self.call("POST", "/me/offline/redeem",
                            {"username": "carol", "token": g["token"],
                             "at": "2026-10-07"})
        self.assertEqual(code, 200)
        self.call("POST", "/admin/leave",
                  {"username": "carol", "at": "2026-10-10"})
        code, body = self.call("POST", "/me/offline/redeem",
                               {"username": "carol", "token": g["token"],
                                "at": "2026-10-11"})
        self.assertEqual(code, 409)
        self.assertIn("REVOKED", body["error"])

    def test_transfer_and_rebuild_compare(self):
        code, _ = self.call("POST", "/admin/transfer",
                            {"username": "alice", "to_position_code": "MECH",
                             "at": "2026-10-15"})
        self.assertEqual(code, 200)
        code, body = self.call("POST", "/admin/rebuild-sets",
                               {"at": "2026-10-16"})
        self.assertEqual(code, 200)
        code, rep = self.call(
            "GET", "/admin/compare?position_code=MECH&at=2026-10-16")
        self.assertEqual(rep["diffs"], [])

    def test_history_endpoint(self):
        code, body = self.call(
            "GET", f"/admin/history?username=alice&policy_id={self.ref['policies']['LOTO']}&at=2026-03-15")
        self.assertEqual(code, 200)
        self.assertEqual(body["version_no"], 2)
        self.assertIn("v2", body["historical_body"])


if __name__ == "__main__":
    unittest.main()
