'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let data=null,selected=null,dirty=false,editorSignature='',tabName='studio';
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const csrf=$('meta[name="csrf-token"]').content;
async function api(path,method='GET',body){
 const options={method,headers:{'X-CSRF':csrf}};
 if(body instanceof FormData)options.body=body;
 else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const r=await fetch('/api'+path,options);const d=await r.json();
 if(r.status===401){location.href='/login';throw Error('Sign in again');}
 if(!r.ok)throw Error(d.error||'Request failed');return d;
}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,7000);}
function guarded(fn){return async e=>{try{await fn(e);}catch(x){toast(x.message);}};}
function options(kind,value,empty='None'){
 return `<option value="">${empty}</option>`+data.assets.filter(a=>a.kind===kind).map(a=>`<option value="${esc(a.id)}" ${a.id===value?'selected':''}>${esc(a.name)}</option>`).join('');
}
function field(label,name,value,type='text'){return `<label>${label}<input name="${name}" type="${type}" ${type==='number'?'step="any"':''} value="${esc(value)}"></label>`;}
function textarea(label,name,value){return `<label>${label}<textarea name="${name}">${esc(value)}</textarea></label>`;}
function sceneMarkup(s,i){return `<div class="scene" data-index="${i}"><div class="scene-head"><span>SCENE ${String(i+1).padStart(2,'0')}</span><div><button type="button" data-move="${i}" data-dir="-1" aria-label="Move scene up">↑</button> <button type="button" data-move="${i}" data-dir="1" aria-label="Move scene down">↓</button> <button type="button" data-remove="${i}" aria-label="Remove scene">Remove</button></div></div>
 ${textarea('Setting & lighting','setting',s.setting)}<div class="field-row">${textarea('Facial expression','expression',s.expression)}${textarea('Action & movement','action',s.action)}</div>
 ${textarea('Camera direction','camera',s.camera)}${textarea('Narration / captions','narration',s.narration)}<div class="field-row"><label>Scene length<select name="duration"><option value="5" ${s.duration===5?'selected':''}>5 seconds</option><option value="10" ${s.duration===10?'selected':''}>10 seconds</option></select></label><label>Use existing footage<select name="clip">${options('video',s.clip,'Generate animation')}</select></label></div>${field('Footage trim start (seconds)','trim',s.trim||0,'number')}</div>`;}
