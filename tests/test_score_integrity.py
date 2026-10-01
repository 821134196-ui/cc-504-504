"""评分完整性与全流程留痕：漏项、范围、唯一评分、异常态拦截、封存唯一、撤样释放。"""
import sqlite3
import unittest
from datetime import date, timedelta

from app import services as svc
from tests.support import RuleCase


class ScoreIntegrityTest(RuleCase):
    def test_missing_items_rejected(self):
        sid = self.make_sample(code="S-001", seal="FC-S1")
        with self.assertRaises(svc.ServiceError) as cm:
            svc.submit_score(self.conn, self.user("rev1"), sid, None, 8, 12.0, "")
        self.assertIn("漏项", str(cm.exception))
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid, 8, None, None, "")
        # 漏项尝试被审计且没有产生评分
        self.assertTrue(self.audit_for("score.incomplete"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"], 0)

    def test_score_ranges(self):
        sid = self.make_sample(code="S-002", seal="FC-S2")
        for bad in ((0, 8, 10.0), (11, 8, 10.0), (8, 0, 10.0), (8, 8, -1.0), (8, 8, 100.0)):
            with self.assertRaises(svc.ServiceError):
                svc.submit_score(self.conn, self.user("rev1"), sid, *bad, "")
            # 每次失败后仍允许重新提交（没有半成品记录）
        svc.submit_score(self.conn, self.user("rev1"), sid, 10, 1, 99.9, "边界值")

    def test_integer_only_for_sensory(self):
        sid = self.make_sample(code="S-003", seal="FC-S3")
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid, 7.5, 8, 10.0, "")

    def test_one_score_per_reviewer_per_sample(self):
        sid = self.make_sample(code="S-004", seal="FC-S4")
        svc.submit_score(self.conn, self.user("rev1"), sid, 8, 8, 10.0, "first")
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid, 9, 9, 11.0, "second")
        # 另一个评审人可以提交
        svc.submit_score(self.conn, self.user("rev2"), sid, 6, 6, 20.0, "")

    def test_db_check_constraints_enforced(self):
        """绕过服务层直接写坏数据也应被 SQLite 拒绝。"""
        sid = self.make_sample(code="S-005", seal="FC-S5")
        rid = self.uids["rev1"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO scores(sample_id,user_id,taste,spec,loss_measured) "
                "VALUES (?,?,?,?,?)", (sid, rid, None, 8, 10.0))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO scores(sample_id,user_id,taste,spec,loss_measured) "
                "VALUES (?,?,?,?,?)", (sid, rid, 99, 8, 10.0))

    def test_abnormal_samples_not_scorable(self):
        # 封签破损
        sid = self.make_sample(code="S-006", seal="FC-S6")
        svc.mark_exception(self.conn, self.user("buyer"), sid, "seal_broken",
                           "封签撕裂", status="seal_broken")
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid, 8, 8, 10.0, "")

        # 到货超温（登记时自动标记）
        sid2 = self.make_sample(code="S-007", seal="FC-S7", temp=7.0)
        self.assertEqual(
            self.conn.execute("SELECT status FROM samples WHERE id=?", (sid2,)).fetchone()["status"],
            "over_temp")
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid2, 8, 8, 10.0, "")

    def test_expired_sample_flagged_on_scoring(self):
        sid = self.make_sample(code="S-008", seal="FC-S8", days_ago=5, shelf=3)
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid, 8, 8, 10.0, "")
        self.assertEqual(
            self.conn.execute("SELECT status FROM samples WHERE id=?", (sid,)).fetchone()["status"],
            "expired")
        self.assertTrue(any(e["kind"] == "expired" for e in self.conn.execute(
            "SELECT kind FROM events WHERE sample_id=?", (sid,)).fetchall()))


class SealAndTraceTest(RuleCase):
    def test_seal_code_unique_among_active_samples(self):
        self.make_sample(code="N-001", seal="FC-ONLY")
        with self.assertRaises(svc.ServiceError) as cm:
            self.make_sample(code="N-002", seal="FC-ONLY")
        self.assertIn("封存编号", str(cm.exception))

    def test_seal_released_after_withdraw(self):
        s1 = self.make_sample(code="N-003", seal="FC-MOVE")
        svc.withdraw_sample(self.conn, self.user("buyer"), s1)
        # 撤样后同一封存编号可用于新样品
        s2 = self.make_sample(code="N-004", seal="FC-MOVE")
        rows = self.conn.execute(
            "SELECT code,status FROM samples WHERE seal_code='FC-MOVE' ORDER BY id").fetchall()
        self.assertEqual([(r["code"], r["status"]) for r in rows],
                         [("N-003", "withdrawn"), ("N-004", "sealed")])
        # 此时再来一份仍然冲突（一份封存编号只关联一份当前样品）
        with self.assertRaises(svc.ServiceError):
            self.make_sample(code="N-005", seal="FC-MOVE")
        self.assertEqual(s2 is not None, True)

    def test_cannot_withdraw_after_reveal(self):
        sid = self.make_sample(code="N-006", seal="FC-W1")
        svc.submit_score(self.conn, self.user("rev1"), sid, 8, 8, 10.0, "")
        svc.submit_score(self.conn, self.user("rev2"), sid, 7, 7, 12.0, "")
        svc.unveil_confirm(self.conn, self.user("rev1"), sid)
        svc.unveil_confirm(self.conn, self.user("rev2"), sid)
        with self.assertRaises(svc.ServiceError):
            svc.withdraw_sample(self.conn, self.user("buyer"), sid)

    def test_all_exceptions_leave_events_and_notifications(self):
        sid = self.make_sample(code="N-007", seal="FC-E1")
        svc.mark_exception(self.conn, self.user("buyer"), sid, "seal_broken", "破损")
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM events WHERE sample_id=? ORDER BY id", (sid,)).fetchall()]
        self.assertEqual(kinds[0], "receive")
        self.assertIn("seal_broken", kinds)
        note = self.conn.execute(
            "SELECT subject FROM notifications WHERE subject LIKE '%封签破损%'").fetchone()
        self.assertIsNotNone(note)

    def test_over_temp_auto_traced_on_receive(self):
        sid = self.make_sample(code="N-008", seal="FC-E2", temp=5.2, limit=4.0)
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM events WHERE sample_id=?", (sid,)).fetchall()]
        self.assertEqual(kinds, ["receive", "over_temp"])


if __name__ == "__main__":
    unittest.main()
