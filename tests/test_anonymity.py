"""匿名隔离：揭盲前评审人无法获知/查询供应商身份，采购员不能填分。"""
import unittest

from app import services as svc
from tests.support import RuleCase


class AnonymityTest(RuleCase):
    def setUp(self):
        super().setUp()
        self.sid = self.make_sample(code="A-001", seal="FC-A1")
        svc.submit_score(self.conn, self.user("rev1"), self.sid, 8, 7, 15.0, "ok")

    def test_sample_list_hides_supplier_when_blind(self):
        # 不带供应商信息的查询路径（评审页模型）拿不到名字
        row = svc.get_sample(self.conn, self.sid, include_supplier=False)
        self.assertIsNone(row["supplier_name"])

    def test_reviewer_cannot_query_identity_before_unveil(self):
        with self.assertRaises(svc.ServiceError):
            svc.view_supplier_identity(self.conn, self.user("rev1"), self.sid)
        denied = self.audit_for("identity.access_denied")
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["allowed"], 0)

    def test_buyer_cannot_query_identity_before_unveil_either(self):
        # 即使采购员登记时知晓，“身份查询”这一受控动作在揭盲前同样拒绝并审计
        with self.assertRaises(svc.ServiceError):
            svc.view_supplier_identity(self.conn, self.user("buyer"), self.sid)
        self.assertTrue(self.audit_for("identity.access_denied"))

    def test_identity_visible_only_after_dual_unveil(self):
        svc.unveil_confirm(self.conn, self.user("rev1"), self.sid)
        with self.assertRaises(svc.ServiceError):
            svc.view_supplier_identity(self.conn, self.user("rev2"), self.sid)  # 还差一人
        svc.unveil_confirm(self.conn, self.user("rev2"), self.sid)
        row = svc.view_supplier_identity(self.conn, self.user("rev1"), self.sid)
        self.assertEqual(row["supplier_name"], "绿野蔬菜合作社")
        self.assertTrue(self.audit_for("identity.view"))

    def test_buyer_cannot_submit_score(self):
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("buyer"), self.sid, 5, 5, 10.0, "")
        denied = self.audit_for("score.deny_role")
        self.assertEqual(denied[0]["allowed"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"], 1)

    def test_admin_cannot_submit_score_either(self):
        with self.assertRaises(svc.ServiceError):
            svc.submit_score(self.conn, self.user("admin"), self.sid, 5, 5, 10.0, "")

    def test_scores_table_never_stores_supplier(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(scores)").fetchall()}
        self.assertNotIn("supplier_id", cols)


if __name__ == "__main__":
    unittest.main()
