"""Run the policy library API with the seeded fixture.

    python3 run_demo.py [--port 8080]
then e.g.
    curl 'http://127.0.0.1:8080/public/policies?at=2026-10-06'
    curl 'http://127.0.0.1:8080/me/policies?username=alice&equipment=BREAKER&scene=MAINTENANCE&at=2026-10-06'
"""
import argparse
from policyhub import db, seed, api
from policyhub.clock import Clock, ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    conn = db.connect(check_same_thread=False)
    db.init_db(conn)
    seed.build(conn)
    clock = Clock(ts("2026-10-06"))
    httpd = api.serve(conn, clock, host=args.host, port=args.port)
    print(f"policyhub listening on http://{args.host}:{args.port} "
          f"(frozen at 2026-10-06)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
