const $=s=>document.querySelector(s),NS='http://www.w3.org/2000/svg',G=300;
const COL={idle:'#8290B7',delivering:'#4D6EF7',negotiating:'#F3B63F',failed:'#F16B73'};
const PRI={emergency:'#F16B73',high:'#8C7CF6',normal:'#5D7CF7',low:'#8290B7'};
const KIND={
 task:['#5D7CF7','Task'],auction:['#4D6EF7','Auction'],intersection:['#F3B63F','Intersection'],
 fault:['#F16B73','Failure'],heal:['#8C7CF6','Self-healing'],done:['#57B894','Delivery'],
 communication:['#D65CBE','Communication'],traffic:['#E49B3A','Traffic'],override:['#F16B73','Human override'],system:['#8290B7','System']
};
let mode='live',S=null,running=false,timer=null,n=0,hist=[],replay=[],ri=0,sel=null,seen=0,pick=0,filt='all',busy=false;

const api=async(p,b)=>{
 const r=await fetch('/api/'+p,{method:b===undefined?'GET':'POST',
   headers:{'Content-Type':'application/json'},body:b===undefined?undefined:JSON.stringify(b)});
 if(!r.ok){let e={};try{e=await r.json()}catch(_){}
   throw new Error(e.error||p)} return r.json();
};
const banner=t=>{const b=$('#banner');b.hidden=!t;b.innerHTML=t||''};
const fy=y=>G-y;
const kind=m=>{
 if(/override/i.test(m))return'override';
 if(/re-auction|repaired|self-heal|stranded/i.test(m))return'heal';
 if(/failed|stranded|blocked|critical/i.test(m))return'fault';
 if(/yield|cleared|negotiat|GO /i.test(m))return'intersection';
 if(/communication|rejoin|lost/i.test(m))return'communication';
 if(/traffic/i.test(m))return'traffic';
 if(/won|bid/i.test(m))return'auction';
 if(/completed|picked up|created/i.test(m))return'done';
 return'task';
};
const el=(t,a={},p)=>{
 const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.append(e);return e;
};

const map=$('#map'),L={};
function scaffold(){
 map.innerHTML='';
 const bg=el('g',{},map);
 [100,200].forEach(v=>{
   el('path',{d:`M${v} 0V300M0 ${fy(v)}H300`,class:'road'},bg);
   el('path',{d:`M${v} 0V300M0 ${fy(v)}H300`,class:'lane'},bg);
 });
 L.zones=el('g',{},map);L.traffic=el('g',{},map);L.tasks=el('g',{},map);
 L.veh=el('g',{},map);L.veh.m={};
 L.labels=el('g',{},map);
}
map.addEventListener('click',e=>{
 if(!pick)return;
 const p=map.createSVGPoint();p.x=e.clientX;p.y=e.clientY;
 const q=p.matrixTransform(map.getScreenCTM().inverse());
 const x=Math.round(Math.max(0,Math.min(G,q.x))),y=Math.round(Math.max(0,Math.min(G,G-q.y)));
 const f=$('#taskForm');
 if(pick===1){f.px.value=x;f.py.value=y;pick=2;toast('Pickup set — now choose the dropoff','task')}
 else{f.dx.value=x;f.dy.value=y;pick=0;map.classList.remove('picking');$('#dlg').showModal()}
});

function vehEl(v){
 let g=L.veh.m[v.id];if(g)return g;
 g=el('g',{class:'veh'},L.veh);
 el('circle',{r:8,class:'pulse'},g);
 el('circle',{r:9,fill:'none',stroke:'#26345F','stroke-width':1.5},g);
 el('circle',{r:9,class:'ring',pathLength:100,'stroke-dasharray':'100 100'},g);
 el('circle',{r:5.5,class:'body'},g);
 const t=el('text',{y:-12,'text-anchor':'middle',class:'vlabel'},g);t.textContent=v.id;
 L.veh.m[v.id]=g;return g;
}

