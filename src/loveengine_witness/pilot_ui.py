"""Static operator UI for the local LAN pilot."""

from __future__ import annotations

OPERATOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoveEngine LAN Pilot</title>
<style>
:root{color-scheme:dark;--ink:#ece9df;--muted:#999b95;--line:#313530;--panel:#171a17;
--panel2:#1d211d;--bg:#0d100e;--green:#9ee493;--amber:#f0c36a;--red:#ff7b72;--blue:#8ecae6}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% 0,#182018 0,transparent 33%),var(--bg);
color:var(--ink);font:15px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
button,input,textarea{font:inherit}.shell{width:min(1240px,calc(100% - 32px));margin:28px auto 56px}
header{display:flex;justify-content:space-between;gap:32px;align-items:flex-end;padding:22px 0;border-bottom:1px solid var(--line)}
.eyebrow{color:var(--green);font-size:12px;letter-spacing:.15em;text-transform:uppercase}.title{margin:5px 0 4px;
font:600 clamp(30px,5vw,52px)/1.02 Georgia,serif}.lede{margin:0;color:var(--muted);max-width:720px}
.status-stack{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.pill{border:1px solid var(--line);border-radius:99px;
padding:7px 10px;color:var(--muted);background:#121512}.pill.ok{border-color:#365c37;color:var(--green)}
.pill.bad{border-color:#6a3835;color:var(--red)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}
.language-switch{display:flex;gap:8px;justify-content:flex-end;align-items:center;margin-bottom:10px;color:var(--muted);font-size:12px}
.language-switch a{color:var(--green);text-decoration:none;border:1px solid var(--line);border-radius:99px;padding:4px 8px}
.metric,.panel{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);box-shadow:0 14px 32px #0004}
.metric{padding:15px}.metric label{display:block;color:var(--muted);font-size:11px;letter-spacing:.1em;text-transform:uppercase}
.metric strong{display:block;margin-top:4px;font:600 24px/1.2 Georgia,serif}.grid{display:grid;grid-template-columns:minmax(300px,.8fr) minmax(420px,1.4fr);gap:12px}
.panel{padding:20px}.panel h2{font:600 20px/1.2 Georgia,serif;margin:0 0 4px}.panel-note{color:var(--muted);margin:0 0 18px}
.field{display:block;margin:13px 0}.field span{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}
input,textarea{width:100%;border:1px solid #3a4039;background:#0d100e;color:var(--ink);padding:11px 12px;outline:none}
input:focus,textarea:focus{border-color:var(--green);box-shadow:0 0 0 3px #9ee49318}textarea{min-height:130px;resize:vertical}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}button{border:1px solid #4e594d;background:#232923;color:var(--ink);
padding:10px 12px;cursor:pointer;text-align:left}button:hover{border-color:var(--green);color:var(--green)}
button.primary{background:var(--green);color:#0b120b;border-color:var(--green);font-weight:700}.danger{color:var(--red)}
.notice{min-height:46px;margin-top:12px;padding:10px 12px;border-left:3px solid var(--blue);background:#111714;color:#cfd7cf;white-space:pre-wrap}
.notice.error{border-color:var(--red);color:#ffc1bd}.session-head{display:flex;justify-content:space-between;gap:12px;align-items:center}
.hash{color:var(--blue);word-break:break-all;font-size:12px}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}
.fact{border-top:1px solid var(--line);padding-top:9px}.fact dt{color:var(--muted);font-size:11px;text-transform:uppercase}.fact dd{margin:4px 0 0}
.feed{display:grid;gap:7px;max-height:330px;overflow:auto}.event{display:grid;grid-template-columns:48px 1fr;gap:10px;padding:10px 0;border-top:1px solid var(--line)}
.event-seq{color:var(--green)}.event-body{white-space:pre-wrap}.empty{color:var(--muted);padding:28px 0;text-align:center}
.gate{margin-top:16px;padding:14px;border:1px solid #6b572e;background:#2c2414}.gate strong{color:var(--amber)}
footer{display:flex;justify-content:space-between;gap:20px;margin-top:14px;color:var(--muted);font-size:12px}
@media(max-width:860px){header{align-items:flex-start;flex-direction:column}.status-stack{justify-content:flex-start}.metrics{grid-template-columns:1fr 1fr}
.grid{grid-template-columns:1fr}.facts{grid-template-columns:1fr 1fr}}@media(max-width:520px){.shell{width:min(100% - 18px,1240px)}
.metrics,.actions,.facts{grid-template-columns:1fr}.panel{padding:16px}}
</style>
</head>
<body>
<div class="shell">
<header>
  <div><div class="eyebrow">LoveEngine / LAN Pilot</div><h1 class="title">Witness operations</h1>
  <p class="lede">Authenticated text broadcasting with visible evidence continuity, outbound Agent observation and an explicit proposal gate.</p></div>
  <div><nav class="language-switch" aria-label="Language"><span>Language</span><a href="?lang=en">English</a><a href="?lang=zh-CN">中文</a></nav>
  <div class="status-stack"><span id="health-pill" class="pill">service · checking</span><span id="ready-pill" class="pill">chain · checking</span></div></div>
</header>
<section class="metrics" aria-label="Pilot metrics">
  <div class="metric"><label>Agent connections</label><strong id="metric-agents">—</strong></div>
  <div class="metric"><label>Relay queue</label><strong id="metric-queue">—</strong></div>
  <div class="metric"><label>Committed events</label><strong id="metric-events">—</strong></div>
  <div class="metric"><label>Stream lag</label><strong id="metric-lag">—</strong></div>
</section>
<main class="grid">
  <section class="panel" data-panel="control">
    <h2>Broadcast control</h2><p class="panel-note">The write token stays in this page's memory and is never persisted.</p>
    <label class="field"><span>Operator token</span><input id="write-token" type="password" autocomplete="off" placeholder="Loaded from the restricted token file"></label>
    <label class="field"><span>Session ID</span><input id="session" value="lan-pilot-live-001" autocomplete="off"></label>
    <label class="field"><span>Live text</span><textarea id="content" placeholder="Publish the next observable statement…"></textarea></label>
    <div class="actions"><button class="primary" id="create-button">Create session</button><button id="publish-button">Publish text</button>
    <button class="danger" id="close-button">Close session</button><button id="refresh-button">Refresh now</button></div>
    <div id="result" class="notice" role="status">Ready. Enter the token to enable authenticated writes.</div>
  </section>
  <section class="panel" data-panel="evidence">
    <div class="session-head"><div><h2>Evidence continuity</h2><p class="panel-note">Read-only state from the canonical metadata store.</p></div><span id="session-pill" class="pill">not loaded</span></div>
    <dl class="facts"><div class="fact"><dt>Sequence</dt><dd id="sequence">—</dd></div><div class="fact"><dt>Source</dt><dd id="source-type">—</dd></div>
    <div class="fact"><dt>Chain block</dt><dd id="chain-block">—</dd></div></dl>
    <div><div class="eyebrow">Head event hash</div><div id="head-hash" class="hash">No session selected.</div></div>
    <h2 style="margin-top:22px">Event feed</h2><div id="event-feed" class="feed"><div class="empty">No committed events.</div></div>
    <div class="gate"><strong>ProposalGate: manual hold</strong><div id="gate-copy">Evidence must be finalized and every critical dispute dismissed before a proposal plan can be produced.</div></div>
  </section>
</main>
<footer><span id="run-id">run · —</span><span id="clock">—</span></footer>
</div>
<script>
const byId=id=>document.getElementById(id);
const sessionInput=byId('session'),contentInput=byId('content'),tokenInput=byId('write-token');
let writeToken='';
tokenInput.addEventListener('input',event=>{writeToken=event.target.value});
function setPill(id,text,state){const node=byId(id);node.textContent=text;node.className='pill '+(state||'')}
function notice(message,error=false){const node=byId('result');node.textContent=message;node.className='notice'+(error?' error':'')}
async function asJson(response){const text=await response.text();try{return JSON.parse(text)}catch{return {raw:text}}}
async function send(path,body){
  if(!writeToken){notice('Write blocked: enter the operator token.',true);return}
  try{
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+writeToken},body:JSON.stringify(body||{})});
    const value=await asJson(response);
    if(!response.ok){notice((value.error&&value.error.code?value.error.code+': ':'')+(value.error?.message||'Request rejected.'),true);return}
    notice('Accepted · correlation '+(response.headers.get('X-Correlation-ID')||'not returned'));
    if(path.endsWith('/events'))contentInput.value='';
    await refresh();
  }catch(error){notice('Service unavailable: '+error.message,true)}
}
function now(){return String(Math.floor(Date.now()/1000))}
byId('create-button').addEventListener('click',()=>send('/v1/live/sessions',{session_id:sessionInput.value,source_type:'operator',created_at:now()}));
byId('publish-button').addEventListener('click',()=>send('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)+'/events',
  {event_id:crypto.randomUUID(),occurred_at:now(),category:'source',source_type:'operator',content:contentInput.value}));
byId('close-button').addEventListener('click',()=>send('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)+'/close',{closed_at:now()}));
byId('refresh-button').addEventListener('click',()=>refresh());
function renderEvents(events){
  const feed=byId('event-feed');feed.replaceChildren();
  if(!events.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No committed events.';feed.append(empty);return}
  events.slice(-8).reverse().forEach(event=>{
    const row=document.createElement('div');row.className='event';
    const seq=document.createElement('div');seq.className='event-seq';seq.textContent='#'+event.sequence;
    const body=document.createElement('div');body.className='event-body';body.textContent=event.content;
    row.append(seq,body);feed.append(row);
  });
}
async function refresh(){
  byId('clock').textContent=new Date().toLocaleTimeString();
  try{
    const [healthResponse,readyResponse,metricsResponse]=await Promise.all([fetch('/healthz'),fetch('/readyz'),fetch('/v1/metrics')]);
    const health=await asJson(healthResponse),ready=await asJson(readyResponse),metrics=await asJson(metricsResponse);
    setPill('health-pill','service · '+(healthResponse.ok?'online':'degraded'),healthResponse.ok?'ok':'bad');
    setPill('ready-pill','chain · '+(ready.ready?'ready':'not ready'),ready.ready?'ok':'bad');
    byId('metric-agents').textContent=metrics.connections?.agents??'—';byId('metric-queue').textContent=metrics.relay?.queue_depth??'—';
    byId('metric-events').textContent=metrics.events?.count??'—';byId('metric-lag').textContent=(metrics.events?.stream_lag_seconds??'—')+'s';
    byId('chain-block').textContent=metrics.last_chain_block??'offline';byId('run-id').textContent='run · '+(metrics.run_id||health.run_id||'—');
    if(!sessionInput.value)return;
    const [sessionResponse,eventsResponse]=await Promise.all([
      fetch('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)),
      fetch('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)+'/events')
    ]);
    if(!sessionResponse.ok){setPill('session-pill','session · not found','bad');renderEvents([]);return}
    const sessionValue=await sessionResponse.json(),eventsValue=await eventsResponse.json();
    setPill('session-pill','session · '+sessionValue.status,sessionValue.status==='closed'?'ok':'');
    byId('sequence').textContent=String(Number(sessionValue.next_sequence)-1);byId('source-type').textContent=sessionValue.source_type;
    byId('head-hash').textContent=sessionValue.head_event_hash;renderEvents(eventsValue.events||[]);
  }catch(error){setPill('health-pill','service · offline','bad');setPill('ready-pill','chain · unknown','bad')}
}
refresh();if(!new URLSearchParams(location.search).has('static'))setInterval(refresh,1500);
</script>
</body></html>"""


OPERATOR_ZH_REPLACEMENTS = {
    '<html lang="en">': '<html lang="zh-CN">',
    "Witness operations": "见证操作台",
    "Authenticated text broadcasting with visible evidence continuity, outbound Agent observation and an explicit proposal gate.": "带鉴权的文字直播操作台：可观察证据连续性、Agent 出站观察和显式提案门禁。",
    "Language": "语言",
    "service · checking": "服务 · 检查中",
    "chain · checking": "链 · 检查中",
    "Agent connections": "Agent 连接",
    "Relay queue": "Relay 队列",
    "Committed events": "已提交事件",
    "Stream lag": "流延迟",
    "Broadcast control": "直播控制",
    "The write token stays in this page's memory and is never persisted.": "写入 token 只保存在当前页面内存，不会持久化。",
    "Operator token": "主持人 token",
    "Loaded from the restricted token file": "来自受限 token 文件",
    "Session ID": "直播 Session ID",
    "Live text": "直播文字",
    "Publish the next observable statement…": "发布下一条可观察文字…",
    "Create session": "创建 session",
    "Publish text": "发布文字",
    "Close session": "关闭 session",
    "Refresh now": "立即刷新",
    "Ready. Enter the token to enable authenticated writes.": "就绪。输入 token 后可进行鉴权写入。",
    "Evidence continuity": "证据连续性",
    "Read-only state from the canonical metadata store.": "来自 canonical 元数据存储的只读状态。",
    "not loaded": "未加载",
    "Sequence": "序号",
    "Source": "来源",
    "Chain block": "链区块",
    "Head event hash": "头事件 hash",
    "No session selected.": "尚未选择 session。",
    "Event feed": "事件流",
    "No committed events.": "暂无已提交事件。",
    "ProposalGate: manual hold": "ProposalGate：手动等待",
    "Evidence must be finalized and every critical dispute dismissed before a proposal plan can be produced.": "只有证据 finalize 且所有关键争议均 dismissed 后，才能生成提案计划。",
}


def localized_operator_html(lang: str | None) -> str:
    if lang != "zh-CN":
        return OPERATOR_HTML
    html = OPERATOR_HTML
    for source, target in OPERATOR_ZH_REPLACEMENTS.items():
        html = html.replace(source, target)
    return html
