(() => {
'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const canvas=$('#office'), ctx=canvas.getContext('2d');
const meta=[
 {id:'pulse',name:'Pulse',role:'CEO · Team orchestrator',color:'#3d7252',skin:'#dba781',hair:'#38392e',home:[16.5,4.5],rest:[16.5,5.5],quote:'Satu tim, satu arah. Aku memastikan setiap sinyal melewati proses yang tepat.',task:'Mengawasi alur kerja tim',short:'Team orchestrator'},
 {id:'scout',name:'Scout',role:'Wallet intelligence',color:'#768659',skin:'#e5b992',hair:'#533c2a',home:[5.5,8.5],rest:[17.5,12.5],quote:'Mengikuti pergerakan wallet. Ada aktivitas baru? Aku yang pertama melihatnya.',task:'Memindai aktivitas wallet',short:'Wallet intelligence'},
 {id:'echo',name:'Echo',role:'KOL & trader registry',color:'#ba825b',skin:'#bf8763',hair:'#352d2b',home:[9.5,8.5],rest:[18.5,10.5],quote:'Menjaga daftar trader dan KOL tetap relevan, satu pembaruan pada satu waktu.',task:'Memperbarui registry trader',short:'KOL & registry'},
 {id:'cipher',name:'Cipher',role:'Signal & confluence',color:'#63899c',skin:'#edd0aa',hair:'#413f45',home:[13.5,8.5],rest:[3.5,4.5],quote:'Satu transaksi belum cukup. Aku mencari bukti dan konfluensi sebelum membuat sinyal.',task:'Menganalisis konfluensi',short:'Signal engine'},
 {id:'sentry',name:'Sentry',role:'Data enrichment',color:'#98829f',skin:'#d1a27f',hair:'#634737',home:[5.5,12.5],rest:[17.5,13.5],quote:'Melengkapi harga dan metadata. Data yang belum tersedia tetap ditandai UNKNOWN.',task:'Memeriksa harga dan metadata',short:'Data quality'},
 {id:'atlas',name:'Atlas',role:'Performance & leaderboard',color:'#b99957',skin:'#e8bd99',hair:'#3d3934',home:[9.5,12.5],rest:[2.5,4.5],quote:'Sinyal sudah dibuat. Aku mengikuti perjalanannya dan menghitung performa yang terukur.',task:'Memperbarui performa sinyal',short:'Performance'},
 {id:'relay',name:'Relay',role:'Alert delivery',color:'#689988',skin:'#b78160',hair:'#313a37',home:[13.5,12.5],rest:[18.5,11.5],quote:'Memproses antrean alert untuk Telegram dan Discord. Pengiriman harus bisa dilacak.',task:'Memproses antrean alert',short:'Alert delivery'}
];
const statuses={busy:'Sibuk',waiting:'Menunggu',resting:'Rehat',error:'Perlu cek',offline:'Offline'};
let selected='pulse', mode='demo', playing=true, speed=1, elapsed=0, zoom=1, pan={x:0,y:0}, events=[], liveTimer=null, livePending=false, liveGeneration=0, lastSnapshot=0, reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
let agents=meta.map((m,i)=>({...m,x:m.home[0],y:m.home[1],path:[],status:i%3===0?'busy':i%3===1?'waiting':'resting',done:0,message:m.task,phase:i*2.4,changed:0,direction:1}));
const deskPositions=[[4,7],[8,7],[12,7],[4,11],[8,11],[12,11],[15,3]];
const blocked=new Set();
const obstruct=(x,y,w,d)=>{for(let a=x;a<x+w;a++)for(let b=y;b<y+d;b++)blocked.add(`${a},${b}`)};
deskPositions.forEach(([x,y])=>obstruct(x,y,3,1));obstruct(2,2,5,1);obstruct(17,13,2,1);obstruct(18,9,1,1);
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
function route(start,end){
 const a=[Math.floor(start[0]),Math.floor(start[1])],b=[Math.floor(end[0]),Math.floor(end[1])],key=p=>p.join(',');
 if(key(a)===key(b))return [[...end]];
 const q=[a], prev=new Map([[key(a),null]]);let at=0;
 while(at<q.length){const p=q[at++];if(key(p)===key(b))break;for(const [dx,dy] of [[1,0],[0,1],[-1,0],[0,-1]]){const n=[p[0]+dx,p[1]+dy],k=key(n);if(n[0]<1||n[0]>18||n[1]<1||n[1]>14||blocked.has(k)||prev.has(k))continue;prev.set(k,p);q.push(n)}}
 if(!prev.has(key(b)))return [];const out=[];let c=b;while(key(c)!==key(a)){out.unshift([c[0]+.5,c[1]+.5]);c=prev.get(key(c))}out.push([...end]);return out;
}
function destination(a){return a.status==='resting'?a.rest:a.home}
function changeState(a,status,message,record=true){
 if(!statuses[status])status='offline';const old=a.status;a.status=status;a.message=message||a.task;
 if(old!==status){a.changed=elapsed;a.path=route([a.x,a.y],destination(a));if(record)addEvent(a.name,status==='busy'?a.task:status==='resting'?'Antrean kosong. Rehat sebentar di lounge.':status==='waiting'?'Siklus selesai. Menunggu tugas berikutnya.':a.message,status)}
}
function addEvent(name,text,status='waiting',timestamp=Date.now(),id=null){
 if(id&&events.some(e=>e.id===id))return;
 events.unshift({name,text,status,timestamp,id});events=events.slice(0,100);renderEvents();
}
function timeText(ts){return new Date(ts).toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}
function renderEvents(){if(!events.length){$('#activity-list').textContent=mode==='live'?'Belum ada aktivitas engine yang teramati.':'Menunggu aktivitas simulasi.';return}const limit=document.body.classList.contains('view-activity')?70:5;$('#activity-list').replaceChildren(...events.slice(0,limit).map(e=>{const row=document.createElement('div');row.className='event';for(const [cls,val] of [['event-time',timeText(e.timestamp)],['event-dot',''],['event-name',e.name],['event-text',e.text],['event-type',mode==='demo'?'DEMO':'ENGINE']]){const s=document.createElement('span');s.className=cls;s.textContent=val;row.append(s)}return row}));}
function portrait(a){const c=document.createElement('canvas');c.width=48;c.height=58;const g=c.getContext('2d');g.translate(24,54);sprite(g,a,0,false,true);return c.toDataURL()}
for(const a of agents)a.portrait=portrait(a);
function select(id){selected=id;renderTeam();renderDetail();if(document.body.classList.contains('view-team'))setView('office')}
let teamSignature='',detailSignature='';
function renderTeam(){
 const sig=selected+agents.map(a=>a.status).join('|');if(sig===teamSignature)return;teamSignature=sig;const focused=document.activeElement?.dataset?.agent;
 const list=$('#team-list');list.replaceChildren(...agents.map(a=>{const b=document.createElement('button');b.dataset.agent=a.id;b.className='agent-row'+(a.id===selected?' selected':'');b.setAttribute('aria-pressed',String(a.id===selected));b.setAttribute('aria-label',`${a.name}, ${a.role}, ${statuses[a.status]}`);b.innerHTML=`<img class="portrait" alt="" src="${a.portrait}"><span class="agent-copy"><span class="agent-name">${a.name}${a.id==='pulse'?' <span class="ceo-tag">CEO</span>':''}</span><span class="agent-role">${a.short}</span></span><span class="agent-state ${a.status}">${statuses[a.status]}</span>`;b.onclick=()=>select(a.id);return b}));
 for(const state of ['busy','waiting','resting'])$('#'+({busy:'busy-count',waiting:'wait-count',resting:'rest-count'}[state])).textContent=agents.filter(a=>a.status===state).length;
 $('#team-grid').replaceChildren(...agents.map(a=>{const b=document.createElement('button');b.className='team-card';b.innerHTML=`<img alt="" src="${a.portrait}"><h3>${a.name}</h3><span class="agent-role">${a.role}</span><p>${a.quote}</p><span class="agent-state ${a.status}">${statuses[a.status]}</span>`;b.onclick=()=>select(a.id);return b}));
 if(focused)list.querySelector('[data-agent="'+focused+'"]')?.focus({preventScroll:true});
}
function renderDetail(){
 const a=agents.find(x=>x.id===selected), el=$('#agent-detail');const sig=JSON.stringify([a.id,a.status,a.done,a.message,mode]);if(sig===detailSignature)return;detailSignature=sig;el.innerHTML=`<div class="detail-top"><img class="detail-avatar" alt="Karakter ${a.name}" src="${a.portrait}"><div><div class="detail-name">${a.name}${a.id==='pulse'?' <span class="ceo-tag">CEO</span>':''}</div><div class="detail-role">${a.short}</div></div></div><p class="detail-quote">“${a.quote}”</p><div class="detail-stats"><span>Status <b>${statuses[a.status]}</b></span><span>${mode==='demo'?'Siklus demo':'Siklus teramati'} <b>${a.done}</b></span></div><p class="detail-quote detail-message"></p>`;el.querySelector('.detail-message').textContent=a.message;
}
function setView(v){document.body.classList.remove('view-team','view-activity');if(v!=='office')document.body.classList.add('view-'+v);$('#team-view').hidden=v!=='team';$$('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===v));renderEvents();resize()}
$$('.nav').forEach(b=>b.onclick=()=>setView(b.dataset.view));
$('#play').onclick=()=>{if(mode!=='demo')return;playing=!playing;$('#play').innerHTML=playing?'Ⅱ <span>Jeda</span>':'▶ <span>Lanjut</span>';$('#play').setAttribute('aria-label',playing?'Jeda simulasi':'Lanjutkan simulasi')};
$('#speed').onclick=()=>{speed=speed===1?2:speed===2?4:1;$('#speed').textContent=speed+'×'};
$('#zoom-in').onclick=()=>zoom=clamp(zoom+.15,.75,1.9);$('#zoom-out').onclick=()=>zoom=clamp(zoom-.15,.75,1.9);$('#fit').onclick=()=>{zoom=1;pan={x:0,y:0}};
$('#connect').onclick=()=>$('#connect-dialog').showModal();
$('#connect-dialog').addEventListener('click',e=>{if(e.target===$('#connect-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close()}});
function setModeUI(){const live=mode==='live';$('#mode-label').classList.toggle('live',live);$('#mode-label').innerHTML=`<i></i> ${live?'LIVE':'DEMO'}`;$('#play').disabled=live;$('#speed').disabled=live;$('#event-source').textContent=live?'ENGINE FEED':'DEMO FEED';$('#feed-note').textContent=live?'Aktivitas dari engine lokal':'Aktivitas simulasi, bukan transaksi nyata';$('#footer-mode').textContent=live?'VIRTUAL OFFICE / LIVE':'VIRTUAL OFFICE / DEMO';$('#connection-status').textContent=live?'Terhubung ke engine lokal':'Simulasi aktivitas · belum terhubung ke engine';}
function startDemo(){liveGeneration++;clearInterval(liveTimer);liveTimer=null;mode='demo';events=[];playing=true;elapsed=0;$('#play').innerHTML='Ⅱ <span>Jeda</span>';$('#play').setAttribute('aria-label','Jeda simulasi');for(const a of agents){a.done=0;a.phase=agents.indexOf(a)*2.4}setModeUI();addEvent('Pulse','Kantor dibuka. Tim siap menjalankan simulasi.','busy');renderTeam();renderDetail();$('#connection-result').textContent='Mode demo aktif.';$('#connect-dialog').close();renderRuntime(null);}
$('#demo-mode').onclick=startDemo;
async function getState(){const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(3500)});if(!r.ok)throw new Error('API Office tidak tersedia');const s=await r.json();if(s.schema!=='r4ise.office.v1'||!Array.isArray(s.agents))throw new Error('Format telemetri tidak cocok');return s}
function applySnapshot(s){lastSnapshot=Date.now();const fresh=typeof s.updated_at==='number'&&Date.now()/1000-s.updated_at<8;const running=s.running&&fresh;renderRuntime(s,fresh);for(const a of agents){const v=s.agents.find(v=>v.id===a.id);changeState(a,!running?'offline':v?.state||'offline',!running?'Engine belum berjalan atau heartbeat terputus.':v?.message||'Menunggu telemetri.',false);a.done=Number.isFinite(v?.completed)?v.completed:0}for(const e of [...(s.events||[])].reverse()){const a=agents.find(a=>a.id===e.agent);if(a)addEvent(a.name,String(e.message).slice(0,180),e.state,e.at*1000,`${s.session}:${e.id}`)}renderEvents();$('#connection-status').textContent=running?'Engine terhubung · diperbarui setiap 1 detik':'Office terhubung · engine tidak aktif';renderTeam();renderDetail()}
async function pollLive(){if(mode!=='live'||livePending)return;livePending=true;const gen=liveGeneration;try{const s=await getState();if(gen===liveGeneration&&mode==='live'){if(s.mode==='demo')startDemo();else applySnapshot(s)}}catch{if(gen===liveGeneration&&mode==='live'){for(const a of agents)changeState(a,'offline','Koneksi terputus. Menunggu engine.',false);$('#connection-status').textContent='Koneksi terputus · mencoba kembali';renderRuntime(null);renderTeam();renderDetail()}}finally{livePending=false}}
$('#live-mode').onclick=async()=>{const output=$('#connection-result');if(!['localhost','127.0.0.1','[::1]'].includes(location.hostname)){output.textContent='Buka dashboard dari http://127.0.0.1:8765 setelah menjalankan python run.py. Halaman online ini tetap dalam mode demo.';$('#connect-dialog details').open=true;return}output.textContent='Mencari engine lokal…';try{const s=await getState();mode='live';liveGeneration++;events=[];clearInterval(liveTimer);setModeUI();applySnapshot(s);liveTimer=setInterval(pollLive,1000);output.textContent='Office terhubung.';$('#connect-dialog').close()}catch{output.textContent='Office belum tersedia. Jalankan perintah di bawah, lalu coba lagi.';$('#connect-dialog details').open=true}};
let runtimeSignature='';
function renderRuntime(s,fresh=true){
 const sig=JSON.stringify([s?.chains,s?.metrics,s?.services,s?.running,fresh,mode]);if(sig===runtimeSignature)return;runtimeSignature=sig;
 const grid=$('#chain-list'),metrics=$('#chain-metrics');grid.replaceChildren();metrics.replaceChildren();const services=$('#service-list');services.replaceChildren();
 if(!s||mode==='demo'){$('#chain-source').textContent=mode==='demo'?'DEMO':'OFFLINE';const p=document.createElement('p');p.textContent=mode==='demo'?'Mode demo: status chain tidak disimulasikan.':'Menunggu telemetri engine. Data sebelumnya tidak dianggap live.';grid.append(p);return}
 $('#chain-source').textContent=s.running&&fresh?'ENGINE LOKAL':'ENGINE TIDAK AKTIF';
 for(const [key,title] of [['events_received','Payload diterima'],['events_parsed','Trade terurai'],['signals_created','Sinyal dibuat'],['outbox_sent','Pengiriman selesai'],['ingest_queue','Antrean ingest'],['discovery_observations','Observasi discovery EVM'],['discovery_added','Kandidat Solana ditambahkan']]){const el=document.createElement('span'),b=document.createElement('b');el.textContent=title;b.textContent=fresh&&Number.isFinite(s.metrics?.[key])?s.metrics[key].toLocaleString('id-ID'):'—';el.append(b);metrics.append(el)}
 const labels={ready:'Siap meminta data',starting:'Menunggu registry',missing_key:'API key belum diisi',disabled:'Dinonaktifkan',invalid_key:'API key ditolak',credits_exhausted:'Kredit habis',forbidden:'Akses ditolak',rate_limited:'Batas permintaan',request_error:'Permintaan gagal',invalid_response:'Respons tidak valid',ok:'Impor berhasil',empty:'Tidak ada target valid',unknown:'Status belum diketahui'};
 const f=s.services?.fomo,d=s.services?.discovery,live=!!(s.running&&fresh);
 function serviceCard(title,state,detail,error=false){const el=document.createElement('div');el.className='chain-item'+(live&&error?' error':'');const h=document.createElement('strong'),v=document.createElement('span'),p=document.createElement('small');h.textContent=title;v.textContent=live?state:'Engine tidak aktif';p.textContent=live?detail:'Menunggu telemetri aktif.';el.append(h,v,p);services.append(el)}
 if(f)serviceCard('FOMO · Echo',labels[f.status]||labels.unknown,`${f.wallet_targets} target wallet per chain · ${f.registry_ready?'Registry diperbarui':'Registry belum siap'}`,['invalid_key','credits_exhausted','forbidden','rate_limited','request_error','invalid_response'].includes(f.status));
 if(d)serviceCard('Discovery · Scout / Cipher',d.enabled?'Discovery aktif':'Dinonaktifkan',`EVM ${d.evm_enabled?'aktif':'nonaktif'} · Promosi otomatis ${d.auto_promote?'diizinkan':'nonaktif'}`);
 for(const c of s.chains||[]){const active=!!(c.poller_active&&s.running&&fresh),err=['error','timeout','rate_limited','reorg_blocked','chain_mismatch'].includes(c.rpc_state);const el=document.createElement('div');el.className='chain-item'+(active?' active':'')+(active&&err?' error':'');const name=document.createElement('strong');name.textContent=c.id==='hyperevm'?'HyperEVM':c.id;const state=document.createElement('span');state.textContent=!c.poller_supported?'Belum ada poller':!c.enabled?'Dinonaktifkan':!c.rpc_configured?'RPC belum diisi':!active?'Poller tidak aktif':err?'Perlu cek: '+c.rpc_state:c.rpc_state==='standby'?'Menunggu wallet':'Poller berjalan';const info=document.createElement('small');info.textContent=(c.rpc_configured?'Endpoint siap':'Endpoint belum diisi')+' · '+(Number.isFinite(c.watched_wallets)?c.watched_wallets:0)+' target wallet · '+(active&&c.health==='HEALTHY'?'RPC merespons':'Health: '+c.health);el.append(name,state,info);grid.append(el)}
}
// Isometric scene: furniture and characters share world coordinates and depth sorting.
let width=1,height=1,viewScale=1,offsetX=0,offsetY=0;
const P=(x,y,z=0)=>[474+(x-y)*24,132+(x+y)*12-z*24];
function poly(points,fill,stroke=null){ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=.7;ctx.stroke()}}
function line(points,color,w=1){ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.strokeStyle=color;ctx.lineWidth=w;ctx.stroke()}
function plane(x,y,w,d,z,c,stroke){poly([P(x,y,z),P(x+w,y,z),P(x+w,y+d,z),P(x,y+d,z)],c,stroke)}
function box(x,y,w,d,h,top='#e3ccb0',left='#aa8d6d',right='#bf9e7b',base=0){poly([P(x,y+d,base),P(x+w,y+d,base),P(x+w,y+d,base+h),P(x,y+d,base+h)],left);poly([P(x+w,y,base),P(x+w,y+d,base),P(x+w,y+d,base+h),P(x+w,y,base+h)],right);plane(x,y,w,d,base+h,top)}
function label(text,x,y,color='#6d7e64',size=9,align='center'){ctx.font=`500 ${size}px 'DM Sans',sans-serif`;ctx.textAlign=align;ctx.fillStyle=color;ctx.fillText(text,x,y)}
function worldText(text,x,y,z,size=13,color='#55704d'){const p=P(x,y,z);ctx.save();ctx.translate(...p);ctx.transform(1,.5,0,1,0,0);ctx.font=`700 ${size}px 'Manrope',sans-serif`;ctx.fillStyle=color;ctx.textAlign='left';ctx.fillText(text,0,0);ctx.restore()}
function plant(x,y,size=1){const p=P(x,y);box(x-.22,y-.22,.45,.45,.55,'#dddcc9','#aaa999','#bbb9a5');ctx.save();ctx.translate(p[0],p[1]-14);ctx.scale(size,size);line([[0,0],[1,-29]],'#557343',2);for(let j=0;j<6;j++){const dir=j%2?-1:1;ctx.fillStyle=['#779c66','#94af73','#668d5c'][j%3];ctx.beginPath();ctx.ellipse(dir*(6+j%2*2),-8-j*4,10,4,dir*.65,0,Math.PI*2);ctx.fill()}ctx.restore()}
function screen(x,y,w=1.05,ceo=false){const z=1.8;box(x+.4,y+.2,.15,.18,.27,'#536458','#405248','#5f7064',1.45);box(x,y,w,.13,.73,'#344c43','#354e44','#243b33',z);const p1=P(x+.07,y+.14,z+.66),p2=P(x+w-.07,y+.14,z+.66),p3=P(x+w-.07,y+.14,z+.12),p4=P(x+.07,y+.14,z+.12);poly([p1,p2,p3,p4],ceo?'#244c41':'#294839');for(let j=0;j<4;j++){const a=P(x+.13,y+.15,z+.53-j*.1),b=P(x+.13+(j%2?.55:.35),y+.15,z+.53-j*.1);line([a,b],j===0?'#a9ca89':'#638d74',1.2)}}
function desk(x,y,ceo=false){for(const [a,b]of [[.12,.14],[2.52,.14],[.12,.85],[2.52,.85]])box(x+a,y+b,.1,.1,1.28,'#6f7665','#758170','#606f62');box(x,y,2.8,1.15,.16,'#cdb48d','#b19b79','#bfa883',1.25);box(x+2.1,y+.18,.6,.75,.85,'#d6cbb3','#bbb49e','#c4bda8');for(let j=0;j<3;j++)line([P(x+2.13,y+.94,.3+j*.23),P(x+2.65,y+.94,.3+j*.23)],'#929682',1);screen(x+.3,y+.1,.95,ceo);screen(x+1.35,y+.1,.95,ceo);plane(x+.65,y+.7,1,.28,1.44,'#7b8876');for(let j=0;j<4;j++)line([P(x+.7+j*.19,y+.7,1.445),P(x+.7+j*.19,y+.94,1.445)],'#b5bda9',.6);box(x+2.5,y+.65,.18,.18,.21,'#edf0d8','#f6f3e1','#dcdac8',1.42);box(x+.05,y+.7,.32,.32,.03,'#f4edd9','#ddd4bc','#eee4cf',1.43);chair(x+1.5,y+1.65)}
function chair(x,y,c='#71806f'){box(x-.28,y-.1,.6,.45,.09,'#485448','#455145','#5a6755',.2);box(x-.12,y-.02,.16,.16,.48,'#657360','#5e6d58','#52634f',.25);box(x-.38,y-.37,.78,.7,.16,c,'#617259','#53684e',.67);box(x-.4,y+.21,.8,.14,.75,c,'#637658','#738467',.75)}
function sofa(x,y,w=2.5){box(x,y,w,1.05,.55,'#b4c2a0','#869878','#9dae89');box(x,y+.85,w,.22,.9,'#b4c5a1','#91a580','#a1b391',.5);box(x,y,.25,1.05,.62,'#c0cbac','#97a585','#a7b695',.5);box(x+w-.25,y,.25,1.05,.62,'#c0cbac','#97a585','#a7b695',.5);for(let j=0;j<2;j++)box(x+.3+j*(w-.6)/2,y+.15,(w-.65)/2,.65,.17,'#c0cbac','#b0bd9d','#b4c5a4',.55)}
function shelves(x,y){box(x,y,2.6,.6,2.5,'#dac6a2','#bea882','#c7b08d');for(let level=0;level<3;level++){box(x+.12,y+.62,2.36,.05,.5,'#8c8268','#7d8266','#8c8268',.3+level*.68);for(let j=0;j<9;j++)box(x+.23+j*.22,y+.65,.12,.1,.35+(j%3)*.045,['#a9b495','#d5c9a4','#79957e','#bf9b79'][j%4],'#99a387','#9fa688',.31+level*.68)}}
function glassX(x,y,d){poly([P(x,y),P(x,y+d),P(x,y+d,3),P(x,y,3)],'#c7dfd827');for(let i=0;i<=d;i+=2)line([P(x,y+i),P(x,y+i,3)],'#abc3b2',1);line([P(x,y,3),P(x,y+d,3)],'#94b09c',2);line([P(x,y,.2),P(x,y+d,.2)],'#a8bfa9',2)}
function glassY(x,y,w){poly([P(x,y),P(x+w,y),P(x+w,y,3),P(x,y,3)],'#c7dfd827');for(let i=0;i<=w;i+=2)line([P(x+i,y),P(x+i,y,3)],'#b1c3af',1);line([P(x,y,3),P(x+w,y,3)],'#94ad97',2)}
function drawBase(){
 plane(-.5,-.5,21,17,-.32,'#dbe2ce');box(0,0,20,16,.28,'#d4bd99','#b6a689','#c3b295',-.28);
 for(let x=0;x<20;x++)for(let y=0;y<16;y++){const colors=['#e4d4b6','#e8d8ba','#e1ceac','#dfccac','#e9dabb'];plane(x,y,1,1,0,colors[(x*7+y*13)%5],'#c9b79555');const z=(x*3+y)%4;line([P(x+.1,y+.23+z*.12,.01),P(x+.85,y+.23+z*.12,.01)],'#c8b69550',.5)}
 poly([P(0,0),P(20,0),P(20,0,3.8),P(0,0,3.8)],'#dce2cc');poly([P(0,0),P(0,16),P(0,16,3.8),P(0,0,3.8)],'#d3ddca');
 for(let y=1;y<15;y+=3){poly([P(.02,y,.65),P(.02,y+2.6,.65),P(.02,y+2.6,3.35),P(.02,y,3.35)],'#a9c8c4');poly([P(.03,y,2),P(.03,y+2.6,2),P(.03,y+2.6,3.3),P(.03,y,3.3)],'#c1d9cc');line([P(.05,y+1.3,.65),P(.05,y+1.3,3.35)],'#eceddb',2);line([P(.05,y,1.9),P(.05,y+2.6,1.9)],'#eceddb',2);line([P(.1,y+2.1,.8),P(.1,y+1.3,3.1)],'#e0eee044',3)}
 line([P(0,0,3.8),P(20,0,3.8)],'#b4c1ab',3);line([P(0,0,3.8),P(0,16,3.8)],'#b4c1ab',3);
 plane(1.3,1.3,6.9,4.4,.015,'#c9ccb0');for(let i=0;i<5;i++)line([P(1.5,1.6+i*.75,.02),P(7.9,1.6+i*.75,.02)],'#bcc3a5',1);
 plane(3.2,6.1,12.5,7.8,.015,'#d8d8bd');plane(15.6,10.2,3.9,4.8,.02,'#bcc9a5');plane(14.1,.7,5.2,5.7,.02,'#c0caaa');
 worldText('R4ISE',8.9,.07,2.3,33,'#4e694c');worldText('SMART MONEY OPERATIONS',8.92,.07,1.62,6.7,'#79906c');
 worldText('PULSE / CEO',15.7,.07,2.5,10,'#69805a');
 // Wall notice board and clock.
 poly([P(2,.04,1.2),P(5,.04,1.2),P(5,.04,3),P(2,.04,3)],'#b3bf98');worldText('THINK BIG.',2.2,.07,2.6,9,'#f0f1db');worldText('STAY CURIOUS.',2.2,.07,2.1,7,'#f0f1db');worldText('BUILD TOGETHER.',2.2,.07,1.65,6,'#f0f1db');
 const cp=P(7.1,.08,2.65);ctx.fillStyle='#eff0dd';ctx.beginPath();ctx.ellipse(...cp,12,14,.1,0,Math.PI*2);ctx.fill();line([[cp[0],cp[1]-8],cp,[cp[0]+6,cp[1]+4]],'#779066',1.5);
}
function meeting(){box(2,2,4.8,1.4,.15,'#c8b28a','#ab9671','#bfa782',1.05);for(let i=0;i<3;i++){chair(2.7+i*1.5,3.8,'#9aa889');box(2.4+i*1.5,2.4,.5,.3,.03,'#ecebd7','#d7d9bc','#dae0c5',1.22)}box(3.9,2.5,.3,.3,.25,'#96ab74','#72865c','#889e67',1.23)}
function pantry(){box(17.4,8.7,2.1,.75,1.3,'#d9d8bc','#a9b59a','#bec5a8');box(18.3,8.8,.65,.45,.8,'#526451','#4d604b','#687958',1.3);box(18.43,9.25,.25,.08,.2,'#d8dcc5','#bdd0b4','#d4dec4',1.45);box(17.6,8.8,.23,.23,.22,'#f1ecd9','#ddd5bd','#e3dec9',1.3)}
function lamp(x,y){box(x,y,.55,.55,.05,'#7f896e','#727f61','#828d6e');const p=P(x+.25,y+.25);line([p,P(x+.25,y+.25,2.4)],'#6f7e60',2);const q=P(x+.25,y+.25,2.4);poly([[q[0]-12,q[1]],[q[0]+12,q[1]],[q[0]+8,q[1]-12],[q[0]-8,q[1]-12]],'#eee5c5')}
const furniture=[];const add=(x,y,fn)=>furniture.push({depth:x+y,draw:fn});
deskPositions.forEach(([x,y],i)=>add(x+1.4,y+1,()=>desk(x,y,i===6)));add(4.4,3,meeting);add(18.5,9.1,pantry);add(17.3,13.7,()=>sofa(16.6,13.3));add(18.4,12.5,()=>sofa(18.2,11.8,1.4));add(16.7,11.8,()=>{box(16.3,11.4,1.3,.9,.55,'#dac6a0','#b7a582','#c8b691');box(16.45,11.6,.4,.3,.035,'#eff0d8','#dadbc4','#ecebd5',.55);box(17,11.7,.2,.2,.2,'#fcf3dc','#dedbc1','#ece2c9',.55)});
add(10.2,.9,()=>shelves(6,.35));add(13.8,3,()=>glassX(13.8,.5,5.4));add(16,6,()=>{glassY(14,6,2);glassY(18,6,2)});add(4,5.4,()=>{glassY(.3,5.4,2);glassY(6,5.4,2)});
[[1,1,1.25],[1,6,1.1],[1,14.7,1.4],[19,1,1.4],[19,7,1.1],[15.7,14.7,.9],[3,10,.7],[11.3,6,.75],[7.2,10,.6]].forEach(([x,y,s])=>add(x,y,()=>plant(x,y,s)));add(19.2,14.7,()=>lamp(19,14.5));
function sprite(g,a,t,moving=false,portraitMode=false){
 const seated=!portraitMode&&!moving&&(a.status==='busy'||a.status==='waiting');const state=a.status,walk=moving?Math.sin(t*12+a.phase):0,breath=portraitMode?0:Math.sin(t*2+a.phase)*.4;
 g.save();g.translate(0,breath);const px=(x,y,w,h,c)=>{g.fillStyle=c;g.fillRect(Math.round(x),Math.round(y),w,h)};
 if(!portraitMode){g.fillStyle='#3c493520';g.beginPath();g.ellipse(0,0,11,4,0,0,Math.PI*2);g.fill()}
 px(-7,-13+walk*2,5,11,'#3b4945');px(2,-13-walk*2,5,11,'#3b4945');px(-8,-4+walk*2,7,4,'#334138');px(1,-4-walk*2,8,4,'#334138');
 px(-9,-28,18,17,a.color);px(-7,-30,14,4,a.color);px(-8,-16,16,3,shade(a.color,-15));px(-3,-31,6,5,a.skin);px(-4,-28,8,2,'#c5d0b3');
 let arm=moving?walk*3:state==='busy'?Math.sin(t*13+a.phase)*1.7:0;
 px(-13,-27+arm,5,12,a.color);px(8,-27-arm,5,12,a.color);px(-13,-16+arm,5,4,a.skin);px(8,-16-arm,5,4,a.skin);
 px(-9,-46,18,16,a.skin);px(-11,-41,3,7,a.skin);px(8,-41,3,7,a.skin);px(-9,-49,18,8,a.hair);px(-11,-46,4,9,a.hair);px(-6,-51,12,3,a.hair);px(6,-45,4,6,a.hair);
 if(seated){px(-9,-44,18,13,a.hair);px(-7,-32,14,2,a.hair)}else{px(-5,-39,2,2,'#393d32');px(4,-39,2,2,'#393d32');px(-1,-34,4,1,'#aa7959')}
 if(a.id==='echo'){px(-12,-42,4,17,a.hair);px(8,-42,4,17,a.hair)}
 if(a.id==='cipher'&&!seated){px(-8,-40,7,4,'#263d3b');px(2,-40,7,4,'#263d3b');px(-1,-39,3,1,'#263d3b');px(-6,-39,3,1,'#bfd4cb');px(4,-39,3,1,'#bfd4cb')}
 if(a.id==='relay'){px(-11,-46,22,4,'#d2d9b5');px(-12,-40,3,8,'#476a51');px(9,-40,3,8,'#476a51')}
 if(a.id==='pulse'){px(-2,-27,3,8,'#d5e8b3');px(-1,-19,2,2,'#d5e8b3')}
 if(state==='resting'&&!moving&&!portraitMode){px(9,-19,7,7,'#f3e8ce');px(16,-17,2,4,'#dbd4b8');g.strokeStyle='#f4f4dd';g.lineWidth=1;g.beginPath();g.moveTo(12,-23);g.quadraticCurveTo(9+Math.sin(t*2),-27,13,-30);g.stroke()}
 g.restore();
}
function shade(hex,amt){return '#'+hex.slice(1).match(/../g).map(c=>clamp(parseInt(c,16)+amt,0,255).toString(16).padStart(2,'0')).join('')}
let hitTargets=[];
function drawAgent(a){const p=P(a.x,a.y),moving=a.path.length>0;ctx.save();ctx.translate(...p);if(selected===a.id){ctx.strokeStyle='#739658';ctx.lineWidth=1.4;ctx.beginPath();ctx.ellipse(0,0,16,7,0,0,Math.PI*2);ctx.stroke()}sprite(ctx,a,reduced?0:elapsed,moving);ctx.restore();hitTargets.push({id:a.id,x:p[0],y:p[1]-25});
 const isSel=selected===a.id, name=a.name+(a.id==='pulse'?' · CEO':'');ctx.font=`600 8px 'DM Sans',sans-serif`;const tw=ctx.measureText(name).width;ctx.fillStyle=isSel?'#3d6748':'#f9faedee';ctx.beginPath();ctx.roundRect(p[0]-tw/2-6,p[1]+5,tw+12,15,4);ctx.fill();label(name,p[0],p[1]+15,isSel?'#f5f5db':'#647756',8);
 if(a.status==='busy'&&!moving){for(let j=0;j<3;j++){ctx.fillStyle='#b5cda0';const h=3+(Math.sin(elapsed*5+j*1.5)+1)*3;ctx.fillRect(p[0]+15+j*4,p[1]-30-h,2,h)}}
 if((isSel||((Math.floor(elapsed/5)+agents.indexOf(a))%7===0))&&!moving){const txt=a.status==='busy'?(a.id==='pulse'?'Semua on track.':a.task):a.status==='resting'?'Kopi dulu, ya.':a.status==='error'?'Butuh perhatian.':a.status==='offline'?'Menunggu engine…':'Siap. Menunggu tugas.';ctx.font=`500 8px 'DM Sans',sans-serif`;const bw=Math.min(180,ctx.measureText(txt).width+18),bx=p[0]-bw/2,by=p[1]-76;ctx.fillStyle='#fefdf2';ctx.shadowColor='#52653f10';ctx.shadowBlur=8;ctx.beginPath();ctx.roundRect(bx,by,bw,21,5);ctx.fill();ctx.shadowBlur=0;poly([[p[0]-3,by+20],[p[0]+3,by+20],[p[0],by+25]],'#fefdf2');label(txt,p[0],by+14,'#7e8d6d',8)}
}
function render(){
 ctx.setTransform(Math.min(devicePixelRatio||1,2),0,0,Math.min(devicePixelRatio||1,2),0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle='#edf1e4';ctx.fillRect(0,0,width,height);
 // Quiet room-like backdrop and soft grounded shadow.
 const grad=ctx.createRadialGradient(width*.51,height*.55,20,width*.5,height*.53,width*.65);grad.addColorStop(0,'#f5f5e9');grad.addColorStop(1,'#e9efde');ctx.fillStyle=grad;ctx.fillRect(0,0,width,height);
 viewScale=(width<550?.57:Math.min(width/1060,height/650))*zoom;offsetX=(width-1060*viewScale)/2+pan.x;offsetY=(height-650*viewScale)/2+pan.y+8;ctx.translate(offsetX,offsetY);ctx.scale(viewScale,viewScale);
 ctx.fillStyle='#536f3920';ctx.beginPath();ctx.ellipse(555,438,395,115,.06,0,Math.PI*2);ctx.fill();drawBase();
 hitTargets=[];const items=[...furniture,...agents.map(a=>({depth:a.x+a.y,draw:()=>drawAgent(a)}))].sort((a,b)=>a.depth-b.depth);for(const i of items)i.draw();
 // Etched zone labels on front floor, never fake operational counters.
 const s=P(7.6,15.1,.03);label('R 4 I S E   /   O P E R A T I O N S',s[0],s[1],'#9f9e7d',8);const lounge=P(17.5,15.2,.03);label('THE SLOW CORNER',lounge[0],lounge[1],'#829772',7);
}
function resize(){const r=canvas.getBoundingClientRect();if(!r.width||!r.height)return;width=r.width;height=r.height;const d=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(width*d);canvas.height=Math.round(height*d);render()}
new ResizeObserver(resize).observe($('#world-wrap'));
let pointer=null;
canvas.addEventListener('pointerdown',e=>{pointer={x:e.clientX,y:e.clientY,px:pan.x,py:pan.y,moved:false};canvas.setPointerCapture(e.pointerId)});
canvas.addEventListener('pointermove',e=>{if(!pointer)return;const dx=e.clientX-pointer.x,dy=e.clientY-pointer.y;if(Math.abs(dx)+Math.abs(dy)>5)pointer.moved=true;if(pointer.moved){pan.x=clamp(pointer.px+dx,-width*.55,width*.55);pan.y=clamp(pointer.py+dy,-height*.45,height*.45)}});
canvas.addEventListener('pointerup',e=>{if(pointer&&!pointer.moved){const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left-offsetX)/viewScale,y=(e.clientY-r.top-offsetY)/viewScale;const a=hitTargets.map(p=>({...p,d:Math.hypot(p.x-x,p.y-y)})).sort((a,b)=>a.d-b.d)[0];if(a&&a.d<38)select(a.id)}pointer=null});canvas.addEventListener('pointercancel',()=>pointer=null);
function tickDemo(){for(let i=0;i<agents.length;i++){const a=agents[i],cycle=34+(i%3)*7,u=(elapsed+i*5)%cycle;const s=a.id==='pulse'?(u<23?'busy':'waiting'):u<13?'busy':u<21?'waiting':'resting';if(a.status==='busy'&&s!=='busy')a.done++;changeState(a,s,s==='busy'?a.task:s==='resting'?'Tidak ada tugas aktif. Rehat sambil menunggu.':'Menunggu antrean atau jadwal berikutnya.')}}
function step(dt){if(mode==='demo'&&!playing)return;elapsed+=dt*(mode==='demo'?speed:1);if(mode==='demo')tickDemo();for(const a of agents){if(reduced){a.x=destination(a)[0];a.y=destination(a)[1];a.path=[];continue}if(a.path.length){const target=a.path[0],dx=target[0]-a.x,dy=target[1]-a.y,dist=Math.hypot(dx,dy),move=dt*2.5*(mode==='demo'?speed:1);if(dist<.001||dist<=move){a.x=target[0];a.y=target[1];a.path.shift()}else{a.x+=dx/dist*move;a.y+=dy/dist*move}}}}
let last=performance.now(),uiLast=0;
function animate(now){const dt=clamp((now-last)/1000,0,.08);last=now;if(!document.hidden){step(dt);render();if(now-uiLast>900){canvas.dataset.agents=JSON.stringify(agents.map(a=>({id:a.id,x:a.x,y:a.y,path:a.path,state:a.status})));renderTeam();renderDetail();uiLast=now}}requestAnimationFrame(animate)}
$('#clock').textContent=new Date().toLocaleTimeString('id-ID',{timeZone:'Asia/Jakarta',hour:'2-digit',minute:'2-digit'})+' WIB';setInterval(()=>$('#clock').textContent=new Date().toLocaleTimeString('id-ID',{timeZone:'Asia/Jakarta',hour:'2-digit',minute:'2-digit'})+' WIB',30000);
for(const a of agents){a.path=route([a.x,a.y],destination(a))}addEvent('Pulse','Selamat datang di R4ISE. Mari mulai hari ini.','busy');addEvent('Echo','Registry demo siap. Menunggu pembaruan berikutnya.');renderTeam();renderDetail();setModeUI();resize();requestAnimationFrame(animate);
if(location.protocol==='http:'||location.protocol==='https:'){mode='live';events=[];setModeUI();for(const a of agents)changeState(a,'offline','Menunggu engine lokal.',false);renderTeam();renderDetail();renderEvents();liveTimer=setInterval(pollLive,1000);pollLive();}
// Expose a read-only diagnostic summary for smoke tests; no live controls.
window.r4iseDiagnostics=()=>({mode,playing,speed,selected,agents:agents.map(a=>({id:a.id,state:a.status,x:a.x,y:a.y,done:a.done,moving:!!a.path.length})),events:events.length,zoom,hasLiveHeartbeat:!!lastSnapshot});
})();