function renderIntersections(){
 L.zones.innerHTML='';L.traffic.innerHTML='';L.labels.innerHTML='';
 (S.intersections||[]).forEach(i=>{
   const tr=i.traffic||S.traffic[i.id]||{level:0,label:'Light'};
   const busyVehicles=(S.vehicles||[]).filter(v=>v.status!=='failed'&&Math.hypot(v.x-i.x,v.y-i.y)<=i.radius+18);
   const override=i.override_vehicle;
   const zg=el('g',{},L.zones);
   el('circle',{cx:i.x,cy:fy(i.y),r:i.radius+2,class:'zone '+(busyVehicles.length?'busy':''),
      'data-i':i.id},zg);
   const text=el('text',{x:i.x,y:fy(i.y)-i.radius-7,'text-anchor':'middle',class:'intersection-label'},zg);
   text.textContent=`${i.id} · ${busyVehicles.length?'BUSY':'CLEAR'}`;
   const tg=el('g',{},L.traffic);
   const barW=24,barH=3,level=tr.level||0;
   el('rect',{x:i.x-barW/2,y:fy(i.y)+i.radius+5,width:barW,height:barH,rx:2,class:'traffic-track'},tg);
   el('rect',{x:i.x-barW/2,y:fy(i.y)+i.radius+5,width:barW*level,height:barH,rx:2,class:'traffic-fill'},tg);
   const tt=el('text',{x:i.x,y:fy(i.y)+i.radius+15,'text-anchor':'middle',class:'traffic-label'},tg);
   tt.textContent=`${tr.label||'Light'} traffic`;
   if(override){
     const ov=el('text',{x:i.x,y:fy(i.y)+i.radius+27,'text-anchor':'middle',class:'override-label'},tg);
     ov.textContent=`HUMAN → ${override} GO`;
   }
 });
}

