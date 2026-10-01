const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const STATUS_TEXT = {
  sealed: '封存中', overtemp: '到货超温', broken: '封签破损',
  expired: '样品过期', withdrawn: '供应商撤样', revealed: '已揭盲',
};

const ROLE_TEXT = { admin: '管理员', buyer: '采购员', reviewer: '评审人' };

function shell({ title, user, active, body, flash }) {
  const nav = user ? `
  <nav class="nav">
    <div class="brand">🥬 食材封样盲评</div>
    <div class="links">
      <a href="/" class="${active === 'home' ? 'on' : ''}">收样登记</a>
      <a href="/storage" class="${active === 'storage' ? 'on' : ''}">封样库位</a>
      ${user.role === 'reviewer' ? `<a href="/review" class="${active === 'review' ? 'on' : ''}">盲评表</a>` : ''}
      ${user.role === 'admin' ? `<a href="/admin/recusals" class="${active === 'recusal' ? 'on' : ''}">回避审批</a>` : ''}
      <a href="/reveal" class="${active === 'reveal' ? 'on' : ''}">揭盲</a>
      <a href="/results" class="${active === 'results' ? 'on' : ''}">结果复核</a>
      ${user.role === 'admin' ? `<a href="/audit" class="${active === 'audit' ? 'on' : ''}">访问审计</a>` : ''}
    </div>
    <form method="post" action="/logout" class="who">
      <span>${esc(user.display_name)} · ${ROLE_TEXT[user.role]}</span>
      <button class="btn-mini" type="submit">退出</button>
    </form>
  </nav>` : '';

  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<style>
:root{
  --paper:#ffffff; --ink:#2e3330; --ink-soft:#5d655e; --line:#d9dcd4;
  --green:#3f6f4e; --green-soft:#e9f1e6; --green-deep:#2f553b;
  --bg:#f6f7f3; --char:#3c4140;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}
.nav{display:flex;align-items:center;gap:20px;padding:0 24px;height:56px;
  background:var(--char);color:#fff;}
.nav .brand{font-weight:600;letter-spacing:.5px}
.nav .links{display:flex;gap:4px;flex:1}
.nav a{color:#d6dcd4;text-decoration:none;padding:6px 12px;border-radius:6px;font-size:14px}
.nav a:hover{background:#4d5350;color:#fff}
.nav a.on{background:var(--green);color:#fff}
.nav .who{display:flex;align-items:center;gap:10px;font-size:13px;color:#c8cec6}
main{max-width:1080px;margin:24px auto;padding:0 20px}
h1{font-size:21px;margin:0 0 4px} h2{font-size:16px;margin:0 0 12px}
.sub{color:var(--ink-soft);font-size:13px;margin-bottom:18px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:10px;
  padding:20px 22px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px 22px}
label{display:block;font-size:13px;color:var(--ink-soft);margin-bottom:4px}
input,select,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);
  border-radius:6px;font:inherit;color:var(--ink);background:#fff}
input:focus,select:focus,textarea:focus{outline:2px solid var(--green-soft);border-color:var(--green)}
.btn{background:var(--green);color:#fff;border:0;border-radius:6px;padding:9px 20px;
  font:inherit;cursor:pointer}
.btn:hover{background:var(--green-deep)}
.btn-ghost{background:#fff;color:var(--char);border:1px solid var(--line)}
.btn-ghost:hover{border-color:var(--green);color:var(--green-deep)}
.btn-mini{background:transparent;border:1px solid #7c837d;color:#d6dcd4;
  border-radius:5px;padding:3px 10px;font-size:12px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink-soft);font-weight:600;font-size:13px;background:#fafbf8}
.tag{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;
  border:1px solid var(--line);color:var(--char);background:#f2f3ef;white-space:nowrap}
.tag.sealed{border-color:#b9cdbb;color:var(--green-deep);background:var(--green-soft)}
.tag.revealed{border-color:#9aa39c;background:#eef0ec}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:16px;font-size:14px}
.flash.ok{background:var(--green-soft);border:1px solid #b9d2bd;color:var(--green-deep)}
.flash.err{background:#f4ece9;border:1px solid #d8bdb2;color:#7a4636}
.muted{color:var(--ink-soft);font-size:13px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
/* 评分两区：仅用炭灰线条分隔，不使用颜色暗示分数高低 */
fieldset{border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:0 0 16px}
legend{padding:0 8px;font-weight:600;color:var(--char)}
.score-pick{display:flex;gap:6px;flex-wrap:wrap}
.score-pick input{position:absolute;opacity:0;width:0;height:0}
.score-pick span{display:inline-flex;align-items:center;justify-content:center;
  width:38px;height:34px;border:1px solid var(--line);border-radius:6px;cursor:pointer;
  background:#fff;color:var(--char);user-select:none}
.score-pick input:checked + span{background:var(--char);color:#fff;border-color:var(--char)}
.score-pick span:hover{border-color:var(--green)}
.two-col{display:grid;grid-template-columns:1.2fr .8fr;gap:20px}
.blind-note{background:#f2f3ef;border-left:4px solid var(--char);padding:10px 14px;
  font-size:13px;color:var(--char);border-radius:0 6px 6px 0;margin-bottom:16px}
.timeline{list-style:none;margin:0;padding:0;font-size:13px}
.timeline li{padding:7px 0 7px 16px;border-left:2px solid var(--line);margin-left:6px}
.timeline li b{color:var(--char);font-weight:600}
.login-wrap{max-width:380px;margin:8vh auto}
.thumb{width:120px;height:78px;object-fit:cover;border:1px solid var(--line);border-radius:6px;background:#f4f5f2}
a{color:var(--green-deep)}
.row-actions{display:flex;gap:8px;flex-wrap:wrap}
.inline{display:inline}
.danger-zone{border-style:dashed}
</style></head>
<body>${nav}<main>
${flash ? `<div class="flash ${flash.kind}">${esc(flash.text)}</div>` : ''}
${body}
</main></body></html>`;
}

export function loginView(flash) {
  return shell({
    title: '登录', user: null, flash,
    body: `<div class="login-wrap card">
      <h1>食材封样盲评系统</h1>
      <p class="sub">演示账号：admin/admin123 · buyer/buyer123 · r1/rev123、r2/rev123、r3/rev123</p>
      <form method="post" action="/login">
        <label>用户名</label><input name="username" autofocus required>
        <div style="height:12px"></div>
        <label>口令</label><input name="password" type="password" required>
        <div style="height:18px"></div>
        <button class="btn" type="submit">登录</button>
      </form></div>`,
  });
}

function statusTag(s) {
  return `<span class="tag ${esc(s)}">${STATUS_TEXT[s] || esc(s)}</span>`;
}

export function homeView({ user, samples, suppliers, flash }) {
  const canReceive = user.role === 'admin' || user.role === 'buyer';
  const form = canReceive ? `
  <div class="card">
    <h2>收样登记</h2>
    <p class="sub">登记品类、产地、到货温度、批次与封存编号；封签破损/到货超温/临期样品自动标记并留痕。</p>
    <form method="post" action="/samples/receive">
      <div class="grid3">
        <div><label>样品编号 *</label><input name="code" required placeholder="SP-2026-007"></div>
        <div><label>品类 *</label><input name="category" required placeholder="如：番茄"></div>
        <div><label>产地 *</label><input name="origin" required placeholder="如：山东寿光"></div>
        <div><label>到货温度 ℃ *</label><input name="arrival_temp" type="number" step="0.1" required></div>
        <div><label>运输温度上限 ℃ *</label><input name="temp_limit" type="number" step="0.1" value="7" required></div>
        <div><label>批次 *</label><input name="batch_no" required></div>
        <div><label>封存编号 *</label><input name="seal_no" required placeholder="FC-260007"></div>
        <div><label>供应商 *（对评审人保密）</label>
          <select name="supplier_id" required>
            <option value="">请选择</option>
            ${suppliers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}
          </select></div>
        <div><label>保质期至 *</label><input name="expires_at" type="datetime-local" required></div>
        <div><label>库位（可稍后在封样库位分配）</label><input name="storage_location"></div>
        <div><label>封签状态</label>
          <select name="seal_broken"><option value="0">封签完好</option><option value="1">封签已破损</option></select></div>
      </div>
      <div style="height:16px"><button class="btn" type="submit">登记收样</button></div>
    </form>
  </div>` : `<div class="card"><h2>样品台账</h2><p class="sub">评审人视图：揭盲前仅显示样品编号，不显示供应商身份。</p></div>`;

  const rows = samples.map((s) => {
    const hideSupplier = user.role === 'reviewer' && s.status !== 'revealed';
    return `<tr>
      <td class="mono">${esc(s.code)}</td>
      <td>${esc(s.category)}<br><span class="muted">${esc(s.origin)}</span></td>
      <td>${s.arrival_temp}℃<br><span class="muted">上限 ${s.temp_limit}℃</span></td>
      <td class="mono">${esc(s.batch_no)}<br><span class="muted">封存 ${esc(s.seal_no)}</span></td>
      <td>${hideSupplier ? '<span class="muted">🔒 揭盲后公开</span>' : esc(s.supplier_name)}</td>
      <td>${statusTag(s.status)}</td>
      <td class="row-actions">
        <a class="btn-mini" style="border-color:var(--line);color:var(--green-deep)" href="/review/${s.id}">盲评</a>
        <a class="btn-mini" style="border-color:var(--line);color:var(--green-deep)" href="/result/${s.id}">详情</a>
      </td></tr>`;
  }).join('');

  return shell({
    title: '收样登记', user, active: 'home', flash,
    body: `<h1>收样登记 / 样品台账</h1><p class="sub">收样即封存编号入册；一个封存编号只能关联一份当前样品。</p>
    ${form}
    <div class="card"><h2>样品台账（${samples.length}）</h2>
      <table><thead><tr><th>样品编号</th><th>品类/产地</th><th>到货温度</th><th>批次/封存</th><th>供应商</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="muted">暂无样品</td></tr>'}</tbody></table>
    </div>`,
  });
}

export function storageView({ user, samples, flash }) {
  const canEdit = user.role === 'admin' || user.role === 'buyer';
  const cards = samples.map((s) => `
    <div class="card">
      <div class="two-col">
        <div>
          <h2 class="mono">${esc(s.code)} <span class="muted">· ${esc(s.category)}（${esc(s.origin)}）</span></h2>
          <p>${statusTag(s.status)}
             <span class="muted">封存编号 ${esc(s.seal_no)} ｜ 批次 ${esc(s.batch_no)}</span></p>
          <p class="muted">到货 ${s.arrival_temp}℃（上限 ${s.temp_limit}℃）｜
             保质期至 ${new Date(s.expires_at).toLocaleString('zh-CN')}</p>
          ${canEdit ? `
          <form method="post" action="/samples/${s.id}/storage" class="inline">
            <label>封样库位</label>
            <div style="display:flex;gap:8px">
              <input name="location" value="${esc(s.storage_location || '')}" placeholder="如：冷库 A-01">
              <button class="btn-ghost btn" type="submit">保存库位</button>
            </div>
          </form>` : `<p>库位：<b>${esc(s.storage_location || '未分配')}</b></p>`}
          ${canEdit && s.status !== 'revealed' && s.status !== 'withdrawn' && s.status !== 'broken' ? `
          <div style="height:10px"></div>
          <div class="row-actions">
            <form method="post" action="/samples/${s.id}/seal-broken" class="inline">
              <button class="btn-ghost btn" type="submit">上报封签破损</button>
            </form>
            <form method="post" action="/samples/${s.id}/withdraw" class="inline">
              <input type="hidden" name="reason" value="供应商撤样">
              <button class="btn-ghost btn" type="submit">供应商撤样</button>
            </form>
          </div>` : ''}
        </div>
        <div>
          <label>样品照片（本地存储）</label>
          ${s.photo_path ? `<img class="thumb" src="/${esc(s.photo_path)}" alt="${esc(s.code)}" style="width:100%;height:auto">` : '<p class="muted">暂无照片</p>'}
          ${canEdit ? `
          <form method="post" action="/samples/${s.id}/photo" enctype="multipart/form-data" style="margin-top:8px">
            <input type="file" name="photo" accept="image/*" required>
            <div style="height:8px"></div>
            <button class="btn-ghost btn" type="submit">上传照片</button>
          </form>` : ''}
        </div>
      </div>
    </div>`).join('');
  return shell({
    title: '封样库位', user, active: 'storage', flash,
    body: `<h1>封样库位</h1><p class="sub">记录每份封存样品的库位与照片；封签破损、供应商撤样在此处置并全程留痕。</p>${cards}`,
  });
}

function recusalBadge(recused) {
  return recused ? `<p class="blind-note" style="border-left-color:#7a4636">您已获准回避该样品，无需评分；如回避依据已消除，可申请撤回后再评。</p>` : '';
}

export function reviewListView({ user, samples, recusedIds, flash }) {
  const items = samples.map((s) => {
    const mine = s.my_score;
    return `<tr>
      <td class="mono">${esc(s.code)}</td><td>${esc(s.category)}（${esc(s.origin)}）</td>
      <td>${esc(s.seal_no)}</td><td>${statusTag(s.status)}</td>
      <td>${mine ? `已评：口感 ${mine.taste} ｜ 规格 ${mine.spec} ｜ 损耗 ${mine.cooking_loss}%`
                 : recusedIds.has(s.id) ? '<span class="muted">已回避</span>' : '<span class="muted">未评</span>'}</td>
      <td><a href="/review/${s.id}">${s.status === 'sealed' && !recusedIds.has(s.id) && !mine ? '进入盲评' : '查看'}</a></td>
    </tr>`;
  }).join('');
  return shell({
    title: '盲评表', user, active: 'review', flash,
    body: `<h1>盲评表</h1>
    <div class="blind-note">盲评纪律：本页只按样品编号展示，全程不显示供应商；揭盲前查询供应商身份将被拒绝并记录审计。评分须本人填写，他人不得代填。</div>
    <div class="card"><table><thead><tr><th>样品编号</th><th>品类/产地</th><th>封存编号</th><th>状态</th><th>我的评分</th><th></th></tr></thead>
    <tbody>${items || '<tr><td colspan="6" class="muted">暂无可评审样品</td></tr>'}</tbody></table></div>`,
  });
}

function scoreRadios(name, value) {
  let out = '<div class="score-pick">';
  for (let i = 1; i <= 10; i++) {
    out += `<label><input type="radio" name="${name}" value="${i}" ${value === i ? 'checked' : ''}><span>${i}</span></label>`;
  }
  return out + '</div>';
}

export function reviewView({ user, sample, events, recused, myScore, flash }) {
  const sealed = sample.status === 'sealed';
  const canScore = user.role === 'reviewer' && sealed && !recused && !myScore;
  return shell({
    title: `盲评 ${sample.code}`, user, active: 'review', flash,
    body: `<h1>盲评表 · <span class="mono">${esc(sample.code)}</span></h1>
    <p class="sub">${esc(sample.category)} ｜ 产地 ${esc(sample.origin)} ｜ 封存编号 ${esc(sample.seal_no)} ｜ ${STATUS_TEXT[sample.status]}</p>
    <div class="blind-note">🔒 供应商身份已密封。
      <form method="post" action="/samples/${sample.id}/identity-probe" class="inline">
        <button class="btn-mini" style="border-color:#9aa39c;color:#5d655e" type="submit">尝试查询供应商身份（揭盲前禁止，点击会留痕）</button>
      </form>
    </div>
    ${recusalBadge(recused)}
    ${!sealed ? `<div class="flash err">该样品状态为「${STATUS_TEXT[sample.status]}」，不接受评分（全过程已留痕）。</div>` : ''}
    ${myScore ? `<div class="flash ok">您已完成评分：口感 ${myScore.taste} ｜ 规格 ${myScore.spec} ｜ 烹调损耗 ${myScore.cooking_loss}%${myScore.remark ? ` ｜ 备注：${esc(myScore.remark)}` : ''}</div>` : ''}
    ${user.role !== 'reviewer' ? '<div class="blind-note">采购员/管理员仅可查看本页，不得替评审人填分。</div>' : ''}

    <div class="two-col">
      <div class="card">
        ${canScore ? `<form method="post" action="/review/${sample.id}/scores">
          <fieldset>
            <legend>感官评分（1–10，整数）</legend>
            <label>口感</label>${scoreRadios('taste')}
            <div style="height:10px"></div>
            <label>规格</label>${scoreRadios('spec')}
          </fieldset>
          <fieldset>
            <legend>实测数据</legend>
            <label>烹调损耗（%，0–100 实测值）</label>
            <input name="cooking_loss" type="number" step="0.1" min="0" max="99.9" required placeholder="如：12.5">
            <div style="height:10px"></div>
            <label>评审备注</label><textarea name="remark" rows="2"></textarea>
          </fieldset>
          <button class="btn" type="submit">提交评分</button>
          <span class="muted" style="margin-left:10px">任一项漏填都会留痕并退回补填。</span>
        </form>` : myScore ? '' : '<p class="muted">当前不可填写评分。</p>'}

        ${user.role === 'reviewer' && !recused && sealed && !myScore ? `
        <div style="height:18px"></div>
        <details><summary class="muted">与该样品存在利害关系？申请回避</summary>
          <form method="post" action="/samples/${sample.id}/recusal" style="margin-top:10px">
            <label>回避理由</label><textarea name="reason" rows="2" required placeholder="说明与供应商或样品的利害关系"></textarea>
            <div style="height:10px"></div><button class="btn-ghost btn" type="submit">提交回避申请</button>
          </form>
        </details>` : ''}
      </div>
      <div class="card">
        <h2>留痕时间线</h2>
        <ul class="timeline">${events.map((e) => `<li><b>${esc(e.type)}</b> <span class="muted">${new Date(e.created_at).toLocaleString('zh-CN')}</span><br>${esc(e.detail || '')} <span class="muted">— ${esc(e.actor || 'system')}</span></li>`).join('')}</ul>
      </div>
    </div>
    <p style="margin-top:6px"><a href="/review">← 返回盲评列表</a></p>`,
  });
}

export function recusalAdminView({ user, recusals, flash }) {
  const rows = recusals.map((r) => `<tr>
    <td class="mono">${esc(r.code)}</td><td>${esc(r.reviewer_name)}</td>
    <td>${esc(r.reason)}</td><td><span class="tag">${esc(r.status === 'requested' ? '待审批' : r.status === 'approved' ? '已批准' : '已撤回/驳回')}</span></td>
    <td class="muted">${new Date(r.created_at).toLocaleString('zh-CN')}</td>
    <td>${r.status === 'requested' ? `<div class="row-actions">
      <form method="post" action="/admin/recusals/${r.id}/decide" class="inline">
        <input type="hidden" name="approve" value="1"><button class="btn" type="submit">批准回避</button></form>
      <form method="post" action="/admin/recusals/${r.id}/decide" class="inline">
        <input type="hidden" name="approve" value="0"><button class="btn-ghost btn" type="submit">驳回</button></form>
    </div>` : ''}</td></tr>`).join('');
  return shell({
    title: '回避审批', user, active: 'recusal', flash,
    body: `<h1>评审人回避申请</h1><p class="sub">批准后该评审人对此样品的评分入口即关闭，操作全程留痕。</p>
    <div class="card"><table><thead><tr><th>样品编号</th><th>评审人</th><th>理由</th><th>状态</th><th>时间</th><th>审批</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="muted">暂无回避申请</td></tr>'}</tbody></table></div>`,
  });
}

export function revealListView({ user, samples, flash }) {
  const rows = samples.map((s) => `<tr>
    <td class="mono">${esc(s.code)}</td><td>${esc(s.category)}（${esc(s.origin)}）</td>
    <td>${esc(s.seal_no)}</td><td>${statusTag(s.status)}</td>
    <td>${s.score_count} 人已评</td>
    <td>${s.status === 'sealed'
      ? (user.role === 'admin' || user.role === 'buyer'
          ? `<a href="/reveal/${s.id}">两人揭盲</a>`
          : '<span class="muted">待管理员揭盲</span>')
      : s.status === 'revealed' ? `<a href="/result/${s.id}">查看揭盲结果</a>` : '<span class="muted">不可揭盲</span>'}</td>
  </tr>`).join('');
  return shell({
    title: '揭盲', user, active: 'reveal', flash,
    body: `<h1>揭盲</h1>
    <div class="blind-note">揭盲须两名授权人员同时在场：当前登录人为第一揭盲人，需由第二名授权人员现场输入本人口令共同确认。两人同时触发只产生一次揭盲结果。</div>
    <div class="card"><table><thead><tr><th>样品编号</th><th>品类/产地</th><th>封存编号</th><th>状态</th><th>评分</th><th></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="muted">暂无样品</td></tr>'}</tbody></table></div>`,
  });
}

export function revealView({ user, sample, scoreCount, flash }) {
  return shell({
    title: `揭盲 ${sample.code}`, user, active: 'reveal', flash,
    body: `<h1>两人揭盲 · <span class="mono">${esc(sample.code)}</span></h1>
    <p class="sub">${esc(sample.category)} ｜ 封存编号 ${esc(sample.seal_no)} ｜ 已有 ${scoreCount} 名评审人评分</p>
    <div class="card">
      <p>第一揭盲人（当前登录）：<b>${esc(user.display_name)}（${esc(user.username)}）</b></p>
      <form method="post" action="/reveal/${sample.id}">
        <label>第二揭盲人用户名（管理员或采购员）</label>
        <input name="second_user" required placeholder="如：buyer">
        <div style="height:10px"></div>
        <label>第二揭盲人口令（现场输入）</label>
        <input name="second_pass" type="password" required>
        <div style="height:16px"></div>
        <button class="btn" type="submit">两人同时揭盲，公开供应商与汇总意见</button>
      </form>
    </div><p><a href="/reveal">← 返回</a></p>`,
  });
}

export function revealViewReadonly({ user, sample, scoreCount, flash }) {
  return shell({
    title: `揭盲 ${sample.code}`, user, active: 'reveal', flash,
    body: `<h1>揭盲 · <span class="mono">${esc(sample.code)}</span></h1>
    <p class="sub">${esc(sample.category)} ｜ 封存编号 ${esc(sample.seal_no)} ｜ 已有 ${scoreCount} 名评审人评分</p>
    <div class="card"><p>${statusTag(sample.status)}</p>
      <p class="muted">揭盲须由两名授权人员（管理员 + 采购员）同时在场完成；评审人无权操作。揭盲后可在结果复核页查看供应商与评审意见对照。</p>
    </div><p><a href="/reveal">← 返回</a></p>`,
  });
}

export function resultsListView({ user, samples, flash }) {
  const rows = samples.map((s) => `<tr>
    <td class="mono">${esc(s.code)}</td><td>${esc(s.category)}</td>
    <td>${esc(s.supplier_name || '—')}</td><td>${statusTag(s.status)}</td>
    <td>${s.status === 'revealed' ? `<a href="/result/${s.id}">对照评审意见 / 复核</a>`
      : `<a href="/result/${s.id}">查看台账</a>`}</td>
  </tr>`).join('');
  return shell({
    title: '结果复核', user, active: 'results', flash,
    body: `<h1>结果复核</h1><p class="sub">揭盲后按样品对照各评审人意见与供应商信息，复核人形成选用意见。</p>
    <div class="card"><table><thead><tr><th>样品编号</th><th>品类</th><th>供应商</th><th>状态</th><th></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5" class="muted">暂无样品</td></tr>'}</tbody></table></div>`,
  });
}

export function resultView({ user, data, myReview, events, flash }) {
  const { sample, result, reviews } = data;
  if (sample.status !== 'revealed' || !result) {
    return shell({
      title: `样品 ${sample.code}`, user, active: 'results', flash,
      body: `<h1 class="mono">${esc(sample.code)}</h1>
      <div class="flash err">样品尚未揭盲（当前：${STATUS_TEXT[sample.status]}），供应商与汇总意见暂不公开。</div>
      <div class="card"><h2>留痕时间线</h2><ul class="timeline">
        ${events.map((e) => `<li><b>${esc(e.type)}</b> ${esc(e.detail || '')}</li>`).join('')}
      </ul></div><p><a href="/results">← 返回</a></p>`,
    });
  }
  const opinionRows = result.opinions.map((o) => `<tr>
    <td>${esc(o.reviewer)}</td><td>${o.taste}</td><td>${o.spec}</td>
    <td>${o.cooking_loss}%</td><td>${esc(o.remark || '')}</td></tr>`).join('');
  const reviewRows = reviews.map((r) => `<tr>
    <td>${esc(r.reviewer_name)}</td>
    <td>${r.agrees ? '<span class="tag sealed">同意选用</span>' : '<span class="tag">不同意选用</span>'}</td>
    <td>${esc(r.opinion)}</td><td class="muted">${new Date(r.created_at).toLocaleString('zh-CN')}</td></tr>`).join('');
  const canReview = user.role === 'reviewer' && !myReview;
  return shell({
    title: `揭盲结果 ${sample.code}`, user, active: 'results', flash,
    body: `<h1>揭盲结果 · <span class="mono">${esc(sample.code)}</span></h1>
    <div class="card">
      <h2>选用对照</h2>
      <table>
        <tr><th>样品编号</th><td class="mono">${esc(result.code)}</td></tr>
        <tr><th>供应商（揭盲公开）</th><td><b>${esc(sample.supplier_name)}</b></td></tr>
        <tr><th>品类 / 产地</th><td>${esc(sample.category)} ｜ ${esc(sample.origin)}</td></tr>
        <tr><th>批次 / 封存编号</th><td class="mono">${esc(sample.batch_no)} ｜ ${esc(sample.seal_no)}</td></tr>
        <tr><th>平均口感 / 平均规格</th><td>${result.average_taste ?? '—'} ｜ ${result.average_spec ?? '—'}（1–10）</td></tr>
        <tr><th>平均烹调损耗</th><td>${result.average_cooking_loss ?? '—'}%</td></tr>
        <tr><th>揭盲时间</th><td class="muted">${new Date(result.revealed_at).toLocaleString('zh-CN')}</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>各评审人意见对照</h2>
      <table><thead><tr><th>评审人</th><th>口感</th><th>规格</th><th>实测损耗</th><th>备注</th></tr></thead>
      <tbody>${opinionRows || '<tr><td colspan="5" class="muted">无评分记录</td></tr>'}</tbody></table>
    </div>
    <div class="card">
      <h2>结果复核（选用意见）</h2>
      <table><thead><tr><th>复核人</th><th>结论</th><th>意见</th><th>时间</th></tr></thead>
      <tbody>${reviewRows || '<tr><td colspan="4" class="muted">暂无复核</td></tr>'}</tbody></table>
      ${myReview ? '<p class="muted" style="margin-top:12px">您已完成复核。</p>' : canReview ? `
      <form method="post" action="/result/${sample.id}/review" style="margin-top:14px">
        <label>选用结论</label>
        <select name="agrees"><option value="1">同意选用</option><option value="0">不同意选用</option></select>
        <div style="height:10px"></div>
        <label>复核意见</label><textarea name="opinion" rows="2" required></textarea>
        <div style="height:10px"></div><button class="btn" type="submit">提交选用意见</button>
      </form>` : '<p class="muted" style="margin-top:12px">仅评审人可提交复核意见。</p>'}
    </div>
    <div class="card"><h2>留痕时间线</h2><ul class="timeline">
      ${events.map((e) => `<li><b>${esc(e.type)}</b> <span class="muted">${new Date(e.created_at).toLocaleString('zh-CN')}</span><br>${esc(e.detail || '')}</li>`).join('')}
    </ul></div>
    <p><a href="/results">← 返回</a></p>`,
  });
}

export function auditView({ user, logs, flash }) {
  const rows = logs.map((l) => `<tr>
    <td class="muted">${new Date(l.ts).toLocaleString('zh-CN')}</td>
    <td>${esc(l.actor || '—')}</td><td>${esc(l.action)}</td>
    <td>${esc(l.target_type || '')} ${esc(l.target_id || '')}</td>
    <td>${l.allowed ? '<span class="tag sealed">允许</span>' : '<span class="tag">拒绝</span>'}</td>
    <td>${esc(l.detail || '')}</td></tr>`).join('');
  return shell({
    title: '访问审计', user, active: 'audit', flash,
    body: `<h1>访问审计</h1><p class="sub">所有越权访问（揭盲前查询供应商、非评审人填分、已回避评分等）均记录在案。</p>
    <div class="card"><table><thead><tr><th>时间</th><th>人员</th><th>动作</th><th>对象</th><th>结果</th><th>详情</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="muted">暂无审计记录</td></tr>'}</tbody></table></div>`,
  });
}
