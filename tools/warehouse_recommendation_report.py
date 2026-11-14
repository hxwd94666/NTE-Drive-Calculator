# 将只读仓库试算结果渲染为本地可筛选审计报告，不展示账号或装备原生 UID。
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from src.domain.equipment.scoring.grade_limits import GRADE_MIN_RATIOS


def audit_trial(result: dict) -> dict:
    rows = result["rows"]
    discarded = {r["uid"] for r in rows if r["decision"] == "discard"}
    baseline = {uid for r in result["baseline"].values() if r["valid"] for uid in r["uids"]}
    grade_position = next((r['position'] for r in result.get('rule_order', ()) if r.get('enabled') and r.get('smart_kind') == 'grade'), None)
    checks = {
        "建议覆盖所有装备且无重复": len(rows) == result["total_items"] == len({r["uid"] for r in rows}),
        "明确未来组合未进入弃置": not any(r["future_guards"] and r["decision"] == "discard" for r in rows),
        "增加未来储备没有扩大弃置": not any(
            r["decision"] == "discard" and r["without_future"] != "discard" for r in rows
        ),
        "当前已装备未进入弃置": not any(r["original_equipped"] and r["decision"] == "discard" for r in rows),
        "已有锁定未被自动解除": not any(r["original_locked"] and r["decision"] == "discard" for r in rows),
        "原配装见证保留或复算无损": not discarded.intersection(baseline) or not result['reallocation_regressions'],
        "二次配装全部完成且不降分": not result["reallocation_regressions"]
        and all(r["valid"] and r["has_core"] for r in result["after_removal"].values()),
        "顺序生效的当前用途候选保留": all(not discarded.intersection(r.get('effective_required_uids', r["required_uids"])) for r in result["current_buckets"]),
        "顺序生效的未来候选保留": all(not discarded.intersection(r.get('effective_required_uids', r["required_uids"])) for r in result["future_buckets"]),
        "用户评级保留条件生效": not result.get("drive_keep_grade")
        or not any(
            r["kind"] == "drive"
            and r["decision"] == "discard"
            and not (grade_position is not None and r.get('winning_rule', {}).get('position', grade_position) < grade_position)
            and r["best_current_score"] is not None
            and r["best_current_score"] >= r["area"] * 10 * GRADE_MIN_RATIOS[result["drive_keep_grade"]]
            for r in rows
        ),
        "纯未来储备不触发锁定": all(
            any(s.startswith(("全角色", "当前用途", "命中锁定规则")) for s in r["reasons"]) for r in rows if r["decision"] == "lock"
        ),
    }
    return {
        "checks": checks,
        "all_passed": all(checks.values()),
        "new_locks": dict(Counter(r["kind"] for r in rows if r["decision"] == "lock" and not r["original_locked"])),
        "new_discard_marks": dict(
            Counter(r["kind"] for r in rows if r["decision"] == "discard" and not r["original_discarded"])
        ),
        "already_discarded_but_recommended_keep": dict(
            Counter(r["kind"] for r in rows if r["original_discarded"] and r["decision"] in ("keep", "lock"))
        ),
        "existing_locks_without_current_reserve_need": dict(
            Counter(r["kind"] for r in rows if r["original_locked"] and r["model_decision"] == "discard")
        ),
        "scope": "覆盖已声明的角色、评分和储备规则，不证明任意未来角色或所有权重下最优。未执行游戏操作。",
    }


def render_report(result: dict, output: Path, previous: list[dict] | None = None) -> dict:
    audit = audit_trial(result)
    data = dict(result)
    data["audit"] = audit
    data["iterations"] = [
        {"version": v["algorithm"], "counts": v["counts_by_kind"], "seconds": v["elapsed_seconds"]}
        for v in (previous or [])
    ] + [{"version": result["algorithm"], "counts": result["counts_by_kind"], "seconds": result["elapsed_seconds"]}]
    # 页面编号仅为本次冻结清单索引，原生 UID 留在本机 JSON 供复核，不进入展示页面。
    data["rows"] = [
        {
            **{k: v for k, v in row.items() if k not in {"uid", "replacement_uids"}},
            "index": i + 1,
            "replacement_count": len(row["replacement_uids"]),
        }
        for i, row in enumerate(result["rows"])
    ]
    data.pop("current_buckets", None)
    data.pop("future_buckets", None)
    data.pop("scoring_profiles", None)
    for key in ("baseline", "after_removal"):
        data[key] = {role: {k: v for k, v in plan.items() if k != "uids"} for role, plan in data[key].items()}
    encoded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    template = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>仓库推荐试算</title><style>