function render(){
 if(!S)return;
 $('#map').style.setProperty('--tick',($('#speed').value*.95/1000)+'s');
 renderIntersections();
 const V=S.vehicles||[],T=S.tasks||[];
 V.forEach(v=>{
   const g=vehEl(v);
   g.style.transform=`translate(${v.x}px,${fy(v.y)}px)`;
   g.setAttribute('class','veh '+v.status+(!v.connected?' disconnected':''));
   g.children[3].setAttribute('fill',COL[v.status]||COL.idle);
   g.children[2].setAttribute('stroke',v.battery<25?COL.failed:COL[v.status]||COL.idle);
   g.children[2].setAttribute('stroke-dasharray',`${v.battery} 100`);
   g.children[4].textContent=v.id+(!v.connected?' · ×':'');
 });
 L.tasks.innerHTML='';
 const vm=Object.fromEntries(V.map(v=>[v.id,v]));
 V.forEach(v=>{
   if(v.status!=='failed' && v.route && v.route.length){
     const pts=[[v.x,v.y],...v.route].map(p=>`${p[0]},${fy(p[1])}`).join(' ');
     el('polyline',{points:pts,fill:'none',stroke:COL[v.status]||COL.idle,
       'stroke-width':1.2,'stroke-dasharray':'3 3',opacity:.72,class:'route-line'},L.tasks);
   }
 });
 T.filter(t=>t.status!=='completed').forEach(t=>{
   const c=PRI[t.priority]||PRI.normal;
   const assigned=vm[t.assigned_to];
   const active=assigned&&assigned.current_task===t.id;
   if(!active){
     el('rect',{x:t.pickup[0]-3.5,y:fy(t.pickup[1])-3.5,width:7,height:7,
       fill:t.status==='pending'?'#fff':c+'55',stroke:c,'stroke-width':1.3},L.tasks);
     const x=el('text',{x:t.pickup[0]+6,y:fy(t.pickup[1])+2,class:'task-label'},L.tasks);x.textContent=t.id;
   }
   el('path',{d:`M${t.dropoff[0]} ${fy(t.dropoff[1])}m0-6l5 6l-5 6l-5-6z`,fill:c},L.tasks);
 });
 $('#tickLbl').textContent=`${mode==='live'?'LIVE':'REPLAY'} · t=${S.time??n}`;
 $('#fleet').innerHTML=V.map(v=>`
 <button class="vc ${sel===v.id?'sel':''}" data-id="${v.id}" style="--c:${COL[v.status]||COL.idle}">
   <div class="r"><b>${v.id}</b><span class="pill">${v.status}</span></div>
   <div class="bar"><i style="width:${v.battery}%;background:${v.battery<25?COL.failed:COL.delivering}"></i></div>
   <div class="r"><small>${v.battery}% battery · cap ${v.capacity}</small><small>${v.connected?'● linked':'× offline'}</small></div>
   <small>${v.current_task?v.current_task+' → '+v.destination.map(Math.round).join(', '):'no active task'}</small>
 </button>`).join('');
 const sv=sel&&V.find(v=>v.id===sel);
 $('#selPanel').innerHTML=sv?`
   <div class="selected-head"><b>${sv.id}</b><span>${sv.connected?'Communication online':'Communication lost'}</span></div>
   <div class="act">
     <button data-fail="breakdown" ${mode!=='live'||sv.status==='failed'?'disabled':''}>Fail</button>
     <button data-fail="battery_critical" ${mode!=='live'||sv.status==='failed'?'disabled':''}>Critical battery</button>
     <button data-fail="blocked" ${mode!=='live'||sv.status==='failed'?'disabled':''}>Block path</button>
     <button data-comm ${mode!=='live'?'disabled':''}>${sv.connected?'Lose comms':'Restore comms'}</button>
     <button data-repair ${mode!=='live'||sv.status!=='failed'?'disabled':''}>Repair</button>
   </div>`:'<p class="hint">Select a vehicle to test failure or communication-loss recovery.</p>';

 $('#tasks').innerHTML=T.length?T.slice().reverse().map(t=>`
   <div class="tk ${t.status==='completed'?'done':''}" style="--c:${PRI[t.priority]||PRI.normal}">
     <b>${t.id}</b><span class="chip">${t.priority}</span>
     <span>${t.status}${t.assigned_to?' · '+t.assigned_to:''}</span>
     <small>${t.picked_up?'cargo onboard':'pickup'} · ${t.dropoff.join(', ')}</small>
   </div>`).join(''):'<p class="hint">No tasks yet.</p>';

 const row=e=>`<li style="--c:${KIND[kind(e.message)][0]}" data-k="${kind(e.message)}"><time>t=${e.time}</time><span>${e.message}</span></li>`;
 $('#feed').innerHTML=(S.log||[]).slice(-40).reverse().map(row).join('');
 (S.log||[]).slice(seen).forEach(e=>{
   const k=kind(e.message);
   if(['fault','heal','intersection','communication','override'].includes(k))toast(e.message,k);
 });
 seen=(S.log||[]).length;
 analytics(row);updateControls();
}

function toast(m,k){
 const d=document.createElement('div');d.className='toast';d.style.setProperty('--c',KIND[k][0]);
 d.textContent=m;$('#toasts').append(d);while($('#toasts').children.length>3)$('#toasts').firstChild.remove();
 setTimeout(()=>d.remove(),5200);
}