function readEditor(){
 const p=structuredClone(data.projects.find(p=>p.id===selected));const f=$('#project-form');if(!f)return p;
 for(const k of ['title','topic','character','format','reference','music','description'])p[k]=f.elements[k].value;
 for(const k of ['narration','captions'])p[k]=f.querySelector(`input[name="${k}"]`).checked;
 for(const k of ['voice_volume','music_volume'])p[k]=Number(f.elements[k].value);
 p.scenes=$$('.scene').map(el=>{const s={};for(const k of ['setting','expression','action','camera','narration','clip'])s[k]=el.querySelector(`[name="${k}"]`).value;
 for(const k of ['duration','trim'])s[k]=Number(el.querySelector(`[name="${k}"]`).value);return s;});return p;
}
function editor(p){
 if(!p)return;$('#editor').classList.remove('empty');editorSignature=JSON.stringify(p);
 $('#editor').innerHTML=`<div class="section-title"><h2>Director’s desk</h2><span class="tag">${p.output?'MP4 ready':p.scenes.length+' scenes'}</span></div><form id="project-form">
 ${field('Video title','title',p.title)}${textarea('Story brief','topic',p.topic)}<div class="field-row"><label>Format<select name="format"><option value="shorts" ${p.format==='shorts'?'selected':''}>Vertical · YouTube Shorts</option><option value="landscape" ${p.format==='landscape'?'selected':''}>Landscape · YouTube video</option></select></label><label>Character reference<select name="reference">${options('image',p.reference,'No reference image')}</select></label></div>
 ${textarea('Character identity & costume','character',p.character)}<p class="muted">Reuse a reference for visual consistency. Expression and action directions guide the model; results are not guaranteed.</p>
 <div class="actions"><button class="primary" type="button" id="generate-plan">Write scenes with AI</button><button type="button" id="add-scene">＋ Add scene</button></div><div id="scenes">${p.scenes.map(sceneMarkup).join('')}</div>
 <h3>Sound & finishing</h3><div class="field-row"><label class="check"><input name="narration" type="checkbox" ${p.narration?'checked':''}> AI narrator</label><label class="check"><input name="captions" type="checkbox" ${p.captions?'checked':''}> Burn in captions</label></div><label>Music track<select name="music">${options('audio',p.music,'No background music')}</select></label><div class="field-row">${field('Voice volume (0–1)','voice_volume',p.voice_volume,'number')}${field('Music volume (0–1)','music_volume',p.music_volume,'number')}</div>${textarea('YouTube description','description',p.description)}
 <div class="actions"><button type="submit">Save draft</button><button class="primary" type="button" id="produce">Create video</button><button type="button" id="clone">Duplicate project</button></div></form>
 ${p.output?`<h3>Finished video</h3><video class="preview" src="/media/${esc(p.output)}" controls></video><div class="actions"><a class="button" href="/media/${esc(p.output)}" download>Download MP4</a><button id="upload-youtube" class="primary" ${p.youtube_id?'disabled':''}>${p.youtube_id?'Uploaded':'Upload to YouTube'}</button></div>`:'<p class="muted">Your playable MP4 appears here after rendering. Original clip audio is replaced by the selected narration and music.</p>'}
 ${p.youtube_id?`<p class="good">YouTube video ID: ${esc(p.youtube_id)}</p>`:''}`;
 $('#project-form').addEventListener('input',()=>dirty=true);$('#project-form').addEventListener('change',()=>dirty=true);
 $('#project-form').onsubmit=guarded(async e=>{e.preventDefault();await saveDraft();toast('Draft saved');});
 $('#add-scene').onclick=()=>{const p=readEditor();if(p.scenes.length>=24)return toast('Maximum 24 scenes');p.scenes.push({setting:'A floating obstacle course at sunset',expression:'Eyes widen, then a confident smile',action:'Crouches, jumps across the gap and lands with arms out',camera:'Medium shot, smooth follow camera, hold on the reaction',narration:'One jump could change everything.',duration:5,trim:0,clip:''});editor(p);dirty=true;};
 $$('[data-remove]').forEach(b=>b.onclick=()=>{const p=readEditor();p.scenes.splice(Number(b.dataset.remove),1);editor(p);dirty=true;});
 $$('[data-move]').forEach(b=>b.onclick=()=>{const p=readEditor(),i=Number(b.dataset.move),j=i+Number(b.dataset.dir);if(j<0||j>=p.scenes.length)return;[p.scenes[i],p.scenes[j]]=[p.scenes[j],p.scenes[i]];editor(p);dirty=true;});
 $('#generate-plan').onclick=guarded(async()=>{if(!confirm('Generate new scenes with your connected OpenAI account? This replaces the scene plan and uses paid API credits.'))return;await saveDraft();await api(`/projects/${selected}/queue`,'POST',{kind:'plan'});toast('Story queued');await refresh();showTab('queue');});
 $('#produce').onclick=guarded(async()=>{if(!confirm('Create this video? Missing clips and narration use your paid provider accounts.'))return;await saveDraft();await api(`/projects/${selected}/queue`,'POST',{kind:'produce'});toast('Video queued');await refresh();showTab('queue');});
 $('#clone').onclick=guarded(async()=>{await saveDraft();const p=await api(`/projects/${selected}/clone`,'POST',{});selected=p.id;dirty=false;await refresh();});
 if($('#upload-youtube'))$('#upload-youtube').onclick=guarded(async()=>{if(dirty)return toast('Save your edits and render again before uploading.');if(!confirm(`Upload this MP4 to ${data.connections.channel.title||'your connected channel'} as ${data.settings.privacy}?`))return;await api(`/projects/${selected}/queue`,'POST',{kind:'upload'});await refresh();showTab('queue');});
}
async function saveDraft(){const p=await api(`/projects/${selected}`,'PUT',readEditor());dirty=false;data.projects[data.projects.findIndex(x=>x.id===selected)]=p;editor(p);return p;}
function fillForm(form,cfg){for(const [k,v] of Object.entries(cfg)){const el=form.elements[k];if(!el)continue;if(el.type==='checkbox')el.checked=!!v;else el.value=v===null?'':String(v);}}
function renderState(){
 $('#project-count').textContent=data.projects.length;
 $('#project-list').innerHTML=data.projects.length?data.projects.map(p=>`<button class="project ${selected===p.id?'selected':''}" data-project="${p.id}"><b>${esc(p.title)}</b><span>${p.format==='shorts'?'SHORTS':'LANDSCAPE'} · ${p.scenes.length} scenes ${p.output?'· Ready':''}</span></button>`).join(''):'<p class="muted">No projects yet.</p>';
 $$('[data-project]').forEach(b=>b.onclick=()=>{if(dirty&&!confirm('Discard unsaved edits?'))return;selected=b.dataset.project;dirty=false;editor(data.projects.find(p=>p.id===selected));renderState();});
 const p=data.projects.find(p=>p.id===selected);if(p&&!dirty&&JSON.stringify(p)!==editorSignature)editor(p);
 const c=data.connections;$('#worker-status').textContent=c.worker?'● Worker online':'○ Worker offline';$('#worker-status').className=c.worker?'good':'muted';
 $('#setup-banner').textContent=(!c.openai||!c.runway)?'Connect OpenAI and Runway to generate animated videos. You can start writing scenes and uploading assets now.':(!c.worker?'Start the worker to process queued videos.':'Studio ready. Review generated motion and character consistency before publishing.');
 $('#asset-list').innerHTML=data.assets.map(a=>`<article class="panel asset">${a.kind==='image'?`<img src="/media/${a.id}" alt="${esc(a.name)}">`:a.kind==='video'?`<video src="/media/${a.id}" controls preload="metadata"></video>`:`<audio src="/media/${a.id}" controls preload="metadata"></audio>`}<h3>${esc(a.name)}</h3><span class="tag">${a.kind.toUpperCase()}</span></article>`).join('')||'<p class="muted">Your uploaded assets will appear here.</p>';
 $('#job-list').innerHTML=data.jobs.map(j=>`<article class="panel job"><div class="section-title"><h2>${esc(data.projects.find(p=>p.id===j.project)?.title||j.project)}</h2><span class="tag">${esc(j.status.toUpperCase())}</span></div><p>${esc(j.phase)}</p><small>${new Date(j.created*1000).toLocaleString()} · ${esc(j.kind)}</small>${j.error?`<p class="error">${esc(j.error)}</p>`:''}<div class="actions">${['queued','waiting','running','review'].includes(j.status)?`<button data-job="${j.id}" data-action="cancel">Cancel</button>`:''}${j.status==='blocked'?`<button data-job="${j.id}" data-action="retry">Retry safe step</button>`:''}${j.status==='review'?`<button class="primary" data-job="${j.id}" data-action="approve">Approve & upload</button>`:''}<button data-open="${j.project}">Open project</button></div></article>`).join('')||'<div class="panel empty"><h2>Your queue is clear.</h2><p>Create a video to start production.</p></div>';
 $$('[data-job]').forEach(b=>b.onclick=guarded(async()=>{if(b.dataset.action==='approve'&&!confirm('Publish this video using your saved visibility and audience settings?'))return;await api(`/jobs/${b.dataset.job}/${b.dataset.action}`,'POST',{});await refresh();}));
 $$('[data-open]').forEach(b=>b.onclick=()=>{if(dirty&&!confirm('Discard unsaved edits?'))return;dirty=false;selected=b.dataset.open;editor(data.projects.find(p=>p.id===selected));showTab('studio');});
 $('#auto-status').textContent=data.settings.enabled?'Enabled':'Paused';
 $('#connection-cards').innerHTML=[['OpenAI','Scripts & narrator',c.openai],['Runway','Animated scenes',c.runway],['YouTube',c.channel.title||'Publishing',c.youtube],['Render worker',c.ffmpeg?'FFmpeg available':'Install FFmpeg',c.worker&&c.ffmpeg]].map(([n,d,ok])=>`<div class="panel"><span class="tag ${ok?'good':''}">${ok?'CONFIGURED':'SETUP REQUIRED'}</span><h3>${n}</h3><p>${esc(d)}</p></div>`).join('');
 $('#channel-name').textContent=c.channel.title?'Connected channel: '+c.channel.title:'No YouTube channel connected';$('#redirect-uri').textContent=data.redirect_uri;
}
function showTab(name){tabName=name;$$('.view').forEach(v=>v.hidden=v.id!==name);$$('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));$('#page-title').textContent={studio:'Your next story starts here.',library:'Everything your story needs.',queue:'From idea to finished video.',autopilot:'Keep your stories moving.',connections:'Connect your production tools.'}[name];
 if(name==='autopilot'){$('#template-select').innerHTML='<option value="">Choose a project</option>'+data.projects.map(p=>`<option value="${p.id}">${esc(p.title)}</option>`).join('');fillForm($('#auto-form'),data.settings);}
 if(name==='connections')fillForm($('#models-form'),data.settings);
}
async function refresh(){data=await api('/state');if(!selected&&data.projects.length)selected=data.projects[0].id;renderState();}
async function newProject(){if(dirty&&!confirm('Discard unsaved edits?'))return;const p=await api('/projects','POST',{});selected=p.id;dirty=false;await refresh();showTab('studio');}
$('#new-project').onclick=guarded(newProject);$('#empty-create').onclick=guarded(newProject);
$$('[data-tab]').forEach(b=>b.onclick=()=>{if(data)showTab(b.dataset.tab);});
$('#upload-form').onsubmit=guarded(async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{await api('/assets','POST',new FormData(e.target));e.target.reset();await refresh();toast('Asset uploaded');}finally{b.disabled=false;}});
$('#keys-form').onsubmit=guarded(async e=>{e.preventDefault();await api('/secrets','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();await refresh();toast('Credentials saved securely');});
$('#models-form').onsubmit=guarded(async e=>{e.preventDefault();await api('/settings','POST',Object.fromEntries(new FormData(e.target)));await refresh();toast('Defaults saved');});
$('#auto-form').onsubmit=guarded(async e=>{e.preventDefault();const v=Object.fromEntries(new FormData(e.target));v.enabled=e.target.elements.enabled.checked;v.audience=v.audience===''?null:v.audience==='true';v.synthetic=v.synthetic===''?null:v.synthetic==='true';if(v.enabled&&!confirm(v.mode==='autopilot'?'Enable recurring paid generation and automatic YouTube uploads? The first run starts in about one minute.':'Enable recurring paid video creation for your review?'))return;await api('/settings','POST',v);await refresh();toast(v.enabled?'Autopilot settings enabled':'Settings saved, autopilot paused');});
$('#pause-auto').onclick=guarded(async()=>{await api('/settings','POST',{enabled:false});await refresh();showTab('autopilot');toast('Autopilot paused');});
$('#disconnect-youtube').onclick=guarded(async()=>{await api('/youtube/disconnect','POST',{});await refresh();toast('YouTube disconnected');});
$('#logout').onclick=guarded(async()=>{await api('/logout','POST',{});location.href='/login';});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
refresh().catch(e=>toast(e.message));setInterval(()=>refresh().catch(()=>{}),7000);
