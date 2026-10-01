"""种子数据：用户、供应商、含封签破损样例在内的各种状态样品、本地占位照片、模拟通知。"""
from __future__ import annotations

import os
import struct
import zlib
from datetime import date, timedelta

from . import services as svc
from .db import get_db, init_db

PHOTO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "photos")


def _placeholder_png(path: str, rgb=(95, 122, 58)) -> None:
    """无第三方依赖生成一张占位样品照片（彩色仅用于照片，不用于评分暗示）。"""
    w = h = 160
    r0, g0, b0 = rgb
    raw = bytearray()
    cx = cy = 80
    for y in range(h):
        raw.append(0)  # filter type 0
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            shade = 1.25 if d < 46 else 0.85
            raw += bytes((min(255, int(r0 * shade)),
                          min(255, int(g0 * shade)),
                          min(255, int(b0 * shade))))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as fp:
        fp.write(blob)


def seed(db_path: str) -> bool:
    """空库时灌入演示数据，返回是否执行了播种。"""
    conn = get_db(db_path)
    init_db(conn)
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        conn.close()
        return False

    # ---- 人员 ----
    users = {}
    for username, display, role in [
        ("admin", "王主任", "admin"),
        ("buyer", "李采购", "buyer"),
        ("rev1", "张厨师", "reviewer"),
        ("rev2", "陈师傅", "reviewer"),
        ("rev3", "赵营养师", "reviewer"),
    ]:
        cur = conn.execute("INSERT INTO users(username,display,role) VALUES (?,?,?)",
                           (username, display, role))
        users[username] = cur.lastrowid

    # ---- 供应商（对评审人屏蔽）----
    sups = {}
    for name, contact in [
        ("绿野蔬菜合作社", "钱经理 138-0000-0001"),
        ("鲁西黄牛养殖场", "孙场长 138-0000-0002"),
        ("澄海渔业联社", "周社长 138-0000-0003"),
        ("金穗粮油公司", "吴主管 138-0000-0004"),
    ]:
        cur = conn.execute("INSERT INTO suppliers(name,contact) VALUES (?,?)", (name, contact))
        sups[name] = cur.lastrowid

    admin = {"id": users["admin"], "username": "admin", "display": "王主任", "role": "admin"}
    buyer = {"id": users["buyer"], "username": "buyer", "display": "李采购", "role": "buyer"}
    rev1 = {"id": users["rev1"], "username": "rev1", "display": "张厨师", "role": "reviewer"}
    rev2 = {"id": users["rev2"], "username": "rev2", "display": "陈师傅", "role": "reviewer"}
    rev3 = {"id": users["rev3"], "username": "rev3", "display": "赵营养师", "role": "reviewer"}

    os.makedirs(PHOTO_DIR, exist_ok=True)
    today = date.today()

    def photo(code, rgb):
        name = f"{code}.png"
        _placeholder_png(os.path.join(PHOTO_DIR, name), rgb)
        return name

    def recv(code, cat, origin, temp, limit, batch, seal, loc, sup,
             made, shelf=3, rgb=(95, 122, 58), temp_field_limit=None):
        return svc.receive_sample(
            conn, buyer, code=code, category=cat, origin=origin,
            arrival_temp=temp, temp_limit=limit, batch_no=batch, seal_code=seal,
            storage_loc=loc, supplier_id=sups[sup],
            sample_date=made.isoformat(), shelf_days=shelf, photo_path=photo(code, rgb))

    # 1) 生菜：盲评中，两人已打分，赵营养师已回避 —— 评审人视角供应商不可见
    s1 = recv("YT-2026-001", "生菜", "云南昆明", 2.8, 4, "P260930-A", "FC-001",
              "冷库A-1-01", "绿野蔬菜合作社", today - timedelta(days=1), rgb=(96, 140, 72))
    svc.submit_score(conn, rev1, s1, 8, 7, 18.5, "叶片挺括，焯水后翠绿色")
    svc.submit_score(conn, rev2, s1, 7, 8, 21.0, "规格整齐，泥沙略多")
    svc.apply_recusal(conn, rev3, s1, "与该社社长为邻居，申请回避")
    rid = conn.execute("SELECT id FROM recusals WHERE sample_id=? AND user_id=?",
                       (s1, rev3["id"])).fetchone()["id"]
    svc.decide_recusal(conn, admin, rid, True)

    # 2) 牛腩：三人打分完成且已双人揭盲，待统一选用意见
    s2 = recv("YT-2026-002", "牛腩", "山东聊城", 3.4, 4, "P260929-C", "FC-002",
              "冷库A-1-02", "鲁西黄牛养殖场", today - timedelta(days=2), shelf=5,
              rgb=(150, 92, 60))
    svc.submit_score(conn, rev1, s2, 9, 8, 12.0, "大理石纹明显，炖煮出成率高")
    svc.submit_score(conn, rev2, s2, 8, 9, 14.2, "切块规整，筋膜比例适中")
    svc.submit_score(conn, rev3, s2, 8, 8, 13.5, "香气正，略有血水残留")
    svc.unveil_confirm(conn, rev1, s2)
    svc.unveil_confirm(conn, rev2, s2)

    # 3) 鲈鱼：到货超温，收样即自动标记并留痕，不进入评审
    recv("YT-2026-003", "鲈鱼", "广东澄海", 6.9, 4, "P261001-F", "FC-003",
         "冷库B-2-01（隔离）", "澄海渔业联社", today - timedelta(days=1),
         rgb=(70, 110, 130))

    # 4) 番茄：★封签破损样例★ —— 库管复核留痕、暂停评审、模拟通知验收组
    s4 = recv("YT-2026-004", "番茄", "山东寿光", 3.1, 4, "P260930-B", "FC-004",
              "冷库A-1-03", "绿野蔬菜合作社", today - timedelta(days=1), rgb=(168, 96, 70))
    svc.mark_exception(conn, buyer, s4, "seal_broken",
                       "入库复核发现封签边缘撕裂约 2cm，已拍照并暂停该样品评审",
                       status="seal_broken")

    # 5) 豆腐：样品过期（库中存放超保质期）
    s5 = recv("YT-2026-005", "豆腐", "本市豆坊", 2.5, 4, "P260925-D", "FC-005",
              "冷库A-2-04（隔离）", "绿野蔬菜合作社", today - timedelta(days=6),
              shelf=3, rgb=(214, 200, 150))
    svc.mark_exception(conn, admin, s5, "expired",
                       f"保质期 3 天，生产日期 {(today - timedelta(days=6)).isoformat()}，已过期",
                       status="expired")

    # 6) 冷鲜鸡：全新封样，等待盲评
    recv("YT-2026-006", "冷鲜鸡", "江苏南通", 3.8, 4, "P261001-E", "FC-006",
         "冷库B-2-02", "澄海渔业联社", today, rgb=(190, 178, 150))

    # 7) 大米：供应商撤样 —— 封存编号 FC-007 随即释放
    s7 = recv("YT-2026-007", "大米", "黑龙江五常", 21.0, 25, "P260920-G", "FC-007",
              "常温库C-3-01", "金穗粮油公司", today - timedelta(days=10), shelf=180,
              rgb=(200, 190, 150))
    svc.withdraw_sample(conn, buyer, s7)

    # 8) 小米：复用已释放的封存编号 FC-007（撤样后一份封存编号只关联这一份当前样品）
    recv("YT-2026-008", "小米", "山西沁县", 20.0, 25, "P261001-H", "FC-007",
         "常温库C-3-02", "金穗粮油公司", today - timedelta(days=3), shelf=180,
         rgb=(196, 170, 96))

    conn.close()
    return True
