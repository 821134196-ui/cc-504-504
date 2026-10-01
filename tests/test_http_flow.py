"""HTTP 层冒烟测试 + 种子数据自洽性。"""
import http.client
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

from app.db import get_db
from app.seed import seed
from app.web import Handler


def _post(host, port, path, data=None, cookie=None, files=None):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    headers = {"Cookie": cookie} if cookie else {}
    if files:
        # 简单 multipart 编码
        boundary = "----testboundary123"
        body = b""
        for k, v in (data or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                     f"{v}\r\n").encode()
        for k, (filename, content) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                     f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n").encode()
            body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        conn.request("POST", path, body=body, headers=headers)
    else:
        body = urllib.parse.urlencode(data or {})
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    payload = resp.read().decode("utf-8", errors="replace")
    set_cookie = resp.getheader("Set-Cookie", "")
    location = resp.getheader("Location", "")
    conn.close()
    return resp.status, payload, set_cookie, location


def _get(host, port, path, cookie=None):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    resp = conn.getresponse()
    payload = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return resp.status, payload


class HttpSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "http.db")
        seed(cls.db_path)

        class _H(Handler):
            pass

        _H.db_path = cls.db_path
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        cls.host, cls.port = cls.httpd.server_address
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

        # 取出各角色 uid
        conn = get_db(cls.db_path)
        rows = conn.execute("SELECT id,username FROM users").fetchall()
        cls.uids = {r["username"]: r["id"] for r in rows}
        cls.fresh_sample = conn.execute(
            "SELECT id FROM samples WHERE status='sealed' ORDER BY id LIMIT 1").fetchone()["id"]
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def cookie(self, username):
        return f"uid={self.uids[username]}"

    def test_00_login_required(self):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 303)
        self.assertEqual(resp.getheader("Location"), "/login")
        resp.read()
        conn.close()
        status, body = _get(self.host, self.port, "/login")
        self.assertEqual(status, 200)
        self.assertIn("选择一个身份", body)
        # 未带 Cookie 直接 POST 登录必须成功并下发会话 Cookie
        status, _, set_cookie, location = _post(
            self.host, self.port, "/login", {"uid": str(self.uids["rev1"])})
        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertIn("uid=", set_cookie)

    def test_01_dashboard_blind_for_reviewer(self):
        status, body = _get(self.host, self.port, "/", self.cookie("rev1"))
        self.assertEqual(status, 200)
        # 未揭盲样品不暴露供应商名称（绿野的所有样品都尚未揭盲）
        self.assertNotIn("绿野蔬菜合作社", body)
        # 已揭盲牛腩(2号)的供应商可以显示
        self.assertIn("鲁西黄牛养殖场", body)
        status2, body2 = _get(self.host, self.port, "/samples/2/unveil", self.cookie("rev1"))
        self.assertIn("鲁西黄牛养殖场", body2)

    def test_02_buyer_cannot_open_score_submit(self):
        # 采购员提交评分被拒（HTTP 400 + 审计）
        status, body, _, _ = _post(self.host, self.port,
                                   f"/samples/{self.fresh_sample}/score",
                                   {"taste": "8", "spec": "8", "loss_measured": "10",
                                    "comment": "x"},
                                   self.cookie("buyer"))
        self.assertEqual(status, 400)
        self.assertIn("只有评审人", body)

    def test_03_score_incomplete_rejected(self):
        status, body, _, _ = _post(self.host, self.port,
                                   f"/samples/{self.fresh_sample}/score",
                                   {"taste": "", "spec": "8", "loss_measured": ""},
                                   self.cookie("rev3"))
        self.assertEqual(status, 400)
        self.assertIn("漏项", body)

    def test_04_reviewer_full_score_takes_a_seal_broken_path(self):
        # rev3 对冷鲜鸡完整评分
        status, _, _, loc = _post(self.host, self.port,
                                  f"/samples/{self.fresh_sample}/score",
                                  {"taste": "8", "spec": "7", "loss_measured": "13.5",
                                   "comment": "新鲜"}, self.cookie("rev3"))
        self.assertEqual(status, 303)
        self.assertIn("msg=", loc)

    def test_05_identity_query_denied_before_unveil(self):
        status, body = _get(self.host, self.port,
                            f"/samples/{self.fresh_sample}/identity", self.cookie("rev1"))
        # 服务层抛 ServiceError -> 400 页面（GET 包装）
        self.assertEqual(status, 400)
        self.assertIn("揭盲前不得查询", body)
        conn = get_db(self.db_path)
        denied = conn.execute(
            "SELECT COUNT(*) c FROM audit_logs WHERE action='identity.access_denied' "
            "AND allowed=0").fetchone()["c"]
        conn.close()
        self.assertGreaterEqual(denied, 1)

    def test_06_seed_contains_seal_broken_example(self):
        status, body = _get(self.host, self.port, "/", self.cookie("admin"))
        self.assertIn("封签破损", body)
        self.assertIn("YT-2026-004", body)

    def test_07_recusal_flow_blocks_reviewer(self):
        # rev2 对新样品（6号冷鲜鸡已被 rev3 评分，仍可回避）申请回避
        status, _, _, _ = _post(self.host, self.port,
                                f"/samples/{self.fresh_sample}/recusal",
                                {"reason": "HTTP 流程回避"}, self.cookie("rev2"))
        self.assertEqual(status, 303)
        conn = get_db(self.db_path)
        rid = conn.execute("SELECT id FROM recusals WHERE user_id=? ORDER BY id DESC LIMIT 1",
                           (self.uids["rev2"],)).fetchone()["id"]
        conn.close()
        _post(self.host, self.port, f"/recusals/{rid}/decide",
              {"approve": "1"}, self.cookie("admin"))
        status, body, _, _ = _post(self.host, self.port,
                                   f"/samples/{self.fresh_sample}/score",
                                   {"taste": "5", "spec": "5", "loss_measured": "9"},
                                   self.cookie("rev2"))
        self.assertEqual(status, 400)
        self.assertIn("回避", body)

    def test_08_dual_unveil_via_http_single_result(self):
        # 用 1 号生菜（已有两人评分，rev3 已回避）；rev1+rev2 双人揭盲
        path = "/samples/1/unveil"
        s1, _, _, loc1 = _post(self.host, self.port, path, {}, self.cookie("rev1"))
        self.assertEqual(s1, 303)
        self.assertIn("等待第二位", urllib.parse.unquote(loc1))
        s2, _, _, loc2 = _post(self.host, self.port, path, {}, self.cookie("rev2"))
        self.assertEqual(s2, 303)
        self.assertIn("揭盲完成", urllib.parse.unquote(loc2))
        # 再来一次
        s3, _, _, loc3 = _post(self.host, self.port, path, {}, self.cookie("rev1"))
        self.assertIn("已揭盲", urllib.parse.unquote(loc3))
        conn = get_db(self.db_path)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) c FROM unveils WHERE sample_id=1").fetchone()["c"], 1)
        conn.close()

    def test_09_decision_and_review_flow(self):
        # 2 号牛腩在种子里已揭盲：管理员形成意见，再复核
        s, _, _, _ = _post(self.host, self.port, "/samples/2/decision",
                           {"result": "select", "opinion": "综合口感与损耗，建议选用"},
                           self.cookie("admin"))
        self.assertEqual(s, 303)
        s2, body = _get(self.host, self.port, "/reviews", self.cookie("admin"))
        self.assertIn("建议选用", body)
        s3, _, _, _ = _post(self.host, self.port, "/samples/2/review",
                            {"passed": "1", "note": "评分与揭盲记录齐全"},
                            self.cookie("admin"))
        self.assertEqual(s3, 303)

    def test_10_audit_page_admin_only(self):
        s, body = _get(self.host, self.port, "/audit", self.cookie("admin"))
        self.assertEqual(s, 200)
        self.assertIn("identity.access_denied", body)
        s2, _ = _get(self.host, self.port, "/audit", self.cookie("rev1"))
        self.assertEqual(s2, 303)


if __name__ == "__main__":
    unittest.main()
