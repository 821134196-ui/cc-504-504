"""业务规则层：收样、异常留痕、回避、盲评、双人揭盲竞态、选用意见、复核、身份查询审计。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta


class ServiceError(Exception):
    """业务规则违例（会以 4xx 展示给用户）。"""


# ---------- 通知：模拟适配器 ----------
class MockNotifier:
    name = "mock"

    def send(self, conn: sqlite3.Connection, to_user: str, subject: str, body: str) -> None:
        conn.execute(
            "INSERT INTO notifications(channel,to_user,subject,body) VALUES (?,?,?,?)",
            (self.name, to_user, subject, body),
        )


notifier = MockNotifier()


# ---------- 基础查询 ----------
def list_samples(conn, include_supplier: bool = False):
    cols = ("s.*, sup.name AS supplier_name" if include_supplier
            else "s.*, NULL AS supplier_name")
    return conn.execute(
        f"SELECT {cols} FROM samples s JOIN suppliers sup ON sup.id=s.supplier_id "
        "ORDER BY s.id"
    ).fetchall()


def get_sample(conn, sample_id: int, include_supplier: bool = False):
    cols = ("s.*, sup.name AS supplier_name" if include_supplier
            else "s.*, NULL AS supplier_name")
    row = conn.execute(
        f"SELECT {cols} FROM samples s JOIN suppliers sup ON sup.id=s.supplier_id "
        "WHERE s.id=?", (sample_id,)
    ).fetchone()
    if row is None:
        raise ServiceError("样品不存在")
    return row


def is_revealed(conn, sample_id: int) -> bool:
    return conn.execute("SELECT 1 FROM unveils WHERE sample_id=?", (sample_id,)).fetchone() is not None


def active_recusal(conn, sample_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT status FROM recusals WHERE sample_id=? AND user_id=?",
        (sample_id, user_id),
    ).fetchone()
    return row is not None and row["status"] == "approved"


# ---------- 审计 ----------
def audit(conn, user, action: str, target: str = "", allowed: bool = True, detail: str = ""):
    conn.execute(
        "INSERT INTO audit_logs(user_id,username,action,target,allowed,detail) "
        "VALUES (?,?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else "anonymous",
         action, target, 1 if allowed else 0, detail),
    )


# ---------- 收样与异常留痕 ----------
def receive_sample(conn, user, *, code, category, origin, arrival_temp, temp_limit,
                   batch_no, seal_code, storage_loc, supplier_id, sample_date,
                   shelf_days=3, photo_path=None):
    arrival_temp = float(arrival_temp)
    s = conn.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    if s is None:
        raise ServiceError("供应商不存在")
    clash = conn.execute(
        "SELECT code FROM samples WHERE seal_code=? AND status!='withdrawn'",
        (seal_code,)).fetchone()
    if clash:
        raise ServiceError(
            f"封存编号 {seal_code} 已关联另一份当前样品（{clash['code']}），不能重复使用；"
            "需原样品撤样后该编号才会释放")
    try:
        cur = conn.execute(
            "INSERT INTO samples(code,category,origin,arrival_temp,temp_limit,batch_no,"
            "seal_code,storage_loc,supplier_id,photo_path,sample_date,shelf_days,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'sealed')",
            (code, category, origin, arrival_temp, float(temp_limit), batch_no,
             seal_code, storage_loc, supplier_id, photo_path, sample_date, int(shelf_days)),
        )
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "samples.code" in msg:
            raise ServiceError(f"样品编号 {code} 已存在")
        if "idx_seal_active" in msg:
            raise ServiceError(f"封存编号 {seal_code} 已关联另一份当前样品，不能重复使用")
        raise ServiceError("登记失败：数据冲突")
    sample_id = cur.lastrowid
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (sample_id, "receive",
         f"收样登记：{category}，产地 {origin}，到货温度 {arrival_temp}℃（上限 {temp_limit}℃），"
         f"批次 {batch_no}，封存 {seal_code}，库位 {storage_loc}",
         user["username"]),
    )
    audit(conn, user, "sample.receive", code, detail=f"seal={seal_code}")

    # 到货自动判超温（仍登记，留痕并置异常态，不可进入评审）
    if arrival_temp > float(temp_limit):
        mark_exception(conn, user, sample_id, "over_temp",
                       f"到货温度 {arrival_temp}℃ 超过上限 {temp_limit}℃", status="over_temp")
    return sample_id


def mark_exception(conn, user, sample_id: int, kind: str, detail: str,
                   status: str | None = None):
    """封签破损/超温/样品过期/撤样等异常统一留痕。"""
    get_sample(conn, sample_id)
    allowed_kinds = {"seal_broken", "over_temp", "expired", "withdraw", "status"}
    if kind not in allowed_kinds:
        raise ServiceError("未知异常类型")
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (sample_id, kind, detail, user["username"] if user else "system"),
    )
    if status:
        conn.execute("UPDATE samples SET status=? WHERE id=?", (status, sample_id))
    audit(conn, user, f"sample.{kind}", str(sample_id), detail=detail)
    if kind == "seal_broken":
        notifier.send(conn, "验收组", "样品封签破损", f"样品 #{sample_id} 封签破损，已留痕并暂停评审。")
    if kind == "withdraw":
        notifier.send(conn, "评审组", "供应商撤样", f"样品 #{sample_id} 已被供应商撤回，封存编号释放。")


def withdraw_sample(conn, user, sample_id: int):
    sample = get_sample(conn, sample_id)
    if is_revealed(conn, sample_id):
        raise ServiceError("样品已揭盲，不能撤样")
    if sample["status"] == "withdrawn":
        raise ServiceError("样品已撤样")
    mark_exception(conn, user, sample_id, "withdraw",
                   f"供应商撤样：封存编号 {sample['seal_code']} 释放，库位 {sample['storage_loc']} 清空",
                   status="withdrawn")


def is_expired(sample) -> bool:
    try:
        made = datetime.strptime(sample["sample_date"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return date.today() > made + timedelta(days=int(sample["shelf_days"]))


def evaluable(sample) -> bool:
    """只有封存完好、在温区、未过期、未撤样、尚未揭盲的样品可评审。"""
    return sample["status"] in ("sealed", "evaluating")


# ---------- 回避 ----------
def apply_recusal(conn, user, sample_id: int, reason: str):
    get_sample(conn, sample_id)
    if not reason.strip():
        raise ServiceError("请填写回避理由")
    try:
        conn.execute(
            "INSERT INTO recusals(sample_id,user_id,reason,status) VALUES (?,?,?, 'pending')",
            (sample_id, user["id"], reason.strip()),
        )
    except sqlite3.IntegrityError:
        raise ServiceError("已提交过该样品的回避申请")
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (sample_id, "recusal_apply", f"{user['display']} 申请回避：{reason.strip()}", user["username"]),
    )
    audit(conn, user, "recusal.apply", str(sample_id), detail=reason.strip())


def decide_recusal(conn, admin, recusal_id: int, approve: bool):
    row = conn.execute("SELECT * FROM recusals WHERE id=?", (recusal_id,)).fetchone()
    if row is None:
        raise ServiceError("回避申请不存在")
    if row["status"] != "pending":
        raise ServiceError("该申请已处理")
    status = "approved" if approve else "rejected"
    conn.execute("UPDATE recusals SET status=? WHERE id=?", (status, recusal_id))
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (row["sample_id"], "recusal_decide",
         f"回避申请{('批准' if approve else '驳回')}：{row['reason']}", admin["username"]),
    )
    audit(conn, admin, "recusal.decide", str(row["sample_id"]),
          detail=f"{status} recusal#{recusal_id}")


# ---------- 盲评 ----------
def submit_score(conn, user, sample_id: int, taste, spec, loss_measured, comment: str):
    if user["role"] != "reviewer":
        audit(conn, user, "score.deny_role", str(sample_id), allowed=False,
              detail="非评审人试图填分")
        raise ServiceError("只有评审人可以填分，采购员不能替评审人填分")
    sample = get_sample(conn, sample_id)
    if active_recusal(conn, sample_id, user["id"]):
        audit(conn, user, "score.deny_recused", str(sample_id), allowed=False)
        raise ServiceError("您已回避该样品，不能评分")
    if not evaluable(sample):
        raise ServiceError(f"样品当前状态为 {sample['status']}，不能评分")
    if is_expired(sample):
        mark_exception(conn, user, sample_id, "expired",
                       "评审时发现样品已过保质期", status="expired")
        raise ServiceError("样品已过期，已留痕，不能评分")

    # 评分漏项：三项缺一不可（CHECK 约束是第二道防线）
    missing, vals = [], {}
    for name, raw in (("taste", taste), ("spec", spec), ("loss_measured", loss_measured)):
        if raw is None or str(raw).strip() == "":
            missing.append(name)
            continue
        try:
            vals[name] = float(raw)
        except ValueError:
            raise ServiceError(f"{name} 必须是数字")
    if missing:
        labels = {"taste": "口感", "spec": "规格", "loss_measured": "实测烹调损耗"}
        detail = "评分漏项：" + "、".join(labels[m] for m in missing)
        conn.execute(
            "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
            (sample_id, "score_incomplete", f"{user['display']} {detail}", user["username"]))
        audit(conn, user, "score.incomplete", str(sample_id), allowed=False,
              detail="漏项:" + ",".join(missing))
        raise ServiceError(detail)
    if not (1 <= vals["taste"] <= 10 and vals["taste"] == int(vals["taste"])):
        raise ServiceError("口感评分须为 1-10 的整数")
    if not (1 <= vals["spec"] <= 10 and vals["spec"] == int(vals["spec"])):
        raise ServiceError("规格评分须为 1-10 的整数")
    if not (0 <= vals["loss_measured"] < 100):
        raise ServiceError("实测损耗须为 0-100 之间的百分数")

    conn.execute("UPDATE samples SET status='evaluating' WHERE id=? AND status='sealed'",
                 (sample_id,))
    try:
        conn.execute(
            "INSERT INTO scores(sample_id,user_id,taste,spec,loss_measured,comment) "
            "VALUES (?,?,?,?,?,?)",
            (sample_id, user["id"], int(vals["taste"]), int(vals["spec"]),
             vals["loss_measured"], comment.strip()),
        )
    except sqlite3.IntegrityError:
        raise ServiceError("您已提交过该样品的评分（每人每样品仅一条）")
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (sample_id, "score",
         f"{user['display']} 提交盲评：口感 {int(vals['taste'])}，规格 {int(vals['spec'])}，"
         f"实测损耗 {vals['loss_measured']}%", user["username"]),
    )
    audit(conn, user, "score.submit", str(sample_id),
          detail=f"taste={vals['taste']},spec={vals['spec']},loss={vals['loss_measured']}")


def list_scores(conn, sample_id: int):
    return conn.execute(
        "SELECT sc.*, u.display AS reviewer FROM scores sc "
        "JOIN users u ON u.id=sc.user_id WHERE sc.sample_id=? ORDER BY sc.id",
        (sample_id,),
    ).fetchall()


# ---------- 揭盲 ----------
def unveil_confirm(conn, user, sample_id: int):
    """双人同时揭盲：第二个人确认时在单事务内生成唯一结果。

    SQLite 写事务串行化 + unveils.sample_id UNIQUE 保证并发下只有一次结果；
    败者捕获 IntegrityError 回滚。
    """
    if user["role"] not in ("reviewer", "admin"):
        raise ServiceError("采购员不能执行揭盲")
    sample = get_sample(conn, sample_id)
    if active_recusal(conn, sample_id, user["id"]):
        raise ServiceError("您已回避该样品，不能参与揭盲")
    if sample["status"] in ("withdrawn", "seal_broken", "over_temp", "expired", "rejected"):
        raise ServiceError(f"样品状态为 {sample['status']}，异常未解除，不能揭盲")

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT * FROM unveils WHERE sample_id=?", (sample_id,)).fetchone()
        if existing:
            conn.execute("COMMIT")
            audit(conn, user, "unveil.already", str(sample_id), allowed=False,
                  detail="样品已揭盲，重复操作被拒绝")
            return {"already": True}

        confs = conn.execute(
            "SELECT user_id FROM unveil_confirmations WHERE sample_id=?", (sample_id,)
        ).fetchall()
        conf_ids = {r["user_id"] for r in confs}
        conf_ids.discard(user["id"])  # 幂等：同一人重复确认不占位

        if not conf_ids:
            # 第一人：只登记确认
            conn.execute(
                "INSERT OR IGNORE INTO unveil_confirmations(sample_id,user_id) VALUES (?,?)",
                (sample_id, user["id"]),
            )
            conn.execute(
                "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
                (sample_id, "unveil_confirm",
                 f"{user['display']} 已确认揭盲，等待第二人同时确认", user["username"]),
            )
            audit(conn, user, "unveil.confirm", str(sample_id))
            conn.execute("COMMIT")
            return {"waiting": True}

        # 第二人：落唯一揭盲结果（CHECK 保证两人不同；UNIQUE 保证仅一次）
        first_id = next(iter(conf_ids))
        try:
            conn.execute(
                "INSERT INTO unveils(sample_id,confirmer_a,confirmer_b) VALUES (?,?,?)",
                (sample_id, first_id, user["id"]),
            )
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            audit(conn, user, "unveil.race_lost", str(sample_id), allowed=False,
                  detail="并发揭盲竞态：结果已由另一组确认产生")
            return {"race_lost": True}

        conn.execute(
            "INSERT OR IGNORE INTO unveil_confirmations(sample_id,user_id) VALUES (?,?)",
            (sample_id, user["id"]),
        )
        conn.execute("UPDATE samples SET status='revealed' WHERE id=?", (sample_id,))
        supplier = conn.execute(
            "SELECT sup.name FROM suppliers sup JOIN samples s ON s.supplier_id=sup.id "
            "WHERE s.id=?", (sample_id,)).fetchone()["name"]
        conn.execute(
            "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
            (sample_id, "unveiled",
             f"双人同时揭盲完成，供应商身份：{supplier}", user["username"]),
        )
        audit(conn, user, "unveil.complete", str(sample_id), detail=f"supplier={supplier}")
        conn.execute("COMMIT")
        notifier.send(conn, "评审组", "样品揭盲完成",
                      f"样品 #{sample_id} 已双人揭盲，可形成选用意见。")
        return {"revealed": True, "supplier": supplier}
    except Exception:
        # 任何意外都回滚，绝不留下半成品确认
        conn.execute("ROLLBACK")
        raise


def unveil_status(conn, sample_id: int):
    confs = conn.execute(
        "SELECT uc.user_id, u.display, uc.created_at FROM unveil_confirmations uc "
        "JOIN users u ON u.id=uc.user_id WHERE uc.sample_id=? ORDER BY uc.id",
        (sample_id,),
    ).fetchall()
    unveil = conn.execute("SELECT * FROM unveils WHERE sample_id=?", (sample_id,)).fetchone()
    return confs, unveil


# ---------- 揭盲后：身份查询（审计）、选用意见、复核 ----------
def view_supplier_identity(conn, user, sample_id: int):
    """揭盲前任何评审人不得查询供应商身份。"""
    sample = get_sample(conn, sample_id, include_supplier=True)
    revealed = is_revealed(conn, sample_id)
    if not revealed:
        audit(conn, user, "identity.access_denied", sample["code"], allowed=False,
              detail="揭盲前查询供应商身份被拒绝")
        raise ServiceError("揭盲前不得查询供应商身份，本次访问已记录审计")
    audit(conn, user, "identity.view", sample["code"], detail="揭盲后查看")
    return sample


def create_decision(conn, user, sample_id: int, result: str, opinion: str):
    if not is_revealed(conn, sample_id):
        raise ServiceError("尚未揭盲，不能形成选用意见")
    if result not in ("select", "reject"):
        raise ServiceError("选用结论无效")
    try:
        conn.execute(
            "INSERT INTO decisions(sample_id,result,opinion,decided_by) VALUES (?,?,?,?)",
            (sample_id, result, opinion.strip(), user["id"]),
        )
    except sqlite3.IntegrityError:
        raise ServiceError("该样品已有选用意见")
    conn.execute("UPDATE samples SET status='decision' WHERE id=?", (sample_id,))
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (sample_id, "decision",
         f"统一选用意见：{'选用' if result == 'select' else '不选用'}。{opinion.strip()}",
         user["username"]),
    )
    audit(conn, user, "decision.create", str(sample_id), detail=result)
    notifier.send(conn, "采购组", "选用意见已形成",
                  f"样品 #{sample_id} 结论：{'选用' if result == 'select' else '不选用'}。")


def submit_review(conn, user, sample_id: int, passed: bool, note: str):
    decision = conn.execute(
        "SELECT * FROM decisions WHERE sample_id=?", (sample_id,)).fetchone()
    if decision is None:
        raise ServiceError("尚无选用意见，不能复核")
    try:
        conn.execute(
            "INSERT INTO reviews(sample_id,user_id,passed,note) VALUES (?,?,?,?)",
            (sample_id, user["id"], 1 if passed else 0, note.strip()),
        )
    except sqlite3.IntegrityError:
        raise ServiceError("您已复核过该样品")
    conn.execute(
        "INSERT INTO events(sample_id,kind,detail,actor) VALUES (?,?,?,?)",
        (sample_id, "review",
         f"{user['display']} 复核{'通过' if passed else '不通过'}：{note.strip()}",
         user["username"]),
    )
    audit(conn, user, "review.submit", str(sample_id), detail="pass" if passed else "fail")
