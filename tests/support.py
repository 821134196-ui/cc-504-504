"""共享测试夹具：每个用例一个临时 SQLite 库。"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import services as svc  # noqa: E402
from app.db import get_db, init_db  # noqa: E402


class RuleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.conn = get_db(self.db_path)
        init_db(self.conn)
        self._seed_people()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_people(self):
        self.uids = {}
        for username, display, role in [
            ("admin", "管理员", "admin"),
            ("buyer", "采购员", "buyer"),
            ("rev1", "评审甲", "reviewer"),
            ("rev2", "评审乙", "reviewer"),
            ("rev3", "评审丙", "reviewer"),
        ]:
            cur = self.conn.execute(
                "INSERT INTO users(username,display,role) VALUES (?,?,?)",
                (username, display, role))
            self.uids[username] = cur.lastrowid
        self.sup_a = self.conn.execute(
            "INSERT INTO suppliers(name,contact) VALUES ('绿野蔬菜合作社','x')").lastrowid
        self.sup_b = self.conn.execute(
            "INSERT INTO suppliers(name,contact) VALUES ('鲁西黄牛养殖场','y')").lastrowid

    def user(self, name):
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (self.uids[name],)).fetchone()
        return dict(row)

    def make_sample(self, code="T-001", seal="FC-100", supplier=None,
                    temp=3.0, limit=4.0, days_ago=0, shelf=3):
        sid = svc.receive_sample(
            self.conn, self.user("buyer"),
            code=code, category="生菜", origin="云南", arrival_temp=temp, temp_limit=limit,
            batch_no="B1", seal_code=seal, storage_loc="A-1",
            supplier_id=supplier or self.sup_a,
            sample_date=(date.today() - timedelta(days=days_ago)).isoformat(),
            shelf_days=shelf)
        return sid

    def audit_for(self, action_part):
        return self.conn.execute(
            "SELECT * FROM audit_logs WHERE action LIKE ? ORDER BY id",
            (f"%{action_part}%",)).fetchall()
