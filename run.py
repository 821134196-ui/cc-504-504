#!/usr/bin/env python3
"""一条本地命令启动食材盲评选样系统。

    python3 run.py                # 初始化 SQLite + 种子数据（含封签破损样例）并启动
    python3 run.py --reset        # 清空演示库与照片后重新播种
    python3 run.py --port 8080

仅依赖 Python 3 标准库；浏览器打开 http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from app.db import get_db
from app.seed import seed
from app.web import make_server

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "tasting.db")


def main() -> int:
    ap = argparse.ArgumentParser(description="食材盲评选样系统（本地零依赖）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true", help="删除旧库与照片后重新播种")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    if args.reset and os.path.exists(DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            p = DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)
        photo_dir = os.path.join(BASE, "data", "photos")
        if os.path.isdir(photo_dir):
            for name in os.listdir(photo_dir):
                os.remove(os.path.join(photo_dir, name))

    fresh = seed(DB_PATH)
    # 预热一次连接，确保表结构就绪
    get_db(DB_PATH).close()

    httpd = make_server(DB_PATH, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 64, flush=True)
    print(" 食材盲评选样系统已启动", flush=True)
    print(f" 地址：{url}", flush=True)
    print(f" 数据库：{DB_PATH}", flush=True)
    print(" 种子数据：" + ("已新建（含封签破损样例 YT-2026-004）" if fresh else "沿用现有库"), flush=True)
    print(" 演示账号：王主任(管理员) / 李采购(采购员) / 张厨师·陈师傅·赵营养师(评审人)", flush=True)
    print(" 提示：先用评审人登录体验盲评页（看不到供应商），再由两人在揭盲页确认", flush=True)
    print(" Ctrl+C 停止", flush=True)
    print("=" * 64, flush=True)
    if not args.no_browser and os.environ.get("DISPLAY"):
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
