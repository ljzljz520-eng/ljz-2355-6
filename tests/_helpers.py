import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from policyhub import db, seed
from policyhub.clock import ts, Clock


def fresh(shared=False):
    conn = db.connect(check_same_thread=not shared)
    db.init_db(conn)
    ref = seed.build(conn)
    return conn, ref
