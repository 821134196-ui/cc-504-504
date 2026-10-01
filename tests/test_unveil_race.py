"""揭盲竞态：两人同时揭盲只产生一次结果。

- 同一人连点不占位、不重复
- 第二人才真正揭盲
- 多线程并发“第二确认”，数据库只允许一条 unveils 记录
- 揭盲后再次确认被拒绝
- 撤样样品不能揭盲
"""
import threading
import unittest

from app import services as svc
from tests.support import RuleCase


class UnveilRaceTest(RuleCase):
    def test_two_different_people_required(self):
        sid = self.make_sample(code="U-001", seal="FC-U1")
        r1 = svc.unveil_confirm(self.conn, self.user("rev1"), sid)
        self.assertTrue(r1.get("waiting"))
        # 同一人重复确认仍然只是等待
        again = svc.unveil_confirm(self.conn, self.user("rev1"), sid)
        self.assertTrue(again.get("waiting"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM unveil_confirmations WHERE sample_id=?",
                              (sid,)).fetchone()["c"], 1)
        self.assertIsNone(
            self.conn.execute("SELECT * FROM unveils WHERE sample_id=?", (sid,)).fetchone())

        done = svc.unveil_confirm(self.conn, self.user("rev2"), sid)
        self.assertTrue(done.get("revealed"))
        self.assertEqual(done["supplier"], "绿野蔬菜合作社")

    def test_only_one_unveil_result_ever(self):
        sid = self.make_sample(code="U-002", seal="FC-U2")
        svc.unveil_confirm(self.conn, self.user("rev1"), sid)
        svc.unveil_confirm(self.conn, self.user("rev2"), sid)
        # 第三人/再来一轮都不能产生第二条结果
        r = svc.unveil_confirm(self.conn, self.user("rev3"), sid)
        self.assertTrue(r.get("already"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM unveils").fetchone()["c"], 1)
        self.assertEqual(
            self.conn.execute("SELECT status FROM samples WHERE id=?", (sid,)).fetchone()["status"],
            "revealed")

    def test_concurrent_second_confirmations_single_winner(self):
        sid = self.make_sample(code="U-003", seal="FC-U3")
        svc.unveil_confirm(self.conn, self.user("rev1"), sid)

        results = []
        users = {n: self.user(n) for n in ("rev1", "rev2", "rev3")}
        barrier = threading.Barrier(3)
        errors = []

        def contender(name):
            # 每个线程独立连接，指向同一个库文件；user 字典为纯数据可跨线程共享
            from app.db import get_db
            conn = get_db(self.db_path)
            try:
                barrier.wait()
                results.append(svc.unveil_confirm(conn, users[name], sid))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=contender, args=(n,))
                   for n in ("rev2", "rev3")]
        for t in threads:
            t.start()
        barrier.wait()  # 主线程放开栅栏，两个“第二确认”同时冲
        for t in threads:
            t.join()

        self.assertFalse(errors, f"并发揭盲出现异常: {errors}")
        winners = [r for r in results if r.get("revealed")]
        self.assertEqual(len(winners), 1, f"应当恰有一次揭盲成功: {results}")
        # 落败方要么在事务队列后发现已揭盲(already)，要么撞唯一约束(race_lost)，
        # 两者都代表“第二次结果被拒绝”
        self.assertTrue(any(r.get("race_lost") or r.get("already") for r in results
                            if not r.get("revealed")))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM unveils WHERE sample_id=?",
                              (sid,)).fetchone()["c"], 1)
        # 落败尝试有审计（竞态兜底或重复揭盲拒绝，至少其一）
        self.assertTrue(self.audit_for("unveil.race_lost") or
                        self.audit_for("unveil.already"))

    def test_concurrent_two_pairs_single_result(self):
        """两组两人同时揭盲同一份样品：整体也只能有一次结果。"""
        sid = self.make_sample(code="U-004", seal="FC-U4")
        out = []
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        users = {n: self.user(n) for n in ("rev1", "rev2", "rev3", "admin")}

        from app.db import get_db

        def run(first, second):
            conn = get_db(self.db_path)
            try:
                barrier.wait()
                r1 = svc.unveil_confirm(conn, users[first], sid)
                r2 = svc.unveil_confirm(conn, users[second], sid)
                with lock:
                    out.extend([r1, r2])
            finally:
                conn.close()

        ts = [threading.Thread(target=run, args=("rev1", "rev2")),
              threading.Thread(target=run, args=("rev3", "admin"))]
        for t in ts:
            t.start()
        barrier.wait()
        for t in ts:
            t.join()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM unveils WHERE sample_id=?",
                              (sid,)).fetchone()["c"], 1)

    def test_buyer_cannot_unveil(self):
        sid = self.make_sample(code="U-005", seal="FC-U5")
        with self.assertRaises(svc.ServiceError):
            svc.unveil_confirm(self.conn, self.user("buyer"), sid)

    def test_withdrawn_sample_cannot_unveil(self):
        sid = self.make_sample(code="U-006", seal="FC-U6")
        svc.withdraw_sample(self.conn, self.user("buyer"), sid)
        with self.assertRaises(svc.ServiceError):
            svc.unveil_confirm(self.conn, self.user("rev1"), sid)


if __name__ == "__main__":
    unittest.main()
