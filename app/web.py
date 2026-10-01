"""HTTP 层：零依赖（标准库 http.server）。

页面：收样登记 / 封样库位 / 盲评表 / 回避申请 / 揭盲 / 结果复核 / 审计。
配色：白底 + 炭灰文字/描边 + 少量食材绿（仅用于品牌与主按钮），
评分一律以数字呈现，不用颜色暗示高低。
"""
from __future__ import annotations

import cgi
import html
import os
import sqlite3
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import services as svc
from .db import get_db, init_db

PHOTO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "photos")

STATUS_TEXT = {
    "sealed": "已封样", "evaluating": "盲评中", "revealed": "已揭盲",
    "decision": "已出意见", "withdrawn": "已撤样", "seal_broken": "封签破损",
    "over_temp": "到货超温", "expired": "样品过期", "rejected": "不予评审",
}
ABNORMAL = {"seal_broken", "over_temp", "expired", "withdrawn", "rejected"}


# ---------------------------------------------------------------- 模板
def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def layout(title: str, user, body: str) -> str:
    nav_user = ("无人" if not user else
                f"{esc(user['display'])}（{'管理员' if user['role'] == 'admin' else '采购员' if user['role'] == 'buyer' else '评审人'}）")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}｜食材盲评选样</title>
<style>
:root{{--ink:#2f2f2c;--muted:#6f6f69;--line:#dcdcd4;--bg:#ffffff;--soft:#f6f6f1;
      --green:#4f7a3a;--green-d:#3d612c;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
header{{border-bottom:1px solid var(--line);background:#fff}}
.bar{{max-width:1080px;margin:0 auto;padding:12px 20px;display:flex;
 align-items:center;gap:18px;flex-wrap:wrap}}
.brand{{font-weight:700;color:var(--green-d);letter-spacing:1px}}
.brand small{{color:var(--muted);font-weight:400;margin-left:8px}}
nav{{display:flex;gap:14px;flex-wrap:wrap}}
nav a{{color:var(--ink);text-decoration:none;border-bottom:2px solid transparent;
 padding-bottom:2px}}
nav a:hover{{border-bottom-color:var(--green)}}
.who{{margin-left:auto;color:var(--muted);font-size:13px}}
.who a{{color:var(--ink)}}
main{{max-width:1080px;margin:0 auto;padding:24px 20px 60px}}
h1{{font-size:21px;margin:0 0 4px}} h2{{font-size:16px;margin:26px 0 10px}}
.sub{{color:var(--muted);margin:0 0 20px;font-size:13px}}
.card{{border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin:14px 0;
 background:#fff}}
.card.soft{{background:var(--soft)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}}
th{{background:var(--soft);font-weight:600;white-space:nowrap}}
.num{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.tag{{display:inline-block;border:1px solid var(--ink);border-radius:3px;
 padding:1px 8px;font-size:12px;white-space:nowrap}}
.tag.abn{{border-style:dashed}}
.muted{{color:var(--muted)}} .blind{{letter-spacing:3px}}
label{{display:block;font-size:13px;margin:10px 0 4px;color:var(--ink)}}
input,select,textarea{{width:100%;padding:7px 9px;border:1px solid #bbb;border-radius:5px;
 font:inherit;background:#fff}}
textarea{{min-height:64px}}
.row{{display:flex;gap:14px;flex-wrap:wrap}} .row>div{{flex:1;min-width:180px}}
.btn{{display:inline-block;border:1px solid var(--green-d);background:var(--green);
 color:#fff!important;padding:7px 16px;border-radius:5px;text-decoration:none;
 cursor:pointer;font:inherit}}
.btn:hover{{background:var(--green-d)}}
.btn.ghost{{background:#fff;color:var(--ink)!important;border-color:var(--ink)}}
.btn.small{{padding:3px 10px;font-size:13px}}
.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}}
.msg{{border:1px solid var(--green);background:var(--soft);padding:9px 12px;
 border-radius:5px;margin-bottom:14px}}
.err{{border:1px dashed var(--ink);padding:9px 12px;border-radius:5px;margin-bottom:14px}}
.section-l{{border-left:3px solid var(--green);padding-left:12px;margin:18px 0 6px;
 font-weight:600}}
.timeline{{list-style:none;margin:8px 0;padding:0;border-left:1px solid var(--line)}}
.timeline li{{padding:4px 0 4px 14px;position:relative;font-size:13px}}
.timeline li::before{{content:"";position:absolute;left:-4px;top:11px;width:7px;height:7px;
 background:#fff;border:1px solid var(--ink);border-radius:50%}}
.timeline time{{color:var(--muted);margin-right:8px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
@media(max-width:820px){{.grid2{{grid-template-columns:1fr}}}}
.photo{{max-width:260px;border:1px solid var(--line);border-radius:6px}}
.score-pill{{border:1px solid var(--line);border-radius:4px;padding:1px 7px;
 font-variant-numeric:tabular-nums}}
code{{background:var(--soft);padding:1px 5px;border-radius:3px}}
</style></head><body>
<header><div class="bar">
 <span class="brand">食材盲评选样 <small>收样 · 封样 · 盲评 · 揭盲 · 复核</small></span>
 <nav><a href="/">样品台</a><a href="/receive">收样登记</a>
  <a href="/recusals">回避审批</a><a href="/reviews">结果复核</a>
  <a href="/audit">访问审计</a></nav>
 <span class="who">当前：{nav_user} · <a href="/login">切换用户</a></span>
</div></header><main>
<h1>{esc(title)}</h1>
{body}
</main></body></html>"""


def flash(msg: str, err: str | None) -> str:
    out = ""
    if msg:
        out += f'<div class="msg">{esc(msg)}</div>'
    if err:
        out += f'<div class="err">⚠ {esc(err)}</div>'
    return out


def login_page(users, err=""):
    opts = "".join(
        f'<option value="{u["id"]}">{esc(u["display"])}｜'
        f'{("管理员" if u["role"] == "admin" else "采购员" if u["role"] == "buyer" else "评审人")}'
        f'（{esc(u["username"])}）</option>' for u in users)
    body = (flash("", err) +
            '<p class="sub">本地演示登录：选择一个身份进入。评审人在揭盲前无法看到供应商名称。</p>'
            '<form method="post" action="/login" class="card" style="max-width:420px">'
            '<label>以谁的身份使用</label><select name="uid">' + opts + '</select>'
            '<div style="margin-top:14px"><button class="btn">进入系统</button></div></form>')
    return layout("登录", None, body)


def status_tag(status) -> str:
    cls = "tag abn" if status in ABNORMAL else "tag"
    return f'<span class="{cls}">{esc(STATUS_TEXT.get(status, status))}</span>'


def dashboard(samples, user, msg="", err=""):
    reviewer = user["role"] == "reviewer"
    trs = []
    for s in samples:
        # 评审人在揭盲前看不到供应商；采购员/管理员登记时已知晓
        if reviewer and s["status"] not in ("revealed", "decision"):
            sup = '<span class="muted blind">盲样</span>'
        else:
            sup = esc(s["supplier_name"])
        acts = []
        sid = s["id"]
        if s["status"] in ("sealed", "evaluating"):
            acts.append('<a class="btn small" href="/samples/%d/score">盲评</a>' % sid)
            acts.append('<a class="btn small ghost" href="/samples/%d/recusal">回避</a>' % sid)
            acts.append('<a class="btn small ghost" href="/samples/%d/unveil">揭盲</a>' % sid)
        if s["status"] in ("revealed", "decision"):
            acts.append('<a class="btn small" href="/samples/%d/unveil">揭盲页</a>' % sid)
        if s["status"] == "revealed":
            acts.append('<a class="btn small ghost" href="/samples/%d/decision">选用意见</a>' % sid)
        if s["status"] == "decision":
            acts.append('<a class="btn small" href="/reviews">结果复核</a>' % sid)
        if s["status"] not in ("withdrawn",) and s["status"] not in ("revealed", "decision"):
            acts.append(
                '<form method="post" action="/samples/%d/withdraw" style="display:inline">'
                '<button class="btn small ghost">撤样留痕</button></form>' % sid)
        trs.append(f"""<tr>
<td class="num"><a href="/samples/{sid}"><code>{esc(s['code'])}</code></a></td>
<td>{esc(s['category'])}</td><td>{esc(s['origin'])}</td>
<td class="num">{s['arrival_temp']:g}℃</td>
<td class="num">{esc(s['seal_code'])}</td><td>{esc(s['storage_loc'])}</td>
<td>{sup}</td><td>{status_tag(s['status'])}</td>
<td class="actions">{''.join(acts) or '<span class="muted">—</span>'}</td></tr>""")
    body = flash(msg, err) + f"""
<p class="sub">共 {len(samples)} 份样品。评审页只按样品编号呈现，不暴露供应商；
评分以数字显示，不用颜色区分高低。</p>
<div class="card" style="overflow-x:auto"><table>
<tr><th>样品编号</th><th>品类</th><th>产地</th><th>到货温度</th><th>封存编号</th>
<th>封样库位</th><th>供应商</th><th>状态</th><th>操作</th></tr>
{''.join(trs)}</table></div>"""
    return layout("样品台", user, body)


def receive_page(user, msg="", err="", form=None):
    f = form or {}
    body = flash(msg, err) + """
<p class="sub">收取供应商试样：登记品类、产地、到货温度、批次与封存编号，拍照本地存档。
到货温度超过上限将自动标记“到货超温”并留痕，样品不进入评审。</p>
<form class="card" method="post" action="/receive" enctype="multipart/form-data">
<div class="row">
<div><label>样品编号 *</label><input name="code" required placeholder="如 YT-2026-006"></div>
<div><label>品类 *</label><input name="category" required placeholder="如 生菜 / 牛腩"></div>
<div><label>产地 *</label><input name="origin" required placeholder="如 云南昆明"></div>
</div>
<div class="row">
<div><label>到货温度(℃) *</label><input name="arrival_temp" type="number" step="0.1" required></div>
<div><label>温控上限(℃)</label><input name="temp_limit" type="number" step="0.1" value="4"></div>
<div><label>生产日期 *</label><input name="date" type="date" required></div>
<div><label>保质期(天)</label><input name="shelf_days" type="number" value="3"></div>
</div>
<div class="row">
<div><label>批次 *</label><input name="batch_no" required placeholder="如 P261001"></div>
<div><label>封存编号 *</label><input name="seal_code" required placeholder="如 FC-006（一份当前样品唯一）"></div>
<div><label>封样库位 *</label><input name="storage_loc" required placeholder="如 A-2-05"></div>
</div>
<div class="row">
<div><label>供应商（对评审人屏蔽）*</label><select name="supplier_id">"""
    for sup in f.get("suppliers", []):
        body += f'<option value="{sup["id"]}">{esc(sup["name"])}</option>'
    body += """</select></div>
<div><label>样品照片（本地存档）</label><input type="file" name="photo" accept="image/*"></div>
</div>
<div style="margin-top:14px"><button class="btn">登记收样并封样</button>
<a class="btn ghost" href="/">返回</a></div></form>"""
    return layout("收样登记", user, body)


def detail_page(conn, user, s, msg="", err=""):
    reviewer = user["role"] == "reviewer"
    revealed = svc.is_revealed(conn, s["id"])
    if reviewer and not revealed:
        sup = '<span class="muted blind">揭盲后可见</span>'
    else:
        sup = esc(s["supplier_name"])
    expired = svc.is_expired(s)
    photo = ""
    if s["photo_path"]:
        photo = (f'<div><h2>样品照片</h2><img class="photo" src="/photos/{esc(os.path.basename(s["photo_path"]))}" '
                 f'alt="样品照片"><div class="muted" style="margin-top:4px">本地存档：{esc(os.path.basename(s["photo_path"]))}</div></div>')
    events = conn.execute("SELECT * FROM events WHERE sample_id=? ORDER BY id", (s["id"],)).fetchall()
    tl = "".join(
        f'<li><time>{esc(e["created_at"])}</time><b>{esc(e["kind"])}</b> '
        f'{esc(e["detail"])} <span class="muted">— {esc(e["actor"])}</span></li>'
        for e in events)
    scores = svc.list_scores(conn, s["id"])
    if scores:
        scr = ["<div class='card'><div class='section-l'>盲评意见（按评分顺序）</div><table>",
               "<tr><th>评审人</th><th>口感(1-10)</th><th>规格(1-10)</th>"
               "<th>实测损耗(%)</th><th>备注</th></tr>"]
        for r in scores:
            scr.append(f'<tr><td>{esc(r["reviewer"])}</td>'
                       f'<td class="num"><span class="score-pill">{r["taste"]}</span></td>'
                       f'<td class="num"><span class="score-pill">{r["spec"]}</span></td>'
                       f'<td class="num"><span class="score-pill">{r["loss_measured"]:g}</span></td>'
                       f'<td>{esc(r["comment"]) or "—"}</td></tr>')
        avg_t = sum(r["taste"] for r in scores) / len(scores)
        avg_s = sum(r["spec"] for r in scores) / len(scores)
        avg_l = sum(r["loss_measured"] for r in scores) / len(scores)
        scr.append(f'<tr><th>均值</th><th class="num">{avg_t:.1f}</th>'
                   f'<th class="num">{avg_s:.1f}</th><th class="num">{avg_l:.1f}</th><th></th></tr>')
        scr.append("</table></div>")
        score_html = "".join(scr)
    else:
        score_html = '<p class="muted">尚无评审意见。</p>'

    abnormal_btns = ""
    if s["status"] in ("sealed", "evaluating"):
        abnormal_btns = """
<h2>异常留痕</h2><div class="card soft">
<p class="muted" style="margin-top:0">封签破损、超温、过期等异常均写入痕迹并暂停该样品流程。</p>
<div class="actions">
<form method="post" action="/samples/%d/exception"><input type="hidden" name="kind" value="seal_broken">
<input name="detail" placeholder="封签破损情况描述（可选）"><button class="btn ghost">登记封签破损</button></form>
<form method="post" action="/samples/%d/exception"><input type="hidden" name="kind" value="expired">
<input name="detail" placeholder="过期情况描述（可选）"><button class="btn ghost">登记样品过期</button></form>
</div></div>""" % (s["id"], s["id"])

    body = flash(msg, err) + f"""
<div class="actions" style="margin-bottom:12px">
<a class="btn ghost small" href="/">← 返回样品台</a>
<a class="btn small" href="/samples/{s['id']}/score">盲评表</a>
<a class="btn ghost small" href="/samples/{s['id']}/unveil">揭盲页</a></div>
<div class="grid2"><div>
<div class="card"><h2 style="margin-top:0">封样与登记信息</h2><table>
<tr><th>样品编号</th><td><code>{esc(s['code'])}</code></td></tr>
<tr><th>品类 / 产地</th><td>{esc(s['category'])} · {esc(s['origin'])}</td></tr>
<tr><th>到货温度</th><td class="num">{s['arrival_temp']:g}℃（上限 {s['temp_limit']:g}℃）</td></tr>
<tr><th>批次</th><td>{esc(s['batch_no'])}</td></tr>
<tr><th>封存编号</th><td class="num">{esc(s['seal_code'])}</td></tr>
<tr><th>封样库位</th><td>{esc(s['storage_loc'])}</td></tr>
<tr><th>生产日期 / 保质期</th><td>{esc(s['sample_date'])} / {s['shelf_days']} 天
{'（<b>已过期</b>）' if expired else ''}</td></tr>
<tr><th>供应商</th><td>{sup}</td></tr>
<tr><th>状态</th><td>{status_tag(s['status'])}</td></tr>
</table></div>{photo}</div>
<div><h2 style="margin-top:0">评审意见（揭盲页对照用）</h2>{score_html}</div></div>
{abnormal_btns}
<h2>全程留痕</h2><ul class="timeline">{tl or '<li class="muted">暂无</li>'}</ul>"""
    return layout(f"样品 {s['code']}", user, body)


def score_page(conn, user, s, msg="", err=""):
    existing = conn.execute(
        "SELECT * FROM scores WHERE sample_id=? AND user_id=?", (s["id"], user["id"])
    ).fetchone()
    if user["role"] != "reviewer":
        form = ('<div class="err">采购员及其他非评审角色不能填分，更不能替评审人填分。'
                "本次访问将被审计。</div>")
    elif existing:
        form = ('<p class="msg">您已提交过该样品评分（每人仅一条）：'
                f'口感 {existing["taste"]}、规格 {existing["spec"]}、'
                f'实测损耗 {existing["loss_measured"]:g}%</p>')
    else:
        form = """
<form method="post" action="/samples/%d/score" class="card">
<p class="muted" style="margin-top:0">您正在盲评 <code>%s</code>。
页面不提供、系统也禁止查询供应商身份。感官评分与实测损耗请分区填写，三项均不得漏项。</p>
<div class="section-l">感官评分（1–10 整数）</div>
<div class="row">
<div><label>口感 *</label><input name="taste" type="number" min="1" max="10" step="1" required></div>
<div><label>规格 *</label><input name="spec" type="number" min="1" max="10" step="1" required></div>
</div>
<div class="section-l">实测数据</div>
<label>烹调损耗（%%，0–100 实测值）*</label>
<input name="loss_measured" type="number" min="0" max="99.9" step="0.1" required>
<label>评语</label><textarea name="comment" placeholder="口感与规格的具体观察（可选）"></textarea>
<div style="margin-top:14px"><button class="btn">提交盲评</button>
<a class="btn ghost" href="/samples/%d">返回</a></div></form>""" % (s["id"], esc(s["code"]), s["id"])
    body = flash(msg, err) + form
    return layout(f"盲评表 · {s['code']}", user, body)


def recusal_page(conn, user, s, msg="", err=""):
    mine = conn.execute(
        "SELECT * FROM recusals WHERE sample_id=? AND user_id=?", (s["id"], user["id"])
    ).fetchone()
    if mine:
        form = (f'<p class="msg">您已申请回避，状态：{esc(mine["status"])}；'
                f'理由：{esc(mine["reason"])}</p>')
    else:
        form = """
<form method="post" class="card" action="/samples/%d/recusal">
<p class="muted" style="margin-top:0">如与 <code>%s</code> 供应商存在利害关系，请申请回避；
批准后将不能对该样品评分或参与揭盲，全程留痕。</p>
<label>回避理由 *</label><textarea name="reason" required placeholder="如：与该供应商有亲属关系"></textarea>
<div style="margin-top:14px"><button class="btn">提交回避申请</button>
<a class="btn ghost" href="/samples/%d">返回</a></div></form>""" % (s["id"], esc(s["code"]), s["id"])
    return layout(f"回避申请 · {s['code']}", user, flash(msg, err) + form)


def recusals_page(conn, user, rows, msg="", err=""):
    trs = []
    for r in rows:
        trs.append(f"""<tr><td class="num">{esc(r['code'])}</td><td>{esc(r['display'])}</td>
<td>{esc(r['reason'])}</td><td>{esc(r['created_at'])}</td><td>{esc(r['status'])}</td>
<td><div class="actions">
<form method="post" action="/recusals/{r['id']}/decide"><input type="hidden" name="approve" value="1">
<button class="btn small">批准</button></form>
<form method="post" action="/recusals/{r['id']}/decide"><input type="hidden" name="approve" value="0">
<button class="btn small ghost">驳回</button></form></div></td></tr>""")
    table = ("<div class='card'><table><tr><th>样品编号</th><th>申请人</th><th>理由</th>"
             "<th>时间</th><th>状态</th><th>处理</th></tr>" + "".join(trs) + "</table></div>"
             if trs else '<p class="muted">没有待处理或已处理的回避申请。</p>')
    return layout("回避审批", user, flash(msg, err) + table)


def unveil_page(conn, user, s, msg="", err=""):
    confs, unveil = svc.unveil_status(conn, s["id"])
    scores = svc.list_scores(conn, s["id"])
    opinions = "".join(
        f'<tr><td>{esc(r["reviewer"])}</td><td class="num">{r["taste"]}</td>'
        f'<td class="num">{r["spec"]}</td><td class="num">{r["loss_measured"]:g}</td>'
        f'<td>{esc(r["comment"]) or "—"}</td></tr>' for r in scores)
    if unveil:
        a = conn.execute("SELECT display FROM users WHERE id=?", (unveil["confirmer_a"],)).fetchone()["display"]
        b = conn.execute("SELECT display FROM users WHERE id=?", (unveil["confirmer_b"],)).fetchone()["display"]
        identity = (f'<div class="msg">双人揭盲已于 {esc(unveil["created_at"])} 完成：'
                    f'{esc(a)} + {esc(b)}。供应商：<b>{esc(s["supplier_name"])}</b>。'
                    '本次揭盲只产生一次结果。</div>')
        confirm = ""
    else:
        who = "、".join(esc(c["display"]) for c in confs) or "（尚无）"
        n = len(confs)
        identity = ('<p class="muted">揭盲前不显示供应商。需两名不同人员（评审人或管理员）'
                    f'先后确认，当前确认 {n}/2：{who}</p>')
        already = any(c["user_id"] == user["id"] for c in confs)
        if svc.active_recusal(conn, s["id"], user["id"]):
            confirm = '<p class="err">您已回避该样品，不能参与揭盲。</p>'
        elif already:
            confirm = '<p class="msg">您已确认，等待第二位人员确认。</p>'
        else:
            confirm = ("<form method='post' action='/samples/%d/unveil'>"
                       '<button class="btn">我确认，参与双人揭盲</button> '
                       '<a class="btn ghost" href="/samples/%d">返回</a></form>' % (s["id"], s["id"]))
    body = flash(msg, err) + f"""
{identity}
<h2>各评审意见对照{'' if unveil else '（供应商仍盲）'}</h2>
<div class="card"><table><tr><th>评审人</th><th>口感</th><th>规格</th>
<th>实测损耗(%)</th><th>评语</th></tr>{opinions or '<tr><td colspan=5 class=muted>尚无评分</td></tr>'}
</table></div>
<h2>双人揭盲确认</h2><div class="card soft">{confirm}
<p class="muted" style="margin-bottom:0">两人同时揭盲在数据库单事务内完成；
即使并发点击，封存结果也只生成一次（竞态落败方被拒绝并审计）。</p></div>"""
    return layout(f"揭盲 · {s['code']}", user, body)


def decision_page(conn, user, s, msg="", err=""):
    d = conn.execute("SELECT * FROM decisions WHERE sample_id=?", (s["id"],)).fetchone()
    if d:
        form = (f'<p class="msg">选用意见已形成：<b>{"选用" if d["result"] == "select" else "不选用"}</b>'
                f'——{esc(d["opinion"])}（{esc(d["created_at"])}）</p>')
    else:
        form = """
<form method="post" class="card" action="/samples/%d/decision">
<p class="sub">供应商已揭盲：<b>%s</b>。请对照各评审意见统一形成结论。</p>
<label>结论 *</label><select name="result"><option value="select">选用</option>
<option value="reject">不选用</option></select>
<label>选用意见</label><textarea name="opinion" required></textarea>
<div style="margin-top:14px"><button class="btn">提交选用意见</button></div></form>
""" % (s["id"], esc(s["supplier_name"]))
    return layout(f"选用意见 · {s['code']}", user, flash(msg, err) + form)


def reviews_page(conn, user, samples, msg="", err=""):
    blocks = []
    for s in samples:
        d = conn.execute("SELECT * FROM decisions WHERE sample_id=?", (s["id"],)).fetchone()
        if not d:
            continue
        revs = conn.execute(
            "SELECT rv.*, u.display FROM reviews rv JOIN users u ON u.id=rv.user_id "
            "WHERE rv.sample_id=? ORDER BY rv.id", (s["id"],)).fetchall()
        rev_html = "".join(
            f'<li>{esc(r["display"])}：<b>{"复核通过" if r["passed"] else "复核不通过"}</b>'
            f' {esc(r["note"])} <span class="muted">{esc(r["created_at"])}</span></li>'
            for r in revs) or '<li class="muted">暂无复核</li>'
        mine = any(r["user_id"] == user["id"] for r in revs)
        form = ""
        if not mine:
            form = """<form method="post" action="/samples/%d/review" class="row" style="align-items:end">
<div><label>复核结论</label><select name="passed"><option value="1">通过</option>
<option value="0">不通过</option></select></div>
<div style="flex:2"><label>说明</label><input name="note"></div>
<div><button class="btn small">提交复核</button></div></form>""" % s["id"]
        blocks.append(f"""<div class="card"><h2 style="margin-top:0"><code>{esc(s['code'])}</code>
 {esc(s['category'])} · 供应商：{esc(s['supplier_name'])} {status_tag(s['status'])}</h2>
<p>选用意见：<b>{'选用' if d['result'] == 'select' else '不选用'}</b> —— {esc(d['opinion'])}</p>
<ul class="timeline">{rev_html}</ul>{form}</div>""")
    body = flash(msg, err) + ("".join(blocks) or '<p class="muted">暂无已形成选用意见的样品。</p>')
    return layout("结果复核", user, body)


def audit_page(conn, user, logs):
    trs = "".join(
        f'<tr><td class="num">{esc(l["created_at"])}</td><td>{esc(l["username"])}</td>'
        f'<td><code>{esc(l["action"])}</code></td><td>{esc(l["target"])}</td>'
        f'<td>{"允许" if l["allowed"] else "<b>拒绝</b>"}</td><td>{esc(l["detail"])}</td></tr>'
        for l in logs)
    body = f"""<p class="sub">所有关键访问都被审计：揭盲前查询供应商身份（拒绝）、
采购员试图填分（拒绝）、回避、评分、并发揭盲竞态等。</p>
<div class="card" style="overflow-x:auto"><table>
<tr><th>时间</th><th>用户</th><th>动作</th><th>对象</th><th>结果</th><th>详情</th></tr>
{trs or '<tr><td colspan=6 class=muted>暂无审计记录</td></tr>'}</table></div>"""
    return layout("访问审计", user, body)


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    server_version = "BlindTaste/1.0"
    db_path = ""

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # --- 基础设施 ---
    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self, "_conn"):
            self._conn = get_db(self.db_path)
        return self._conn

    def close(self):
        c = getattr(self, "_conn", None)
        if c:
            c.close()

    def finish(self, *a):
        self.close()
        super().finish(*a)

    def current_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "uid" not in cookie:
            return None
        try:
            uid = int(cookie["uid"].value)
        except ValueError:
            return None
        return self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def send_html(self, text: str, status: int = 200, cookies: dict | None = None):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (cookies or {}).items():
            self.send_header("Set-Cookie", f"{k}={v}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path: str, msg: str | None = None, err: str | None = None,
                 cookies: dict | None = None):
        qs = {}
        if msg:
            qs["msg"] = msg
        if err:
            qs["err"] = err
        if qs:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode(qs)
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        for k, v in (cookies or {}).items():
            self.send_header("Set-Cookie", f"{k}={v}; Path=/; HttpOnly")
        self.end_headers()

    def q(self):
        u = urllib.parse.urlparse(self.path)
        return u.path.rstrip("/") or "/", urllib.parse.parse_qs(u.query)

    def require_user(self):
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        return user

    def read_form(self):
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                  environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype})
            fields, files = {}, {}
            for key in fs.keys():
                item = fs[key]
                if getattr(item, "filename", None):
                    files[key] = item
                else:
                    fields[key] = item.value
            return fields, files
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}, {}

    def f(self, fields, name, default=""):
        return fields.get(name, default)

    # --- 路由 ---
    def do_GET(self):
        try:
            path, query = self.q()
            if path == "/login":
                users = self.conn.execute("SELECT * FROM users ORDER BY id").fetchall()
                return self.send_html(login_page(users, query.get("err", [""])[0]))
            if path == "/photos":
                return self.redirect("/")
            if path.startswith("/photos/"):
                return self.serve_photo(path.split("/", 2)[2])

            user = self.require_user()
            if not user:
                return
            msg, err = query.get("msg", [""])[0], query.get("err", [""])[0]

            if path == "/":
                samples = svc.list_samples(self.conn, include_supplier=True)
                return self.send_html(dashboard(samples, user, msg, err))
            if path == "/receive":
                if user["role"] not in ("buyer", "admin"):
                    return self.redirect("/", err="只有采购员/管理员可以登记收样")
                sups = self.conn.execute("SELECT * FROM suppliers ORDER BY id").fetchall()
                return self.send_html(receive_page(user, msg, err, {"suppliers": sups}))
            if path == "/recusals":
                if user["role"] != "admin":
                    return self.redirect("/", err="只有管理员可以审批回避")
                rows = self.conn.execute(
                    "SELECT r.*, s.code, u.display FROM recusals r "
                    "JOIN samples s ON s.id=r.sample_id JOIN users u ON u.id=r.user_id "
                    "ORDER BY r.id DESC").fetchall()
                return self.send_html(recusals_page(self.conn, user, rows, msg, err))
            if path == "/reviews":
                samples = svc.list_samples(self.conn, include_supplier=True)
                return self.send_html(reviews_page(self.conn, user, samples, msg, err))
            if path == "/audit":
                if user["role"] != "admin":
                    return self.redirect("/", err="只有管理员可以查看审计")
                logs = self.conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 300").fetchall()
                return self.send_html(audit_page(self.conn, user, logs))

            # /samples/<id>/...
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "samples":
                return self.get_sample_sub(parts, user, msg, err)
            self.send_error_404()
        except svc.ServiceError as e:
            user = self.current_user()
            self.send_html(layout("操作被拒绝", user,
                                  f'<div class="err">⚠ {esc(str(e))}</div>'
                                  '<a class="btn ghost" href="/">返回样品台</a>'), 400)
        except BrokenPipeError:
            pass

    def get_sample_sub(self, parts, user, msg, err):
        sid = int(parts[1])
        s = svc.get_sample(self.conn, sid, include_supplier=True)
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "":
            return self.send_html(detail_page(self.conn, user, s, msg, err))
        if sub == "score":
            return self.send_html(score_page(self.conn, user, s, msg, err))
        if sub == "recusal":
            return self.send_html(recusal_page(self.conn, user, s, msg, err))
        if sub == "unveil":
            return self.send_html(unveil_page(self.conn, user, s, msg, err))
        if sub == "identity":
            s2 = svc.view_supplier_identity(self.conn, user, sid)
            return self.redirect(f"/samples/{sid}",
                                 msg=f"供应商身份：{s2['supplier_name']}（本次查看已审计）")
        if sub == "decision":
            if not svc.is_revealed(self.conn, sid):
                return self.redirect(f"/samples/{sid}", err="尚未揭盲")
            return self.send_html(decision_page(self.conn, user, s, msg, err))
        self.send_error_404()

    def do_POST(self):
        try:
            path, _ = self.q()
            # 登录/登出在身份校验之前放行，否则永远无法建立会话
            if path == "/login":
                fields, _ = self.read_form()
                self.redirect("/", cookies={"uid": self.f(fields, "uid")})
                return
            if path == "/logout":
                self.redirect("/login", cookies={"uid": ""})
                return
            user = self.require_user()
            if not user:
                return
            fields, files = self.read_form()

            if path == "/receive":
                return self.post_receive(user, fields, files)

            parts = path.strip("/").split("/")
            if parts[0] == "samples" and len(parts) >= 3:
                sid = int(parts[1])
                sub = parts[2]
                if sub == "exception":
                    kind = self.f(fields, "kind")
                    svc.mark_exception(self.conn, user, sid, kind,
                                       self.f(fields, "detail") or STATUS_TEXT.get(kind, kind),
                                       status=kind if kind in ("seal_broken", "expired") else None)
                    return self.redirect(f"/samples/{sid}", msg="异常已登记并留痕")
                if sub == "withdraw":
                    svc.withdraw_sample(self.conn, user, sid)
                    return self.redirect(f"/samples/{sid}", msg="撤样已留痕，封存编号已释放")
                if sub == "score":
                    svc.submit_score(
                        self.conn, user, sid,
                        self.f(fields, "taste") or None,
                        self.f(fields, "spec") or None,
                        self.f(fields, "loss_measured") or None,
                        self.f(fields, "comment"))
                    return self.redirect(f"/samples/{sid}", msg="盲评已提交")
                if sub == "recusal":
                    svc.apply_recusal(self.conn, user, sid, self.f(fields, "reason"))
                    return self.redirect(f"/samples/{sid}", msg="回避申请已提交，待管理员审批")
                if sub == "unveil":
                    res = svc.unveil_confirm(self.conn, user, sid)
                    if res.get("revealed"):
                        return self.redirect(f"/samples/{sid}/unveil",
                                             msg=f"揭盲完成，供应商：{res['supplier']}")
                    if res.get("race_lost"):
                        return self.redirect(f"/samples/{sid}/unveil",
                                             err="揭盲结果刚刚已由另一组确认产生，仅一次有效")
                    if res.get("already"):
                        return self.redirect(f"/samples/{sid}/unveil", err="样品已揭盲，无需重复操作")
                    return self.redirect(f"/samples/{sid}/unveil", msg="已确认，等待第二位人员同时揭盲")
                if sub == "decision":
                    svc.create_decision(self.conn, user, sid,
                                        self.f(fields, "result"), self.f(fields, "opinion"))
                    return self.redirect(f"/samples/{sid}", msg="选用意见已形成")
                if sub == "review":
                    svc.submit_review(self.conn, user, sid,
                                      self.f(fields, "passed") == "1", self.f(fields, "note"))
                    return self.redirect("/reviews", msg="复核已提交")
            if parts[0] == "recusals" and len(parts) == 3 and parts[2] == "decide":
                svc.decide_recusal(self.conn, user, int(parts[1]),
                                   self.f(fields, "approve") == "1")
                return self.redirect("/recusals", msg="回避申请已处理")
            self.send_error_404()
        except svc.ServiceError as e:
            user = self.current_user()
            self.send_html(layout("操作被拒绝", user,
                                  f'<div class="err">⚠ {esc(str(e))}</div>'
                                  '<a class="btn ghost" href="/">返回样品台</a>'), 400)

    def post_receive(self, user, fields, files):
        photo_path = None
        item = files.get("photo")
        if item is not None and item.filename and item.file:
            ext = os.path.splitext(item.filename)[1][:5].replace("/", "") or ".png"
            fname = f"{int(time.time() * 1000)}_{self.f(fields, 'code', 's')}{ext}"
            os.makedirs(PHOTO_DIR, exist_ok=True)
            with open(os.path.join(PHOTO_DIR, fname), "wb") as fp:
                fp.write(item.file.read())
            photo_path = fname
        sid = svc.receive_sample(
            self.conn, user,
            code=self.f(fields, "code"), category=self.f(fields, "category"),
            origin=self.f(fields, "origin"), arrival_temp=self.f(fields, "arrival_temp"),
            temp_limit=self.f(fields, "temp_limit", "4"), batch_no=self.f(fields, "batch_no"),
            seal_code=self.f(fields, "seal_code"), storage_loc=self.f(fields, "storage_loc"),
            supplier_id=int(self.f(fields, "supplier_id")),
            sample_date=self.f(fields, "date"), shelf_days=int(self.f(fields, "shelf_days", "3")),
            photo_path=photo_path)
        return self.redirect(f"/samples/{sid}", msg="收样登记完成，已封样入库")

    def serve_photo(self, name):
        name = os.path.basename(urllib.parse.unquote(name))
        target = os.path.normpath(os.path.join(PHOTO_DIR, name))
        if not target.startswith(PHOTO_DIR) or not os.path.isfile(target):
            self.send_response(404)
            self.end_headers()
            return
        ctype = "image/png" if name.lower().endswith(".png") else "image/jpeg"
        with open(target, "rb") as fp:
            data = fp.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_404(self):
        self.send_html(layout("未找到", self.current_user(),
                              '<p>页面不存在。</p><a class="btn ghost" href="/">返回</a>'), 404)


def make_server(db_path: str, host: str = "127.0.0.1", port: int = 8000):
    init_db(get_db(db_path))  # 确保表存在

    class _H(Handler):
        pass

    _H.db_path = db_path
    return ThreadingHTTPServer((host, port), _H)