body{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;background:#10151d;color:#e5edf6;margin:0;padding:26px;max-width:1550px;margin:auto}h1{font-size:27px;margin:0}h2{font-size:19px;margin-top:26px}small,.muted{color:#a5b5c9}.cards{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}.card{background:#192431;border:1px solid #344457;border-radius:10px;padding:14px 20px;min-width:175px}.num{font-size:28px;font-weight:700;color:#6ac7ff}table{border-collapse:collapse;width:100%;background:#151f2a}th,td{text-align:left;border-bottom:1px solid #304052;padding:9px;vertical-align:top}th{background:#223143;position:sticky;top:0}details{margin:10px 0}select,input,button{background:#1c2b3b;color:#e5edf6;border:1px solid #51657a;padding:8px;border-radius:5px}input{min-width:270px}.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:13px}.lock{color:#ffd45e}.keep{color:#70d3ab}.discard{color:#ffa78c}.review{color:#c0b6ff}.notice{padding:13px;border-left:4px solid #5eb5f0;background:#192a3c}.scroll{max-height:72vh;overflow:auto}.reason{min-width:235px}.stats{min-width:180px}.badge{font-weight:700;white-space:nowrap}.pager{display:flex;gap:14px;align-items:center;margin-top:14px}code{font-family:inherit;color:#b9d9f7}
</style><h1>仓库推荐试算</h1><p class="muted">冻结仓库 · 原始游戏状态未修改 · 配装与储备依据可逐件查看</p>
<div class="notice">这是离线算法试算，尚未接入自动执行。保留“待判断”，不把搜索或用途覆盖不足当作低分。未来组合保留不等于未来角色伤害已经建模。<div id="grade"></div></div>
<div id="cards" class="cards"></div><h2>建议分布</h2><div id="counts"></div>
<p id="actions"></p><div id="grade-comparison"></div><details><summary>查看全角色、二次配装结果和形状储备数量</summary><div id="plans"></div><p id="shapes"></p></details>
<details><summary>查看审查结论与迭代修正</summary><div id="audit"></div><div id="iterations"></div></details>
<h2>逐件清单</h2><div class="controls"><select id="kind"><option value="">全部类型</option><option value="tape">空幕</option><option value="drive">驱动</option></select>
<select id="decision"><option value="">全部建议</option><option value="lock">建议锁定</option><option value="keep">建议保留</option><option value="discard">建议弃置</option><option value="review">待判断</option></select>
<select id="focus"><option value="">全部状态</option><option value="rescued">已弃置但建议留下</option><option value="newlock">新增锁定建议</option><option value="future">明确未来组合</option><option value="oldlock">已有锁但算法认为冗余</option></select>
<input id="query" placeholder="搜索编号、套装、形状、主副词条或角色"><button id="reset">重置筛选</button></div>
<div id="matched" class="muted"></div><div class="scroll"><table><thead><tr><th>编号／类型</th><th>装备</th><th>副词条</th><th>当前用途最高分</th><th>建议／原状态</th><th>依据</th></tr></thead><tbody id="body"></tbody></table></div><div class="pager"><button id="prev">上一页</button><span id="page"></span><button id="next">下一页</button></div>
<script type="application/json" id="data">__DATA__</script><script>
const d=JSON.parse(document.getElementById('data').textContent),$=x=>document.getElementById(x),names={lock:'锁定',keep:'保留',discard:'弃置',review:'待判断'},kinds={tape:'空幕',drive:'驱动'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const table=(head,rows)=>'<table><thead><tr>'+head.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
$('cards').innerHTML=[['装备数量',d.total_items],['仓库质量分',d.warehouse_score==null?'暂无':d.warehouse_score.toFixed(1)+' / 350'],['试算角色',d.evaluated_characters.length+' 人'],['本次离线耗时',d.elapsed_seconds.toFixed(2)+' 秒']].map(([t,v])=>'<div class="card"><div>'+t+'</div><div class="num">'+v+'</div></div>').join('');
$('grade').textContent='本次驱动评级额外保留：'+(d.drive_keep_grade?d.drive_keep_grade+' 及以上':'关闭')+'；按全部适配角色中的最高有效评级。';
$('counts').innerHTML=table(['类型','建议锁定','建议保留','建议弃置','待判断'],Object.entries(d.counts_by_kind).map(([k,v])=>[kinds[k],v.lock||0,v.keep||0,v.discard||0,v.review||0]));
$('actions').textContent='相对现有状态，新增锁定建议：'+JSON.stringify(d.audit.new_locks)+'；新增弃置标记建议：'+JSON.stringify(d.audit.new_discard_marks)+'。类别总数包括已有锁定／弃置。';
if(d.grade_sensitivity)$('grade-comparison').innerHTML='<h2>驱动评级选项对比</h2>'+table(['额外保留评级','驱动锁定','驱动保留','驱动弃置','驱动待判断'],d.grade_sensitivity.map(x=>[x.grade||'关闭',x.counts.lock||0,x.counts.keep||0,x.counts.discard||0,x.counts.review||0]));
$('plans').innerHTML=table(['角色','全仓候选评分','整批移除弃置项后','配齐'],d.evaluated_characters.map(r=>[r.name,d.baseline[r.name].score.toFixed(2),d.after_removal[r.name].score.toFixed(2),d.after_removal[r.name].valid&&d.after_removal[r.name].has_core?'是':'否']));
$('shapes').textContent='现有合法蓝图的单组形状最大重复数：'+Object.entries(d.known_shape_multiplicity).map(([k,v])=>k+' × '+v).join('；')+'。平均需求只用于排序，实际保留完整配装见证。';
$('audit').innerHTML=table(['检查','结果'],Object.entries(d.audit.checks).map(([k,v])=>[k,v?'通过':'未通过']));
$('iterations').innerHTML=table(['版本','空幕弃置候选','驱动弃置候选','耗时'],d.iterations.map(x=>[x.version,x.counts.tape.discard||0,x.counts.drive.discard||0,x.seconds.toFixed(2)+'秒']));
let page=0,filtered=[];function render(){let q=$('query').value.toLowerCase().trim(),focus=$('focus').value;
filtered=d.rows.filter(r=>(!$('kind').value||r.kind===$('kind').value)&&(!$('decision').value||r.decision===$('decision').value)&&(!q||JSON.stringify([r.index,r.set_name,r.shape_id,r.main_stat,r.stats,r.current_scores,r.reasons]).toLowerCase().includes(q))&&(!focus||(focus==='rescued'&&r.original_discarded&&['lock','keep'].includes(r.decision))||(focus==='newlock'&&r.decision==='lock'&&!r.original_locked)||(focus==='future'&&r.future_guards.length)||(focus==='oldlock'&&r.original_locked&&r.model_decision==='discard')));
page=Math.min(page,Math.max(0,Math.ceil(filtered.length/80)-1));$('matched').textContent='符合筛选：'+filtered.length+' 件';
$('body').innerHTML=filtered.slice(page*80,(page+1)*80).map(r=>{let score=r.best_current_score==null?'无已确认用途':r.best_current_score.toFixed(2)+' / '+r.area*10;
let states=[r.original_equipped?'已装备':'',r.original_locked?'已锁定':'',r.original_discarded?'已弃置':''].filter(Boolean).join('、')||'正常';
let use=Object.entries(r.current_scores).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k,v])=>k+' '+v.toFixed(2)).join('；');
return '<tr><td>#'+r.index+'<br>'+kinds[r.kind]+'</td><td>'+esc(r.kind==='tape'?r.set_name:r.shape_id)+'<br>'+esc(r.main_stat)+'<br><small>'+esc(r.quality)+'</small></td><td class="stats">'+Object.entries(r.stats).map(([k,v])=>esc(k)+' '+esc(v)).join('<br>')+'</td><td>'+esc(score)+'<br><small>'+esc(use)+'</small></td><td><span class="badge '+r.decision+'">'+names[r.decision]+'</span><br>'+states+'</td><td class="reason">'+r.reasons.map(esc).join('<br>')+'<br><small>当前用途候选关联：'+r.replacement_count+' 件；按整批见证复核，非独立可删证明。</small></td></tr>'}).join('');
$('page').textContent=(page+1)+' / '+Math.max(1,Math.ceil(filtered.length/80));$('prev').disabled=page===0;$('next').disabled=(page+1)*80>=filtered.length;}
for(let id of ['kind','decision','focus','query'])$(id).addEventListener(id==='query'?'input':'change',()=>{page=0;render()});$('prev').onclick=()=>{page--;render()};$('next').onclick=()=>{page++;render()};$('reset').onclick=()=>{for(let id of ['kind','decision','focus','query'])$(id).value='';page=0;render()};render();
</script></html>"""
    output.write_text(template.replace("__DATA__", encoded), encoding="utf-8")
    return audit
