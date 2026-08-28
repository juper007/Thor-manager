const memoryModal=document.getElementById('memoryModal');
async function showMemory(){
  const s=await fetch('/api/stats').then(r=>r.json()),d=s.memory_detail;if(!d)return;
  const f=n=>n>1e9?(n/1e9).toFixed(1)+' GB':(n/1e6).toFixed(1)+' MB';
  const parts=[['Used',d.used,'#76b900'],['Cache',d.cached,'#a577ff'],['Buffers',d.buffers,'#25c7d9'],['Free',d.free,'#475159'],['Shared',d.shared,'#ffb454']];
  document.getElementById('memoryBar').innerHTML=parts.map(p=>`<i style="width:${p[1]*100/d.total}%;background:${p[2]}"></i>`).join('');
  document.getElementById('memoryLegend').innerHTML=parts.map(p=>`<div style="--c:${p[2]}"><span>${p[0].toUpperCase()}</span><strong>${f(p[1])}</strong></div>`).join('')+`<div style="--c:#879299"><span>AVAILABLE</span><strong>${f(d.available)}</strong></div>`;
  document.getElementById('processes').innerHTML=(d.processes||[]).map(p=>`<div class="proc"><span>${p.pid}</span><b>${p.name}</b><strong>${f(p.rss)}</strong></div>`).join('');memoryModal.classList.add('open');
}
document.getElementById('memoryCard').onclick=showMemory;document.getElementById('closeMemory').onclick=()=>memoryModal.classList.remove('open');memoryModal.onclick=e=>{if(e.target===memoryModal)memoryModal.classList.remove('open')};document.addEventListener('keydown',e=>{if(e.key==='Escape')memoryModal.classList.remove('open')});
const tabStyle=document.createElement('link');tabStyle.rel='stylesheet';tabStyle.href='/tabs.css';document.head.appendChild(tabStyle);const tabScript=document.createElement('script');tabScript.src='/tabs.js';document.body.appendChild(tabScript);
