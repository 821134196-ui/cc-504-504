"""评审人回避：申请、审批留痕，批准后不能评分/揭盲。"""
import unittest

from app import services as svc
from tests.support import RuleCase


class RecusalTest(RuleCase):
    def test_apply_and_approve_blocks_scoring(self):
        sid = self.make_sample(code="R-001", seal="FC-R1")
        svc.apply_recusal(self.conn, self.user("rev1"), sid, "与供应商负责人是亲属")
        # 待审批时尚不阻止（状态 pending），但可以评分
        self.assertFalse(svc.active_recusal(self.conn, sid, self.uids["rev1"]))

        rid = self.conn.execute(
            "SELECT id FROM recusals WHERE sample_id=? AND user_id=?",
            (sid, self.uids["rev1"])).fetchone()["id"]
        svc.decide_recusal(self.conn, self.user("admin"), rid, True)
        self.assertTrue(svc.active_recusal(self.conn, sid, self.uids["rev1"]))

        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("rev1"), sid, 8, 8, 10.0, "")
        # 其他评审人不受影响
        svc.submit_score(self.conn, self.user("rev2"), sid, 7, 7, 12.0, "")

    def test_rejected_recusal_does_not_block(self):
        sid = self.make_sample(code="R-002", seal="FC-R2")
        svc.apply_recusal(self.conn, self.user("rev1"), sid, "理由不充分")
        rid = self.conn.execute(
            "SELECT id FROM recusals WHERE sample_id=? AND user_id=?",
            (sid, self.uids["rev1"])).fetchone()["id"]
        svc.decide_recusal(self.conn, self.user("admin"), rid, False)
        svc.submit_score(self.conn, self.user("rev1"), sid, 6, 6, 9.0, "")  # 不抛异常

    def test_approved_recusal_blocks_unveil(self):
        sid = self.make_sample(code="R-003", seal="FC-R3")
        svc.apply_recusal(self.conn, self.user("rev1"), sid, "利害关系")
        rid = self.conn.execute(
            "SELECT id FROM recusals WHERE sample_id=? AND user_id=?",
            (sid, self.uids["rev1"])).fetchone()["id"]
        svc.decide_recusal(self.conn, self.user("admin"), rid, True)
        with self.assertRaises(svc.ServiceError):
            svc.unveil_confirm(self.conn, self.user("rev1"), sid)

    def test_recusal_requires_reason_and_is_unique(self):
        sid = self.make_sample(code="R-004", seal="FC-R4")
        with self.assertRaises(svc.ServiceError):
            svc.apply_recusal(self.conn, self.user("rev1"), sid, "  ")
        svc.apply_recusal(self.conn, self.user("rev1"), sid, "曾经任职该供应商")
        with self.assertRaises(svc.ServiceError):
            svc.apply_recusal(self.conn, self.user("rev1"), sid, "再申请一次")

    def test_recusal_decide_idempotent(self):
        sid = self.make_sample(code="R-005", seal="FC-R5")
        svc.apply_recusal(self.conn, self.user("rev1"), sid, "x")
        rid = self.conn.execute(
            "SELECT id FROM recusals WHERE sample_id=? AND user_id=?",
            (sid, self.uids["rev1"])).fetchone()["id"]
        svc.decide_recusal(self.conn, self.user("admin"), rid, True)
        with self.assertRaises(svc.ServiceError):
            svc.decide_recusal(self.conn, self.user("admin"), rid, False)

    def test_recusal_leaves_audit_trail(self):
        sid = self.make_sample(code="R-006", seal="FC-R6")
        svc.apply_recusal(self.conn, self.user("rev1"), sid, "留痕测试")
        self.assertTrue(self.audit_for("recusal.apply"))
        rid = self.conn.execute(
            "SELECT id FROM recusals WHERE sample_id=? AND user_id=?",
            (sid, self.uids["rev1"])).fetchone()["id"]
        svc.decide_recusal(self.conn, self.user("admin"), rid, True)
        self.assertTrue(self.audit_for("recusal.decide"))
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM events WHERE sample_id=?", (sid,)).fetchall()]
        self.assertIn("recusal_apply", kinds)
        self.assertIn("recusal_decide", kinds)


if __name__ == "__main__":
    unittest.main()