function analytics(row){
 const logs=S.log||[],V=S.vehicles||[],T=S.tasks||[];
 const rows=[];
 logs.forEach(e=>{
   const m=e.message.match(/^Vehicle (\w+) won Task (\w+) \(best of (\d+) bid\(s\): score ([\d.]+), ([\d.]+) units away, battery (\d+)%/);
   const r=e.message.match(/^Task (\w+) re-auctioned: (\w+) → (\w+) \(best of (\d+) bid\(s\): score ([\d.]+), ([\d.]+) units away, battery (\d+)%/);
   if(m)rows.push([e.time,m[2],'Auction','—',m[1],m[3],m[4],m[5],m[6]]);
   if(r)rows.push([e.time,r[1],'Re-auction',r[2],r[3],r[4],r[5],r[6],r[7]]);
 });
 const count=(rx)=>logs.filter(e=>rx.test(e.message)).length;
 const avg=Math.round(V.reduce((a,v)=>a+v.battery,0)/(V.length||1));
 const m=S.metrics||{};
 $('#metrics').innerHTML=[
  [S.completed_count,'Deliveries completed'],[T.filter(t=>t.status==='assigned').length,'Tasks in progress'],
  [T.filter(t=>t.status==='pending').length,'Tasks awaiting bids'],[V.filter(v=>v.status==='failed').length,'Vehicles failed'],
  [m.reauctions??count(/re-auctioned/),'Re-auctions'],[count(/\bYIELD\b|yielded/),'GO / YIELD decisions'],
  [count(/Communication (LOST|RESTORED)/),'Comms events'],[count(/Human override/),'Human overrides'],
  [avg+'%','Average battery']
 ].map(([a,b])=>`<div class="m"><b>${a}</b><span>${b}</span></div>`).join('');
 $('#auctions').innerHTML='<tr><th>Time</th><th>Task</th><th>Type</th><th>Released by</th><th>Winner</th><th>Bids</th><th>Score</th><th>Distance</th><th>Battery</th></tr>'+
 (rows.length?rows.slice().reverse().map(r=>`<tr><td>t=${r[0]}</td><td><b>${r[1]}</b></td><td>${r[2]}</td><td>${r[3]}</td><td><b>${r[4]}</b></td><td>${r[5]}</td><td>${r[6]}</td><td>${r[7]} u</td><td>${r[8]}%</td></tr>`).join(''):'<tr><td colspan="9">No auctions yet.</td></tr>');
 $('#chips').innerHTML=['all',...Object.keys(KIND)].map(k=>`<button data-chip="${k}" class="${filt===k?'on':''}">${k==='all'?'All':KIND[k][1]}</button>`).join('');
 $('#fullLog').innerHTML=logs.slice().reverse().map(row).filter(h=>filt==='all'||h.includes(`data-k="${filt}"`)).join('');
 chart('#ch1',hist.map(h=>h.done),null);chart('#ch2',hist.map(h=>h.bat),100);
}

function chart(s,d,max){
 const svg=$(s);if(d.length<2){svg.innerHTML='';return}
 const mx=max||Math.max(1,...d),w=400,h=140;
 svg.innerHTML=`<path d="${d.map((v,i)=>(i?'L':'M')+(i/(d.length-1)*w).toFixed(1)+' '+(h-8-v/mx*(h-16)).toFixed(1)).join('')}"/>`;
}
function ingest(s,adv=true){
 S=s;if(adv)n++;hist.push({done:s.completed_count||0,bat:(s.vehicles||[]).reduce((a,v)=>a+v.battery,0)/((s.vehicles||[]).length||1)});
 render();
}
async function step(){
 if(busy)return;busy=true;
 try{
   if(mode==='live')ingest(await api('tick',{}));
   else if(ri<replay.length)ingest(replay[ri++]);else pause();
 }catch(e){pause();banner(`<b>Connection error:</b> ${e.message}. Restart <code>python server.py</code>.`)}
 busy=false;
}
function run(){running=true;$('#bRun').textContent='Pause';clearInterval(timer);timer=setInterval(step,+$('#speed').value)}
function pause(){running=false;$('#bRun').textContent='Start';clearInterval(timer)}
async function reset(){
 pause();n=0;hist=[];seen=0;ri=0;sel=null;scaffold();$('#toasts').innerHTML='';
 if(mode==='live')S=await api('reset',{random:$('#scenario').value==='1'});else S=replay[0];
 ingest(S,false);if(mode!=='live')ri=1;
}
async function act(p,b){
 if(mode!=='live')return;
 try{banner('');ingest(await api(p,b),false)}
 catch(e){banner(`<b>Action failed:</b> ${e.message}`)}
}
function updateControls(){
 const V=S?.vehicles||[],sv=sel&&V.find(v=>v.id===sel);
 $('#bFail').disabled=mode!=='live'||!V.some(v=>v.id==='V3');
 $('#bAdd').disabled=mode!=='live';
 $('#bTraffic').disabled=mode!=='live';
 $('#bOverride').disabled=mode!=='live';
 if(sv)$('#bComm').textContent=sv.connected?`Lose ${sv.id} comms`:`Restore ${sv.id} comms`;
}

$('#bRun').onclick=()=>running?pause():run();
$('#bStep').onclick=step;$('#bReset').onclick=reset;
$('#scenario').onchange=()=>mode==='live'&&reset();
$('#speed').onchange=()=>{if(running)run();render()};
$('#bFail').onclick=()=>{
 const v=S.vehicles.find(v=>v.id==='V3');if(!v)return;
 act(v.status==='failed'?'repair':'fail',{id:'V3',mode:'breakdown'});
};
$('#bComm').onclick=()=>{if(!sel)return;const v=S.vehicles.find(v=>v.id===sel);act('communication',{id:sel,connected:!v.connected,duration:6})};
$('#fleet').onclick=e=>{
 const b=e.target.closest('.vc');if(b){sel=sel===b.dataset.id?null:b.dataset.id;render()}
};
$('#selPanel').onclick=e=>{
 const b=e.target.closest('button');if(!b||!sel)return;
 if(b.dataset.repair!==undefined)act('repair',{id:sel});
 else if(b.dataset.comm!==undefined){
   const v=S.vehicles.find(v=>v.id===sel);act('communication',{id:sel,connected:!v.connected,duration:6});
 }else act('fail',{id:sel,mode:b.dataset.fail});
};
$('#chips').onclick=e=>{const b=e.target.closest('[data-chip]');if(b){filt=b.dataset.chip;render()}};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
 document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
 $('#live').hidden=t.dataset.view!=='live';$('#analytics').hidden=t.dataset.view!=='analytics';
});
const rnd=()=>Math.round(10+Math.random()*280);
$('#bAdd').onclick=()=>{const f=$('#taskForm');[f.px,f.py,f.dx,f.dy].forEach(i=>i.value=i.value||rnd());$('#dlg').showModal()};
$('#tfRand').onclick=()=>{const f=$('#taskForm');[f.px,f.py,f.dx,f.dy].forEach(i=>i.value=rnd())};
$('#tfCancel').onclick=()=>$('#dlg').close();
$('#tfPick').onclick=()=>{$('#dlg').close();pick=1;map.classList.add('picking');document.querySelector('[data-view=live]').click();toast('Click pickup, then dropoff on the map','task')};
$('#taskForm').onsubmit=e=>{
 const f=e.target;if(e.submitter&&e.submitter.value==='ok')
   act('task',{pickup:[+f.px.value,+f.py.value],dropoff:[+f.dx.value,+f.dy.value],priority:f.pr.value});
};
$('#bTraffic').onclick=()=>{
 const id=$('#intersection').value;act('traffic',{intersection:id,level:+$('#trafficLevel').value});
};
$('#bOverride').onclick=()=>{
 const id=$('#intersection').value,vid=$('#overrideVehicle').value;
 const current=S?.emergency_overrides?.[id];
 act('override',{intersection:id,vehicle:current?null:vid});
};

(async()=>{
 scaffold();
 try{S=await api('state');mode='live';}
 catch(e){
   try{
     const t=await(await fetch('sample_states.jsonl')).text();
     replay=t.trim().split('\n').map(JSON.parse);mode='replay';
     banner('<b>Replay mode.</b> Start <code>python server.py</code> for live controls and coordination actions.');
   }catch(e2){banner('<b>Cannot load data.</b> Run <code>python server.py</code> in this folder.');return}
 }
 if(mode==='replay')await reset();else ingest(S,false);
})();
