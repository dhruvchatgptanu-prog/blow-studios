'use strict';
/* Blox Studio owner UI. Vanilla JS (strict CSP: no inline scripts, no external code). */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const csrf = $('meta[name="csrf-token"]').content;
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const S = {view: 'dashboard', tz: 'Australia/Adelaide', videoId: null, tab: 'overview', qaVideo: null, plan: null,
           vocab: null, beat: 0, qaFilter: 'problems'};

async function api(path, method = 'GET', body) {
  const o = {method, headers: {'X-CSRF': csrf}};
  if (body instanceof FormData) o.body = body;
  else if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
  const r = await fetch(path.startsWith('/') ? path : '/api/' + path, o);
  if (r.status === 401) { location.href = '/login'; throw Error('Sign in again'); }
  let d = {};
  try { d = await r.json(); } catch (e) { d = {error: 'Unexpected response (' + r.status + ')'}; }
  if (!r.ok) throw Error(d.error || 'Request failed');
  return d;
}
function toast(t, bad) { const el = $('#toast'); el.textContent = t; el.style.borderColor = bad ? 'var(--bad)' : 'var(--accent)'; el.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => el.hidden = true, 7000); }
function guard(fn) { return async e => { try { if (e && e.preventDefault && e.type === 'submit') e.preventDefault(); await fn(e); } catch (x) { toast(x.message, true); } }; }
function when(ts, o) {
  if (!ts) return '—';
  const d = new Date(ts * 1000), opts = Object.assign({dateStyle: 'medium', timeStyle: 'short'}, o || {});
  Object.keys(opts).forEach(k => opts[k] === undefined && delete opts[k]);
  opts.timeZone = S.tz;
  let out = new Intl.DateTimeFormat('en-AU', opts).format(d);
  if (opts.timeStyle) {  // dateStyle/timeStyle cannot be combined with timeZoneName, so add the zone separately
    const z = new Intl.DateTimeFormat('en-AU', {timeZone: S.tz, timeZoneName: 'short', hour: 'numeric'}).formatToParts(d).find(x => x.type === 'timeZoneName');
    if (z) out += ' ' + z.value;
  }
  return out;
}
function ago(ts) { if (!ts) return 'never'; const s = Math.round(Date.now() / 1000 - ts); return s < 90 ? s + 's ago' : s < 5400 ? Math.round(s / 60) + 'm ago' : Math.round(s / 3600) + 'h ago'; }
const r2 = x => typeof x === 'number' ? Math.round(x * 100) / 100 : x;
function money(x) { return '$' + Number(x || 0).toFixed(2); }
const GOOD = ['approved', 'published', 'scheduled', 'processing_verified', 'succeeded', 'pass', 'done', 'uploaded'];
const BAD = ['failed', 'blocked', 'dead', 'fail', 'cancelled', 'needs_credentials'];
const WARN = ['needs_review', 'uncertain', 'skipped', 'repairing', 'waiting'];
function chip(s, label) { const c = GOOD.includes(s) ? 'good' : BAD.includes(s) ? 'bad' : WARN.includes(s) ? 'warn' : 'info'; return `<span class="chip ${c}">${esc(label || String(s).replace(/_/g, ' '))}</span>`; }
function media(rel) { return rel ? '/media/' + rel.split('/').map(encodeURIComponent).join('/') : ''; }
function empty(msg) { return `<div class="empty">${msg}</div>`; }
function setView(html) { $('#view').innerHTML = html; widths(); }
function widths() { $$('[data-w]').forEach(el => { el.style.width = Math.max(0, Math.min(100, Number(el.dataset.w) || 0)) + '%'; }); }
function bind(sel, ev, fn) { $$(sel).forEach(el => el.addEventListener(ev, guard(fn))); }

const TITLES = {dashboard: 'Dashboard', research: 'Trend research', productions: 'Director’s editor', stories: 'Story backlog', quality: 'Quality control',
  calendar: 'Publishing calendar', characters: 'Characters', library: 'Media library', queue: 'Work queue',
  budget: 'Budget & learning', connections: 'Connections', settings: 'Settings'};

async function show(view) {
  S.view = view; location.hash = view;
  $$('#nav button').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $('#title').textContent = TITLES[view]; $('#header-actions').innerHTML = '';
  try { await VIEWS[view](); } catch (e) { setView(`<div class="panel error">${esc(e.message)}</div>`); }
}

/* ------------------------------------------------------------------ dashboard */
async function dashboard() {
  const s = await api('state'); S.tz = s.timezone;
  const a = s.autopilot;
  const apState = a.emergency_stop ? 'Emergency stop' : !a.enabled ? 'Off' : a.paused ? 'Paused' : 'Running';
  const ready = Object.entries(s.readiness);
  const t = s.budget.today, lim = s.budget.limits;
  const used = t.total_counted, pctDay = Math.min(100, used / Math.max(0.01, lim.daily) * 100);
  const nx = s.next_slot;
  setView(`
  <div class="grid g4">
    <div class="panel"><h2>Autopilot</h2><div class="stat">${esc(apState)} <small>${a.enabled ? esc(a.mode === 'autopilot' ? 'auto-publish after QA' : 'owner review before publishing') : ''}</small></div>
      ${a.pause_reason ? `<p class="warn small">${esc(a.pause_reason)}</p>` : ''}
      <div class="actions">${a.emergency_stop ? '<button data-ap="clear_estop">Clear emergency stop</button>' :
        a.enabled ? (a.paused ? '<button class="primary" data-ap="resume">Resume</button>' : '<button data-ap="pause">Pause</button>') + '<button data-ap="disable" class="quiet">Turn off</button>' :
        '<button class="primary" data-goto="settings">Set up & enable…</button>'}</div></div>
    <div class="panel"><h2>Next publication slot</h2><div class="stat">${nx ? esc(when(nx.slot_at, {dateStyle: 'short'})) : '—'}</div>
      <p class="muted small">${nx ? (nx.status === 'not_created' ? 'Slots appear once the worker runs' : 'Status: ' + esc(nx.status)) : 'No upcoming slots'} · ${esc(s.timezone)}</p></div>
    <div class="panel"><h2>Approved buffer</h2><div class="stat">${s.buffer.approved_unscheduled} <small>/ target ${s.buffer.target}</small></div>
      <p class="muted small">${Object.entries(s.buffer.by_status).map(([k, v]) => esc(k.replace(/_/g, ' ')) + ' ' + v).join(' · ') || 'Nothing in production'}</p></div>
    <div class="panel"><h2>Budget today</h2><div class="stat">${money(used)} <small>/ ${money(lim.daily)}</small></div>
      <div class="bar"><i data-w="${pctDay}"></i></div>
      <p class="muted small">Reserved ${money(t.reserved)} · measured ${money(t.committed_measured)} · estimated ${money(t.committed_estimated)} · month ${money(s.budget.month_totals.total_counted)} / ${money(lim.monthly)}</p></div>
  </div>
  <div class="grid g2">
    <div class="panel"><h2>Provider readiness</h2><table>${ready.map(([k, r]) => `<tr><td>${r.ok ? '<span class="good">●</span>' : r.optional ? '<span class="muted">○</span>' : '<span class="error">●</span>'}</td><td>${esc(r.label)}${r.detail ? ` <span class="muted small">(${esc(r.detail)})</span>` : ''}</td></tr>`).join('')}</table>
      <div class="actions"><button data-goto="connections">Open connections</button></div></div>
    <div class="panel"><h2>Why publishing would not happen now</h2>${s.blockers.length ? '<ul>' + s.blockers.map(b => `<li>${esc(b)}</li>`).join('') + '</ul>' : '<p class="good">No blockers. Approved videos will be assigned to slots automatically.</p>'}
      <h3>Alerts</h3>${s.alerts.length ? s.alerts.map(x => `<p>${chip(x.kind)} ${x.video ? `<a href="#" data-video="${esc(x.video)}">${esc(x.title)}</a>` : ''} <span class="small">${esc(x.message)}</span></p>`).join('') : '<p class="muted">No alerts.</p>'}</div>
  </div>
  <div class="grid g2">
    <div class="panel"><h2>Current work</h2>${s.jobs.length ? `<div class="tablewrap"><table><tr><th>Task</th><th>Status</th><th>Updated</th><th>Note</th></tr>${s.jobs.map(j => `<tr><td>${esc(j.kind)}${j.video_id ? `<br><a href="#" data-video="${esc(j.video_id)}" class="small">${esc(j.video_id)}</a>` : ''}</td><td>${chip(j.status)}</td><td class="small">${ago(j.updated_at)}</td><td class="small muted">${esc((j.last_error || '').slice(0, 140))}</td></tr>`).join('')}</table></div>` : empty('No queued or running work.')}</div>
    <div class="panel"><h2>Quota & workers</h2><table>${Object.entries(s.quota).map(([b, q]) => `<tr><td>YouTube ${esc(b)} bucket</td><td>${q.used_today} / ${q.limit}</td></tr>`).join('')}</table>
      <p class="muted small">Quota resets at midnight Pacific Time. Limits are editable because Google changes them.</p>
      <h3>Workers</h3>${s.workers.length ? s.workers.map(w => `<p class="small">${chip(w.status)} ${esc(w.roles)} · heartbeat ${ago(w.heartbeat_at)}${w.current_task ? ' · running ' + esc(w.current_task) : ''}</p>`).join('') : '<p class="error">No worker heartbeat in the last 5 minutes. Start the worker service.</p>'}
      ${Object.keys(s.breakers).length ? '<h3>Circuit breakers</h3>' + Object.entries(s.breakers).map(([p, b]) => `<p class="small">${chip(b.state === 'closed' ? 'pass' : 'fail', p + ': ' + b.state)} ${esc((b.last_error || '').slice(0, 120))}</p>`).join('') : ''}</div>
  </div>`);
  bind('[data-ap]', 'click', async e => { await api('autopilot/' + e.target.dataset.ap, 'POST', {}); toast('Autopilot updated'); dashboard(); });
  bindCommon();
}

function bindCommon() {
  bind('[data-goto]', 'click', e => show(e.target.dataset.goto));
  bind('[data-video]', 'click', e => { e.preventDefault(); S.videoId = e.target.dataset.video; S.tab = 'overview'; show('productions'); });
}

/* ------------------------------------------------------------------ research */
async function research() {
  const r = await api('research');
  const em = (r.coverage.emerging || {}).topics || [];
  $('#header-actions').innerHTML = '<button class="primary" id="run-research">Run discovery now</button> <button id="run-snap">Refresh stats</button>';
  setView(`
  <p class="muted">${esc(r.disclaimer)} Planned run cost: ${r.cost.search_calls} search calls + about ${r.cost.shared_units_estimate} shared quota units.</p>
  <div class="grid g3">
    <div class="panel"><h2>Add a reference</h2><form id="ref-form"><label>YouTube video or channel link<input name="url" placeholder="https://www.youtube.com/shorts/… or @channel" required></label><button class="primary">Add</button></form>
      <p class="muted small">Links are parsed locally; nothing is fetched from the page itself. Channels join the watchlist.</p></div>
    <div class="panel"><h2>Watchlist</h2>${r.watchlist.length ? r.watchlist.map(w => `<p class="small">${esc(w.title || w.channel_id)} <button class="small quiet" data-unwatch="${esc(w.channel_id)}">remove</button></p>`).join('') : empty('No channels watched yet.')}</div>
    <div class="panel"><h2>Emerging in the sample</h2>${em.length ? em.slice(0, 10).map(t => `<span class="chip">${esc(t.topic)} · ${t.count_24h}×${t.lift ? ' · lift ' + t.lift : ' · new'}</span> `).join('') : empty('Run discovery to see topics.')}<p class="muted small">${esc((r.coverage.emerging || {}).note || '')}</p></div>
  </div>
  <div class="panel"><h2>Ranked candidates</h2>${r.candidates.length ? `<div class="tablewrap"><table><tr><th>#</th><th>Video</th><th>Published</th><th>Views</th><th>Score</th><th>Velocity</th><th>Short?</th><th>Transcript / media</th></tr>
    ${r.candidates.map(c => { const last = c.snapshots[c.snapshots.length - 1] || {}; return `<tr class="click" data-cand="${esc(c.video_id)}"><td>${c.rank.rank || ''}</td>
      <td><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)}</a><br><span class="muted small">${esc(c.channel_title)} · retrieved ${ago(last.retrieved_at)}</span></td>
      <td class="small">${when(c.published_at, {dateStyle: 'short'})}</td><td>${last.views ?? '—'}</td><td>${(c.score ?? 0).toFixed(3)}<br><span class="small muted">conf ${c.rank.confidence ?? ''}</span></td>
      <td>${chip(c.rank.velocity_kind === 'measured' ? 'pass' : 'uncertain', c.rank.velocity_kind || '')}</td><td>${chip(c.shorts.label === 'likely_short' ? 'pass' : 'uncertain', (c.shorts.label || '').replace('_', ' ') + ' ' + (c.shorts.confidence ?? ''))}</td>
      <td class="small">${esc(c.transcript_status)} / ${esc(c.media_status)}</td></tr>`; }).join('')}</table></div>` : empty('No candidates yet. Add a YouTube Data API key in Connections, then run discovery.')}</div>
  <div id="cand-detail"></div>
  <div class="panel"><h2>Coverage of recent runs</h2>${r.coverage.runs.length ? `<table><tr><th>Run</th><th>Status</th><th>Started</th><th>Quota</th><th>Coverage</th></tr>${r.coverage.runs.map(x => `<tr><td>${esc(x.kind)}</td><td>${chip(x.status)}</td><td class="small">${when(x.started_at)}</td><td>${x.quota_used}</td><td class="small">${esc(x.error || '')} ${x.coverage.videos_seen !== undefined ? x.coverage.videos_seen + ' videos · ' + (x.coverage.searches || []).length + ' searches · ' + (x.coverage.watchlist || []).length + ' watched channels' : esc(JSON.stringify(x.coverage).slice(0, 160))}</td></tr>`).join('')}</table>` : empty('No research runs yet.')}</div>`);
  bind('#run-research', 'click', async () => { await api('research/run', 'POST', {}); toast('Discovery queued for the research worker'); });
  bind('#run-snap', 'click', async () => { await api('research/snapshot', 'POST', {}); toast('Stats refresh queued'); });
  bind('#ref-form', 'submit', async e => { const v = await api('research/reference', 'POST', {url: e.target.url.value}); toast('Added ' + v.kind + ' ' + v.id); research(); });
  bind('[data-unwatch]', 'click', async e => { await api('research/unwatch', 'POST', {channel_id: e.target.dataset.unwatch}); research(); });
  bind('[data-cand]', 'click', async e => { if (e.target.tagName === 'A') return; await candidate(e.currentTarget.dataset.cand); });
}

async function candidate(id) {
  const v = await api('research/video/' + encodeURIComponent(id));
  const ex = (v.rank || {}).explanation || [];
  $('#cand-detail').innerHTML = `<div class="panel"><h2>${esc(v.title)}</h2>
    <div class="grid g2"><div><h3>Ranking explanation</h3><table><tr><th>Component</th><th>Value</th><th>Weight</th><th>Contribution</th><th>Why</th></tr>
      ${ex.map(c => `<tr><td>${esc(c.component)}</td><td>${c.value ?? '—'}</td><td>${c.weight}</td><td>${c.contribution}</td><td class="small">${esc(c.why)}</td></tr>`).join('')}</table>
      <p class="small muted">${esc(((v.rank || {}).confidence_notes || []).join('; '))}</p>
      <h3>Shorts signals</h3><p class="small">${esc((v.shorts.reasons || []).join(' · '))}</p>
      ${(v.shorts.injection_flags || []).length ? `<p class="warn small">Instruction-like text found in metadata (treated as data only): ${esc(v.shorts.injection_flags.join(', '))}</p>` : ''}
      <h3>Metric snapshots</h3><table><tr><th>Retrieved</th><th>Views</th><th>Likes</th><th>Comments</th></tr>${v.snapshots.map(s => `<tr><td class="small">${when(s.retrieved_at)}</td><td>${s.views ?? '—'}</td><td>${s.likes ?? 'hidden'}</td><td>${s.comments ?? '—'}</td></tr>`).join('')}</table></div>
    <div><h3>Transcript</h3>${v.transcript ? `<p>${chip(v.transcript.status)} ${esc(v.transcript.provenance)} · timing ${esc(v.transcript.timing)}</p><pre>${esc((v.transcript.text || '').slice(0, 2000))}</pre>` : '<p class="muted">No transcript. Blox does not download other creators’ captions.</p>'}
      <form id="tr-form"><label>Upload a transcript you may use (.srt/.vtt text or plain text)<textarea name="text" placeholder="Paste caption text"></textarea></label>
      <label class="check"><input type="checkbox" name="rights"> I have the right to use this transcript</label><button>Save transcript</button></form>
      <h3>Analysis</h3>${v.analysis ? `<p>${chip(v.analysis.method === 'metadata_only' ? 'uncertain' : 'pass', v.analysis.method)}</p><pre>${esc(JSON.stringify(v.analysis.findings, null, 1).slice(0, 3000))}</pre>` : '<p class="muted">Not analysed.</p>'}
      <div class="actions"><button id="analyze">Analyze (transcript provider + metadata)</button></div></div></div></div>`;
  bind('#tr-form', 'submit', async e => { await api('research/transcript', 'POST', {video_id: id, text: e.target.text.value, rights: e.target.rights.checked}); toast('Transcript saved'); candidate(id); });
  bind('#analyze', 'click', async () => { await api('research/analyze', 'POST', {video_id: id}); toast('Analysis queued'); });
}

/* ------------------------------------------------------------------ productions */
async function productions() {
  const list = await api('videos');
  if (!S.videoId && list.videos.length) S.videoId = list.videos[0].id;
  $('#header-actions').innerHTML = '<button id="new-demo">New from demo story</button> <button class="primary" id="new-auto">New researched video</button>';
  setView(`<div class="grid split-list"><div class="panel"><h2>Videos</h2>${list.videos.length ? list.videos.map(v => `<button class="list-item ${v.id === S.videoId ? 'sel' : ''}" data-pick="${esc(v.id)}"><b>${esc(v.title)}</b><br>${chip(v.status)} <span class="small muted">${esc(v.origin)} · ${ago(v.updated_at)}</span></button>`).join('') : empty('No videos yet.')}</div><div id="vdetail">${S.videoId ? '<p class="muted">Loading…</p>' : empty('Create a video to start.')}</div></div>`);
  bind('[data-pick]', 'click', e => { S.videoId = e.currentTarget.dataset.pick; S.tab = 'overview'; S.plan = null; productions(); });
  bind('#new-demo', 'click', async () => { const r = await api('videos', 'POST', {kind: 'demo'}); S.videoId = r.id; S.plan = null; toast('Demo story created (hand-authored, not AI)'); productions(); });
  bind('#new-auto', 'click', async () => { if (!confirm('Start a researched, AI-written video? This uses your connected providers and budget.')) return; const r = await api('videos', 'POST', {kind: 'autopilot_style'}); S.videoId = r.id; productions(); });
  if (S.videoId) await videoDetail();
}

const TABS = ['overview', 'script', 'timeline', 'editor', 'shots', 'voices', 'originality', 'history'];
async function videoDetail() {
  const d = await api('videos/' + S.videoId); S.detail = d;
  if (!S.vocab) S.vocab = (await api('vocabulary')).schema;
  if (!S.plan && d.video.metadata.plan) S.plan = JSON.parse(JSON.stringify(d.video.metadata.plan));
  const v = d.video;
  $('#vdetail').innerHTML = `<div class="panel"><div class="titlebar"><div><h2>${esc(v.title)}</h2>${chip(v.status)} <span class="small muted">${esc(v.status_reason)}</span></div>
    <div class="actions">${actionButtons(v)}</div></div>
    <div class="tabs">${TABS.map(t => `<button data-tab="${t}" class="${S.tab === t ? 'active' : ''}">${t}</button>`).join('')}</div><div id="tab"></div></div>`;
  bind('[data-tab]', 'click', e => { S.tab = e.target.dataset.tab; videoDetail(); });
  bind('[data-act]', 'click', async e => {
    const act = e.target.dataset.act;
    if (act === 'cancel' && !confirm('Cancel this video? Provider work already submitted may still finish and be charged.')) return;
    const r = await api(`videos/${S.videoId}/${act}`, 'POST', {});
    if (r.id) { S.videoId = r.id; S.plan = null; }
    toast('Done: ' + act); productions();
  });
  TAB_RENDER[S.tab](d);
}
function actionButtons(v) {
  const b = [];
  if (['scripted', 'concept_selected', 'discovered', 'researched'].includes(v.status)) b.push('<button class="primary" data-act="produce">Produce</button>');
  if (['blocked', 'needs_review', 'needs_credentials'].includes(v.status)) b.push('<button class="primary" data-act="resume">Resume</button>');
  if (v.status === 'approved') b.push(v.metadata.owner_approved ? '<button data-act="unapprove">Withdraw approval</button>' : '<button class="primary" data-act="approve">Approve for publishing</button>');
  if (v.metadata.plan) b.push('<button data-act="duplicate">Duplicate</button>');
  if (!['published', 'cancelled', 'failed'].includes(v.status)) b.push('<button class="danger" data-act="cancel">Cancel</button>');
  if (v.youtube_url) b.push(`<a class="button" href="${esc(v.youtube_url)}" target="_blank" rel="noopener noreferrer">Open on YouTube</a>`);
  return b.join('');
}
const TAB_RENDER = {
  overview(d) {
    const v = d.video, est = v.metadata.cost_estimate;
    $('#tab').innerHTML = `<div class="grid g2"><div><dl class="kv"><dt>Origin</dt><dd>${esc(v.origin)}</dd><dt>Created</dt><dd>${when(v.created_at)}</dd>
      <dt>Manifest</dt><dd>${d.manifest ? 'v' + d.manifests.length + ' · ' + esc(d.manifest.source) : '—'}</dd>
      <dt>Validation</dt><dd>${d.manifest ? (d.manifest.validation.ok ? '<span class="good">valid</span>' : `<span class="error">${(d.manifest.validation.errors || []).length} errors</span>`) : '—'}</dd>
      <dt>Slot</dt><dd>${d.slot ? when(d.slot.slot_at) + ' ' + chip(d.slot.status) : '—'}</dd>
      <dt>YouTube</dt><dd>${v.youtube_video_id ? esc(v.youtube_video_id) : '—'}</dd>
      <dt>Upload</dt><dd>${d.upload ? chip(d.upload.status) + ` ${d.upload.bytes_confirmed}/${d.upload.bytes_total || '?'} bytes` : '—'}</dd>
      <dt>Review mode</dt><dd>${v.metadata.owner_approved ? '<span class="good">owner approved</span>' : 'not approved'}</dd></dl>
      ${d.render ? `<h3>Latest render</h3><video class="player" controls preload="metadata" src="${media(d.render.file)}"></video><div class="actions"><a class="button" download href="${media(d.render.file)}">Download MP4</a>${d.render.thumbnail_file ? `<a class="button" href="${media(d.render.thumbnail_file)}" target="_blank">Thumbnail</a>` : ''}</div>` : ''}</div>
      <div><h3>Cost</h3><p>Spent ${money(d.spend.total)}${est ? ` · estimated ${money(est.total)}` : ''}</p>
      ${est ? `<table>${Object.entries(est).map(([k, x]) => `<tr><td>${esc(k)}</td><td>${money(x)}</td></tr>`).join('')}</table>` : ''}
      ${d.pending_paid_calls.length ? `<h3>Paid requests awaiting reconciliation</h3>${d.pending_paid_calls.map(p => `<div class="panel"><p>${esc(p.provider)} · ${esc(p.operation)} · ${chip(p.status)}</p><p class="small">${esc(p.error)}</p>
        <div class="actions"><button data-resolve="${esc(p.idempotency_key)}" data-outcome="not_submitted">Not submitted (retry)</button><button data-resolve="${esc(p.idempotency_key)}" data-outcome="charged_discard">Charged — discard</button></div></div>`).join('')}` : ''}
      ${d.qa ? `<h3>Latest QA</h3><p>${chip(d.qa.verdict)} ${Object.entries(d.qa.summary).map(([k, x]) => esc(k) + ' ' + x).join(' · ')}</p><button data-goto-qa>Open quality control</button>` : ''}</div></div>`;
    bind('[data-resolve]', 'click', async e => { await api(`videos/${S.videoId}/resolve_paid`, 'POST', {key: e.target.dataset.resolve, outcome: e.target.dataset.outcome}); toast('Recorded'); videoDetail(); });
    bind('[data-goto-qa]', 'click', () => { S.qaVideo = S.videoId; show('quality'); });
  },
  script(d) { $('#tab').innerHTML = d.manifest ? `<pre>${esc(d.manifest.director_script)}</pre>` : empty('No script yet.'); },
  timeline(d) {
    if (!d.manifest) { $('#tab').innerHTML = empty('No manifest yet.'); return; }
    const m = d.manifest.body, b = m.beats[Math.min(S.beat, m.beats.length - 1)];
    $('#tab').innerHTML = `<p class="muted small">${m.beats.length} beats · ${m.fps} fps · frame indices are authoritative. Click a beat.</p>
      <div class="beatgrid">${m.beats.map((x, i) => `<div class="beat p-${esc(x.purpose)} ${i === S.beat ? 'sel' : ''}" data-beat="${i}" title="${esc(x.purpose)}">${esc(x.start_tc.slice(3, 5))}<br>${esc(x.purpose.slice(0, 4))}</div>`).join('')}</div>
      <div class="panel mt"><h2>${esc(b.start_tc)}–${esc(b.end_tc)} · frames ${b.start_frame}–${b.end_frame} · ${esc(b.purpose)} · shot ${esc(b.shot)}</h2>
      <p>${esc(b.description)}</p>
      <div class="grid g2">${b.characters.map(c => `<div><h3>${esc(c.id)} · ${esc(c.expression_label)}</h3><table>
        <tr><th></th><th>start</th><th>end</th></tr>
        ${['expression', 'posture'].map(k => `<tr><td>${k}</td><td>${esc(c.start[k])}</td><td>${esc(c.end[k])}</td></tr>`).join('')}
        <tr><td>position</td><td>${esc(c.start.position.map(r2).join(', '))}</td><td>${esc(c.end.position.map(r2).join(', '))}</td></tr>
        <tr><td>facing°</td><td>${r2(c.start.facing)}</td><td>${r2(c.end.facing)}</td></tr>
        <tr><td>head y/p/r</td><td>${[c.start.head.yaw, c.start.head.pitch, c.start.head.roll].map(r2).join(' / ')}</td><td>${[c.end.head.yaw, c.end.head.pitch, c.end.head.roll].map(r2).join(' / ')}</td></tr>
        <tr><td>brows in/out</td><td>${r2(c.start.brows.inner)} / ${r2(c.start.brows.outer)}</td><td>${r2(c.end.brows.inner)} / ${r2(c.end.brows.outer)}</td></tr>
        <tr><td>eyes open</td><td>${r2(c.start.eyes.open)}</td><td>${r2(c.end.eyes.open)}</td></tr>
        <tr><td>mouth</td><td>${esc(c.start.mouth.shape)} ${r2(c.start.mouth.open)}</td><td>${esc(c.end.mouth.shape)} ${r2(c.end.mouth.open)}</td></tr>
        <tr><td>arms L/R</td><td>${esc(c.start.arms.left)} / ${esc(c.start.arms.right)}</td><td>${esc(c.end.arms.left)} / ${esc(c.end.arms.right)}</td></tr>
        <tr><td>feet</td><td>${esc(c.start.feet.stance)} ${esc(c.start.feet.weight)}</td><td>${esc(c.end.feet.stance)} ${esc(c.end.feet.weight)}</td></tr>
        <tr><td>eye-line</td><td colspan="2">${esc(JSON.stringify(c.end.eye_target))}</td></tr></table>
        ${c.actions.map(a => `<p class="small">${chip('info', a.type)} ${a.phases.map(p => esc(p.phase) + ' ' + p.start_frame + '–' + p.end_frame).join(' · ')}</p>`).join('')}
        ${c.blinks.length ? `<p class="small muted">blinks: ${c.blinks.join(', ')}</p>` : ''}</div>`).join('')}</div>
      ${b.camera ? `<h3>Camera</h3><p>${esc(b.camera.start.framing)} → ${esc(b.camera.end.framing)} · ${esc(b.camera.move)} · ${esc(b.camera.start.angle)} · ${esc(b.camera.start.side)} of ${esc(b.camera.start.subject)} · ${esc(b.camera.renderer)}</p>` : ''}
      <h3>Dialogue & sound</h3><p>${b.vocal.map(x => `${esc(x.speaker)} (${esc(x.emotion)}, ${esc(x.pace)}): “${esc((m.lines.find(l => l.id === x.line) || {}).text)}”`).join('<br>') || '—'}</p>
      <p class="small">SFX: ${esc(b.sfx.join(', ') || '—')} · Music: ${esc(b.music || '—')} · Captions: ${esc(b.captions.map(c => (m.captions.find(x => x.id === c) || {}).text).join(' | ') || '—')}</p>
      <h3>Continuity & QA expectations</h3><p class="small">${esc(b.continuity.join('; ') || '—')}</p><p class="small">${esc(b.qa.map(q => q.check + (q.character ? ':' + q.character : '')).join(', ') || '—')}</p></div>`;
    bind('[data-beat]', 'click', e => { S.beat = Number(e.currentTarget.dataset.beat); TAB_RENDER.timeline(d); });
  },
  editor(d) { renderEditor(d); },
  shots(d) {
    $('#tab').innerHTML = d.shots.length ? `<div class="thumbs">${d.shots.map(s => `<figure>${s.preview_file ? `<img src="${media(s.preview_file)}" alt="Shot ${esc(s.shot_key)} preview">` : '<div class="empty">no preview</div>'}
      <figcaption><b>${esc(s.shot_key)}</b> ${chip(s.status)}<br>${esc(s.renderer)} · ${s.native_w ? s.native_w + '×' + s.native_h : ''} · tries ${s.attempts} · repairs ${s.repair_attempts}
      ${(s.detail || {}).upscaled ? '<br><span class="warn">upscaled from provider resolution</span>' : ''}
      ${s.file ? `<br><a href="${media(s.file)}" target="_blank">clip</a>` : ''}<br><button class="small" data-regen="${esc(s.shot_key)}">Regenerate shot</button></figcaption></figure>`).join('')}</div>` : empty('Shots appear after storyboarding.');
    bind('[data-regen]', 'click', async e => { if (!confirm('Re-render this shot? Generative shots use paid credits.')) return; await api(`videos/${S.videoId}/regenerate_shot`, 'POST', {shot: e.target.dataset.regen}); toast('Shot queued'); videoDetail(); });
  },
  voices(d) {
    $('#tab').innerHTML = d.lines.length ? `<table><tr><th>Line</th><th>Speaker</th><th>Text</th><th>Audio</th><th>Alignment</th><th></th></tr>${d.lines.map(l => `<tr><td>${esc(l.line_key)}</td><td>${esc(l.speaker)}</td><td>${esc(l.text)}${l.spoken_text !== l.text ? `<br><span class="small muted">spoken as: ${esc(l.spoken_text)}</span>` : ''}</td>
      <td>${l.file ? `<audio controls preload="none" src="${media(l.file)}"></audio><br><span class="small">${(l.duration_s || 0).toFixed(2)}s · ${esc((l.alignment || {}).provider || '')}${(l.alignment || {}).test_voice ? ' <span class="error">test voice</span>' : ''}</span>` : chip(l.status)}</td>
      <td class="small">${esc(l.alignment_kind)}${(l.alignment || {}).tempo ? '<br>tempo ×' + l.alignment.tempo : ''}</td><td><button class="small" data-revoice="${esc(l.line_key)}">Re-voice</button></td></tr>`).join('')}</table>` : empty('Lines appear after storyboarding.');
    bind('[data-revoice]', 'click', async e => { if (!confirm('Generate this line again? Paid voice providers charge per request.')) return; await api(`videos/${S.videoId}/revoice_line`, 'POST', {line: e.target.dataset.revoice}); toast('Line queued'); videoDetail(); });
  },
  originality(d) {
    const m = d.video.metadata;
    $('#tab').innerHTML = `${d.concept ? `<h3>Concept</h3><p><b>${esc(d.concept.premise)}</b></p><p>Hook: ${esc(d.concept.hook)}<br>Ending: ${esc(d.concept.ending)}</p><h3>Inspiration record</h3><pre>${esc(JSON.stringify(JSON.parse(d.concept.inspiration || '{}'), null, 1))}</pre>` : '<p class="muted">No AI concept (manual, demo or template story).</p>'}
      ${['originality_concept', 'originality_script'].map(k => m[k] ? `<h3>${k.replace('_', ' ')} · decision ${chip(m[k].decision === 'pass' ? 'pass' : 'uncertain', m[k].decision)}</h3><table><tr><th>Check</th><th>Value</th><th>Threshold</th><th>Status</th><th>Detail</th></tr>${m[k].checks.map(c => `<tr><td>${esc(c.check)}</td><td>${c.value ?? '—'}</td><td>${c.threshold}</td><td>${chip(c.status)}</td><td class="small">${esc(c.detail)}</td></tr>`).join('')}</table><p class="small muted">${esc(m[k].disclaimer)} Method: ${esc(m[k].method)}</p>` : '').join('')}
      ${m.brief ? `<h3>Research brief</h3><p class="small">${esc(m.brief.note)}</p>${(m.brief.sources || []).map(s => `<p class="small">${esc(s.id)} · ${esc(s.title)} · evidence: ${esc(s.evidence)}</p>`).join('')}` : ''}`;
  },
  history(d) {
    $('#tab').innerHTML = `<h3>State history</h3><table>${d.events.map(e => `<tr><td class="small">${when(e.at)}</td><td>${esc(e.from_status || '')} → ${chip(e.to_status)}</td><td class="small">${esc(e.actor)}</td><td class="small">${esc(e.note)}</td></tr>`).join('')}</table>
      <h3>Tasks</h3><table>${d.tasks.map(t => `<tr><td>${esc(t.kind)}</td><td>${chip(t.status)}</td><td>${t.attempts}</td><td class="small">${esc((t.last_error || '').slice(0, 200))}</td></tr>`).join('')}</table>
      <h3>Repairs</h3>${d.repairs.length ? `<table>${d.repairs.map(r => `<tr><td class="small">${when(r.created_at)}</td><td>${esc(r.target)}</td><td>${esc(r.action)}</td><td>attempt ${r.attempt}</td><td class="small">${esc(JSON.stringify(r.detail))}</td></tr>`).join('')}</table>` : '<p class="muted">No repairs.</p>'}`;
  },
};

/* --------------------------- plan editor with a beat inspector */
function opts(list, cur, allowEmpty) { return (allowEmpty ? '<option value="">(unchanged)</option>' : '') + list.map(x => `<option ${x === cur ? 'selected' : ''}>${esc(x)}</option>`).join(''); }
function renderEditor(d) {
  if (!S.plan) { $('#tab').innerHTML = empty('This video has no editable plan (it was imported or is still being written). Duplicate a demo video to experiment.'); return; }
  const V = S.vocab, P = S.plan, cast = (P.cast || []).map(c => c.id);
  $('#tab').innerHTML = `<div class="grid g2"><div>
    <h3>Pose key (performance)</h3><form id="key-form"><div class="row"><label>Character<select name="character">${opts(cast)}</select></label><label>Time (s)<input name="t" type="number" step="0.033" value="0"></label></div>
    <div class="row"><label>Expression<select name="expression">${opts(V.EXPRESSIONS, '', true)}</select></label><label>Posture<select name="posture">${opts(V.POSTURES, '', true)}</select></label></div>
    <div class="row"><label>Left arm<select name="arm_left">${opts(V.ARM_POSES, '', true)}</select></label><label>Right arm<select name="arm_right">${opts(V.ARM_POSES, '', true)}</select></label></div>
    <div class="row"><label>Head yaw°<input name="yaw" type="number" min="-70" max="70" step="1"></label><label>Head pitch°<input name="pitch" type="number" min="-40" max="40"></label><label>Head roll°<input name="roll" type="number" min="-30" max="30"></label></div>
    <div class="row"><label>Inner brows<input name="inner" type="number" min="-1" max="1" step="0.05"></label><label>Outer brows<input name="outer" type="number" min="-1" max="1" step="0.05"></label><label>Eyes open<input name="eyes" type="number" min="0" max="1.35" step="0.05"></label></div>
    <div class="row"><label>Mouth shape<select name="mouth">${opts(V.MOUTH_SHAPES, '', true)}</select></label><label>Mouth open<input name="mouth_open" type="number" min="0" max="1" step="0.05"></label><label>Weight<select name="weight">${opts(V.WEIGHTS, '', true)}</select></label></div>
    <div class="row"><label>Eye-line<select name="eye_kind"><option value="">(unchanged)</option><option>camera</option><option>character</option><option>prop</option><option>forward</option></select></label><label>Target id<input name="eye_id"></label></div>
    <label>Director note<input name="note"></label><button>Set key</button></form>
    <h3>Action</h3><form id="act-form"><div class="row"><label>Character<select name="character">${opts(cast)}</select></label><label>Action<select name="type">${opts(Object.keys(V.ACTIONS))}</select></label><label>Start (s)<input name="t" type="number" step="0.033" value="0"></label></div>
    <div class="row"><label>Anticipation fr<input name="a" type="number" value="6"></label><label>Main fr<input name="m" type="number" value="14"></label><label>Follow fr<input name="f" type="number" value="6"></label><label>Hold fr<input name="h" type="number" value="4"></label></div>
    <div class="row"><label>Hand<select name="hand"><option value="">—</option><option>left</option><option>right</option></select></label><label>Prop<input name="prop"></label><label>To x,y<input name="to" placeholder="3.0,0.4"></label><label>To facing°<input name="to_facing" type="number"></label></div>
    <button>Add action</button></form>
    <h3>Lines</h3><table>${(P.lines || []).map((l, i) => `<tr><td>${esc(l.id)}</td><td><input data-line="${i}" data-k="text" value="${esc(l.text)}"></td><td><input data-line="${i}" data-k="t" type="number" step="0.05" value="${l.t}" class="narrow"></td><td><select data-line="${i}" data-k="emotion">${opts(V.EMOTIONS, l.emotion)}</select></td></tr>`).join('')}</table>
  </div><div>
    <h3>Plan JSON (authoring format; seconds convert to frames on save)</h3><textarea id="plan-json" class="tall">${esc(JSON.stringify(P, null, 1))}</textarea>
    <div class="actions"><button id="apply-json">Apply JSON</button><button id="validate">Validate</button><button class="primary" id="save-plan">Save as new manifest version</button></div>
    <div id="val-out"></div></div></div>`;
  const num = v => v === '' ? undefined : Number(v);
  bind('#key-form', 'submit', e => {
    const f = e.target, k = {t: Number(f.t.value)};
    if (f.expression.value) k.expression = f.expression.value;
    if (f.posture.value) k.posture = f.posture.value;
    if (f.arm_left.value || f.arm_right.value) k.arms = Object.assign({}, f.arm_left.value ? {left: f.arm_left.value} : {}, f.arm_right.value ? {right: f.arm_right.value} : {});
    const head = {yaw: num(f.yaw.value), pitch: num(f.pitch.value), roll: num(f.roll.value)}; if (Object.values(head).some(x => x !== undefined)) k.head = JSON.parse(JSON.stringify(head));
    const brows = {inner: num(f.inner.value), outer: num(f.outer.value)}; if (Object.values(brows).some(x => x !== undefined)) k.brows = JSON.parse(JSON.stringify(brows));
    if (f.eyes.value) k.eyes = {open: Number(f.eyes.value)};
    if (f.mouth.value || f.mouth_open.value) k.mouth = Object.assign({}, f.mouth.value ? {shape: f.mouth.value} : {}, f.mouth_open.value ? {open: Number(f.mouth_open.value)} : {});
    if (f.weight.value) k.feet = {weight: f.weight.value};
    if (f.eye_kind.value) k.eye_target = Object.assign({kind: f.eye_kind.value}, f.eye_id.value ? {id: f.eye_id.value} : {});
    if (f.note.value) k.note = f.note.value;
    const list = (P.performance[f.character.value] = P.performance[f.character.value] || []);
    const i = list.findIndex(x => Math.abs(x.t - k.t) < 0.001);
    if (i >= 0) Object.assign(list[i], k); else { list.push(k); list.sort((a, b) => a.t - b.t); }
    toast('Key set at ' + k.t + 's (validate, then save)'); renderEditor(d);
  });
  bind('#act-form', 'submit', e => {
    const f = e.target, params = {};
    if (f.hand.value) params.hand = f.hand.value; if (f.prop.value) params.prop = f.prop.value;
    if (f.to.value) params.to = f.to.value.split(',').map(Number); if (f.to_facing.value) params.to_facing = Number(f.to_facing.value);
    (P.actions = P.actions || []).push({character: f.character.value, type: f.type.value, t: Number(f.t.value), anticipation_frames: Number(f.a.value), main_frames: Number(f.m.value), follow_through_frames: Number(f.f.value), hold_frames: Number(f.h.value), params});
    toast('Action added'); renderEditor(d);
  });
  $$('[data-line]').forEach(el => el.addEventListener('change', () => { const l = P.lines[Number(el.dataset.line)]; l[el.dataset.k] = el.dataset.k === 't' ? Number(el.value) : el.value; }));
  bind('#apply-json', 'click', () => { S.plan = JSON.parse($('#plan-json').value); toast('JSON applied'); renderEditor(d); });
  const showVal = r => { const v = r.validation; $('#val-out').innerHTML = `<p>${v.ok ? '<span class="good">Valid</span>' : `<span class="error">${v.errors.length} error(s)</span>`} · ${v.warnings.length} warning(s)</p>${v.errors.map(x => `<p class="small error">${esc(x.message)}${x.fix ? ' — ' + esc(x.fix) : ''}</p>`).join('')}${v.warnings.map(x => `<p class="small warn">${esc(x.message)}</p>`).join('')}`; };
  bind('#validate', 'click', async () => showVal(await api('validate-plan', 'POST', {plan: S.plan})));
  bind('#save-plan', 'click', async () => { const r = await api(`videos/${S.videoId}/plan`, 'PUT', {plan: S.plan}); showVal(r); toast(r.validation.ok ? 'Saved; ready to produce' : 'Saved with errors (held for review)'); });
}

/* ------------------------------------------------------------------ story backlog */
async function stories() {
  const b = await api('backlog');
  const s = b.summary;
  setView(`<div class="grid g3"><div class="panel"><h2>Ready</h2><div class="stat">${s.ready} <small>stories</small></div><p class="small muted">about ${s.days_left} days at ${s.per_day} per day</p></div>
    <div class="panel"><h2>Used</h2><div class="stat">${s.used}</div><p class="small muted">${s.rejected} rejected</p></div>
    <div class="panel"><h2>Story source</h2><div class="stat">${esc(b.source === 'backlog' ? 'Backlog' : 'LLM API')}</div><p class="small muted">Setting: ${esc(b.setting)} (Settings → Production)</p></div></div>
    <div class="panel"><p class="small">${esc(b.note)}</p>
      <form id="import"><label>Paste story plans (a JSON list of plans, or {"plans": [...]}); the format is the plan JSON in the Director’s editor<textarea name="json" placeholder='[{"title": "…", "cast": […], "shots": […], …}]'></textarea></label>
      <div class="actions"><button class="primary">Validate and add</button></div></form><div id="import-out"></div></div>
    <div class="panel tablewrap">${b.stories.length ? `<table><tr><th>Story</th><th>Status</th><th>Source</th><th>Added</th><th>Notes</th><th></th></tr>${b.stories.map(x => `<tr><td><b>${esc(x.title)}</b><br><span class="small muted">${esc(x.logline)}</span></td>
      <td>${chip(x.status === 'ready' ? 'pass' : x.status === 'used' ? 'info' : 'fail', x.status)}${x.video_id ? `<br><a href="#" data-video="${esc(x.video_id)}" class="small">video</a>` : ''}</td><td class="small">${esc(x.source)}</td><td class="small">${when(x.created_at, {timeStyle: undefined})}</td>
      <td class="small muted">${esc(x.note || (x.validation.warnings || []).slice(0, 2).join('; '))}</td>
      <td>${x.status === 'ready' ? `<button class="small" data-story="${esc(x.id)}" data-sact="reject">Reject</button>` : x.status === 'rejected' ? `<button class="small" data-story="${esc(x.id)}" data-sact="restore">Restore</button>` : ''}</td></tr>`).join('')}</table>` : empty('No stories yet. Paste plans above, or ask Claude for a batch written in the plan format.')}</div>`);
  bind('#import', 'submit', async e => {
    let data;
    try { data = JSON.parse(e.target.json.value); } catch (x) { throw Error('That is not valid JSON'); }
    const plans = Array.isArray(data) ? data : (data.plans || [data]);
    const r = await api('backlog', 'POST', {plans, source: 'owner'});
    $('#import-out').innerHTML = r.results.map(x => `<p class="small">${chip(x.status === 'ready' ? 'pass' : x.status === 'duplicate' ? 'uncertain' : 'fail', x.status)} ${esc(x.title)} ${x.reason ? '<span class="muted">' + esc(x.reason) + '</span>' : ''}</p>`).join('');
    toast(r.results.filter(x => x.status === 'ready').length + ' stories added');
    setTimeout(stories, 2500);
  });
  bind('[data-story]', 'click', async e => { await api(`backlog/${e.target.dataset.story}/${e.target.dataset.sact}`, 'POST', {}); stories(); });
  bindCommon();
}

/* ------------------------------------------------------------------ quality */
async function quality() {
  const list = (await api('videos')).videos.filter(v => v.render_id);
  if (!S.qaVideo && list.length) S.qaVideo = list[0].id;
  setView(`<div class="row narrow-row"><label>Video<select id="qa-pick">${list.map(v => `<option value="${esc(v.id)}" ${v.id === S.qaVideo ? 'selected' : ''}>${esc(v.title)} (${esc(v.status)})</option>`).join('')}</select></label>
    <label>Show<select id="qa-filter"><option value="problems" ${S.qaFilter === 'problems' ? 'selected' : ''}>Problems only</option><option value="all" ${S.qaFilter === 'all' ? 'selected' : ''}>All checks</option></select></label></div><div id="qa-body">${list.length ? '' : empty('No rendered videos yet.')}</div>`);
  bind('#qa-pick', 'change', e => { S.qaVideo = e.target.value; quality(); });
  bind('#qa-filter', 'change', e => { S.qaFilter = e.target.value; quality(); });
  if (!S.qaVideo) return;
  const d = await api('videos/' + S.qaVideo);
  const q = d.qa;
  const checks = q ? q.checks.checks.filter(c => S.qaFilter === 'all' || c.status !== 'pass') : [];
  $('#qa-body').innerHTML = `<div class="grid split-qa"><div class="panel">${d.render ? `<video id="qa-player" class="player" controls preload="metadata" src="${media(d.render.file)}"></video>` : ''}
      <p>${q ? chip(q.verdict) + ' ' + Object.entries(q.summary).map(([k, x]) => esc(k) + ' ' + x).join(' · ') : 'Not checked yet'}</p>
      ${q && q.verdict !== 'approved' ? `<p class="small">${q.verdict === 'blocked' ? 'Publishing is blocked by: ' : q.verdict === 'hold' ? 'Held for your review because these checks were uncertain: ' : 'Repairing: '}${esc((q.checks.reasons || []).join(', '))}</p>` : ''}
      <p class="small muted">${esc(q ? q.checks.disclaimer : '')}</p>
      <h3>QA history</h3>${d.qa_history.map(h => `<p class="small">${when(h.created_at)} ${chip(h.verdict)} ${Object.entries(h.summary).map(([k, x]) => k + ' ' + x).join(' · ')}</p>`).join('') || '<p class="muted">—</p>'}
      <h3>Repairs</h3>${d.repairs.map(r => `<p class="small">${when(r.created_at)} · ${esc(r.target)} · ${esc(r.action)} (attempt ${r.attempt})</p>`).join('') || '<p class="muted">None</p>'}</div>
    <div class="panel tablewrap">${q ? `<table><tr><th>Check</th><th>Result</th><th>Time</th><th>Evidence</th><th>Conf.</th><th>Repair</th></tr>${checks.map(c => `<tr><td>${esc(c.name)}<br><span class="small muted">${esc(c.category)} · ${esc(c.method)}</span></td>
      <td><span class="st-${esc(c.status)}">${esc(c.status)}</span><br><span class="sev-${esc(c.severity)} small">${esc(c.severity)}</span></td>
      <td>${c.frames ? `<a href="#" data-seek="${c.frames[0]}">${esc(c.time)}</a>` : '—'}</td>
      <td><details><summary class="small">${esc(JSON.stringify(c.evidence).slice(0, 90))}</summary><pre>${esc(JSON.stringify(c.evidence, null, 1))}</pre></details></td>
      <td>${c.confidence}</td><td class="small">${c.repair ? esc(c.repair.action) + (c.repair.shot ? ' ' + esc(c.repair.shot) : '') : '—'}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No problems found by the automated checks.</td></tr>'}</table>` : empty('QA has not run for this render.')}</div></div>`;
  bind('[data-seek]', 'click', e => { e.preventDefault(); const p = $('#qa-player'); if (p) { p.currentTime = Number(e.target.dataset.seek) / 30; p.play(); } });
}

/* ------------------------------------------------------------------ calendar */
async function calendar() {
  const c = await api('calendar?past_hours=48&future_hours=48'); S.tz = c.timezone;
  const days = {};
  c.slots.forEach(s => { const k = when(s.slot_at, {dateStyle: 'full', timeStyle: undefined}); (days[k] = days[k] || []).push(s); });
  setView(`<div class="panel"><p class="small">${esc(c.dst_rules)} Display timezone: <b>${esc(c.timezone)}</b> · policy ${esc(c.policy)}.</p>
    ${c.dst_gaps.length ? `<p class="warn small">Daylight-saving gaps in this window: ${c.dst_gaps.map(g => esc(g.local)).join(', ')} (no slot exists at those local times).</p>` : ''}</div>
    ${Object.keys(days).length ? Object.entries(days).map(([day, list]) => `<div class="panel slotday"><h2>${esc(day)}</h2>${list.map(s => `<div class="slot"><div><b>${when(s.slot_at, {dateStyle: undefined})}</b><br><span class="small muted">${new Date(s.slot_at * 1000).toISOString().slice(11, 16)} UTC</span></div>
      <div>${chip(s.status)}</div><div>${s.title ? `<a href="#" data-video="${esc(s.video_id)}">${esc(s.title)}</a> ${s.video_status ? chip(s.video_status) : ''}` : ''}${s.youtube_video_id ? ` · <a href="https://www.youtube.com/watch?v=${esc(s.youtube_video_id)}" target="_blank" rel="noopener noreferrer">${esc(s.youtube_video_id)}</a>` : ''}${s.reason ? `<br><span class="small muted">${esc(s.reason)}</span>` : ''}</div></div>`).join('')}</div>`).join('') : empty('No slots yet. Slots are created by the running worker for the next horizon.')}`);
  bindCommon();
}

/* ------------------------------------------------------------------ characters */
async function characters() {
  const r = await api('characters');
  setView(`<p class="muted">Recurring, original cast. Changes apply to future renders. Voices must be catalogue voices or voices you have rights to use.</p><div class="grid g2">${Object.entries(r.characters).map(([id, c]) => `<div class="panel"><form data-char="${esc(id)}">
    <div class="row"><label>Name<input name="name" value="${esc(c.name)}"></label><label>Scale<input name="scale" type="number" step="0.05" min="0.6" max="1.2" value="${c.bible.scale}"></label><label class="check"><input type="checkbox" name="active" ${c.active ? 'checked' : ''}> Active</label></div>
    <label>Summary<input name="summary" value="${esc(c.bible.summary)}"></label><label>Personality<input name="personality" value="${esc(c.bible.personality)}"></label>
    <h3>Palette</h3><div class="row">${r.vocab.palette_slots.map(k => `<label>${k}${(r.vocab.optional_palette_slots || []).includes(k) ? `<span class="small muted"> <input type="checkbox" name="use_${k}" ${(c.bible.palette || {})[k] ? 'checked' : ''}> set</span>` : ''}<input type="color" name="pal_${k}" value="${esc((c.bible.palette || {})[k] || '#cccccc')}"></label>`).join('')}</div>
    <div class="row"><label>Top<select name="top">${r.vocab.tops.map(x => `<option ${x === c.bible.costume.top ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label>Hair<select name="hair">${r.vocab.hair.map(x => `<option value="${x || ''}" ${x === (c.bible.costume.hair || 'bald') ? 'selected' : ''}>${x || 'none'}</option>`).join('')}</select></label>
      <label>Hat<select name="hat">${r.vocab.hats.map(x => `<option value="${x || ''}" ${x === c.bible.costume.hat ? 'selected' : ''}>${x || 'none'}</option>`).join('')}</select></label>
      <label>Eyewear<select name="eyewear">${(r.vocab.eyewear || [null]).map(x => `<option value="${x || ''}" ${x === (c.bible.costume.eyewear || null) ? 'selected' : ''}>${x || 'none'}</option>`).join('')}</select></label>
      <label>Badge<select name="badge">${r.vocab.badges.map(x => `<option value="${x || ''}" ${x === c.bible.costume.badge ? 'selected' : ''}>${x || 'none'}</option>`).join('')}</select></label>
      <label class="check"><input type="checkbox" name="tie" ${c.bible.costume.tie ? 'checked' : ''}> Tie</label></div>
    <label>Visual rules (one per line)<textarea name="rules">${esc((c.bible.visual_rules || []).join('\n'))}</textarea></label>
    <h3>Voice</h3><div class="row"><label>Free voice (Piper) speaker 0–903<input name="piper_speaker" type="number" min="0" max="903" value="${c.voice.piper_speaker ?? ''}"></label><label>&nbsp;<button type="button" data-hear="${esc(id)}">Hear free voice</button></label></div><div id="hear-${esc(id)}"></div>
    <div class="row"><label>Pitch (semitones)<input name="pitch_semitones" type="number" step="0.5" min="-6" max="6" value="${c.voice.pitch_semitones ?? 0}"></label>
      <label>Formants<select name="pitch_formants">${(r.vocab.pitch_formants || ['preserve']).map(x => `<option ${x === (c.voice.pitch_formants || 'preserve') ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label>Piper length ×<input name="piper_length_scale" type="number" step="0.01" min="0.7" max="1.4" value="${c.voice.piper_length_scale ?? ''}" placeholder="1.0"></label>
      <label>Variation<input name="piper_noise_scale" type="number" step="0.01" min="0.1" max="1" value="${c.voice.piper_noise_scale ?? ''}" placeholder="0.667"></label>
      <label>Rhythm variation<input name="piper_noise_w" type="number" step="0.01" min="0.1" max="1.2" value="${c.voice.piper_noise_w ?? ''}" placeholder="0.8"></label>
      <label>Narration length ×<input name="narration_length_scale" type="number" step="0.01" min="0.8" max="1.2" value="${c.voice.narration_length_scale ?? ''}" placeholder="1.0"></label></div>
    ${c.voice.casting_note ? `<p class="small muted">${esc(c.voice.casting_note)}</p>` : ''}
    <div class="row"><label>OpenAI voice<input name="openai_voice" value="${esc(c.voice.openai_voice || '')}"></label><label>ElevenLabs voice id<input name="elevenlabs_voice_id" value="${esc(c.voice.elevenlabs_voice_id || '')}"></label><label>Local test voice<input name="local_test_voice" value="${esc(c.voice.local_test_voice || '')}"></label></div>
    <label>Voice direction<input name="openai_instructions" value="${esc(c.voice.openai_instructions || '')}"></label>
    <label>Voice rights note<input name="rights_note" value="${esc(c.voice.rights_note || '')}" placeholder="e.g. provider catalogue voice"></label>
    <div class="actions"><button class="primary">Save</button><button type="button" data-preview="${esc(id)}">Render preview</button></div><div id="pv-${esc(id)}"></div></form></div>`).join('')}</div>`);
  const DELIVERY = ['pitch_semitones', 'piper_length_scale', 'piper_noise_scale', 'piper_noise_w', 'narration_length_scale'];
  const delivery = f => { const v = {pitch_formants: f.pitch_formants.value}; DELIVERY.forEach(k => { if (f[k].value !== '') v[k] = Number(f[k].value); }); return v; };
  bind('form[data-char]', 'submit', async e => {
    const f = e.target, pal = {}, c = r.characters[f.dataset.char];
    r.vocab.palette_slots.forEach(k => { if (!f['use_' + k] || f['use_' + k].checked) pal[k] = f['pal_' + k].value; });
    await api('characters/' + f.dataset.char, 'PUT', {name: f.name.value, active: f.active.checked,
      bible: {summary: f.summary.value, personality: f.personality.value, scale: Number(f.scale.value), palette: pal,
        costume: {top: f.top.value, hair: f.hair.value || null, hat: f.hat.value || null, eyewear: f.eyewear.value || null,
          badge: f.badge.value || null, tie: f.tie.checked},
        costume_variants: c.bible.costume_variants,
        visual_rules: f.rules.value.split('\n').filter(Boolean)},
      voice: Object.assign({piper_speaker: f.piper_speaker.value === '' ? null : Number(f.piper_speaker.value),
        openai_voice: f.openai_voice.value, elevenlabs_voice_id: f.elevenlabs_voice_id.value, local_test_voice: f.local_test_voice.value,
        openai_instructions: f.openai_instructions.value, rights_note: f.rights_note.value, casting_note: c.voice.casting_note}, delivery(f))});
    toast('Character saved');
  });
  bind('[data-hear]', 'click', async e => {
    const id = e.target.dataset.hear, f = e.target.closest('form');
    $('#hear-' + id).innerHTML = '<p class="muted small">Generating with the free offline voice…</p>';
    const r = await api('characters/' + id + '/voice-preview', 'POST', Object.assign({piper_speaker: f.piper_speaker.value}, delivery(f)));
    $('#hear-' + id).innerHTML = `<audio controls autoplay src="${r.preview}?t=${Date.now()}"></audio><p class="small muted">Speaker ${r.speaker} · pitch ${r.pitch_semitones ?? 0} st · ${esc(r.license)} · save the character to keep it</p>`;
  });
  bind('[data-preview]', 'click', async e => { const id = e.target.dataset.preview; $('#pv-' + id).innerHTML = '<p class="muted">Rendering with Blender…</p>'; const p = await api('characters/' + id + '/preview', 'POST', {}); $('#pv-' + id).innerHTML = `<img src="${p.preview}?t=${Date.now()}" alt="Preview" class="preview-still">`; });
}

/* ------------------------------------------------------------------ library */
async function library() {
  const r = await api('assets');
  setView(`<div class="panel"><h2>Upload</h2><form id="up"><label>File (PNG/JPEG/WebP, MP4/MOV/WebM, MP3/WAV/M4A)<input type="file" name="file" required></label>
    <label>Purpose<select name="purpose"><option value="production">Production asset</option><option value="music">Licensed music</option><option value="reference">Reference video I may analyse</option></select></label>
    <label class="check"><input type="checkbox" name="rights" value="yes" required> I own this file or have permission to use it for this purpose</label><button class="primary">Upload</button></form></div>
    <div class="panel tablewrap">${r.assets.length ? `<table><tr><th>Name</th><th>Kind</th><th>Rights</th><th>Size</th><th>Uploaded</th><th></th></tr>${r.assets.map(a => `<tr><td>${esc(a.name)}<br><span class="small muted mono">${esc(a.id)}</span></td><td>${esc(a.kind)}</td><td class="small">${esc(a.rights || 'legacy upload')}</td><td>${a.bytes ? Math.round(a.bytes / 1024) + ' KB' : '—'}</td><td class="small">${when(a.created_at)}</td>
      <td>${a.kind === 'video' ? `<button class="small" data-analyse="${esc(a.id)}">Analyse as reference</button>` : ''}</td></tr>`).join('')}</table>` : empty('No uploads yet.')}</div>`);
  bind('#up', 'submit', async e => { const b = e.target.querySelector('button'); b.disabled = true; try { await api('assets', 'POST', new FormData(e.target)); toast('Uploaded'); library(); } finally { b.disabled = false; } });
  bind('[data-analyse]', 'click', async e => { await api('research/analyze', 'POST', {asset: e.target.dataset.analyse}); toast('Reference analysis queued'); });
}

/* ------------------------------------------------------------------ queue */
async function queue() {
  const r = await api('tasks');
  setView(`<div class="panel tablewrap">${r.tasks.length ? `<table><tr><th>Task</th><th>Status</th><th>Attempts</th><th>Due / updated</th><th>Last note</th><th></th></tr>${r.tasks.map(t => `<tr><td>${esc(t.kind)}<br><span class="small muted">${esc(t.video_id || '')}</span></td><td>${chip(t.status)}</td><td>${t.attempts}/${t.max_attempts}</td>
    <td class="small">${when(t.due_at)}<br>${ago(t.updated_at)}</td><td class="small">${esc((t.last_error || '').slice(0, 220))}</td>
    <td>${['queued', 'running'].includes(t.status) ? `<button class="small" data-task="${esc(t.id)}" data-act="cancel">Cancel</button>` : ['failed', 'dead', 'cancelled'].includes(t.status) ? `<button class="small" data-task="${esc(t.id)}" data-act="requeue">Requeue</button>` : ''}</td></tr>`).join('')}</table>` : empty('The queue is empty.')}</div>`);
  bind('[data-task]', 'click', async e => { await api(`tasks/${e.target.dataset.task}/${e.target.dataset.act}`, 'POST', {}); queue(); });
}

/* ------------------------------------------------------------------ budget & learning */
async function budget() {
  const [b, a] = await Promise.all([api('budget'), api('analytics')]);
  const s = b.summary;
  setView(`<div class="grid g3"><div class="panel"><h2>Today (${esc(s.day)})</h2><div class="stat">${money(s.today.total_counted)} <small>of ${money(s.limits.daily)}</small></div><p class="small">Reserved ${money(s.today.reserved)} · measured ${money(s.today.committed_measured)} · estimated ${money(s.today.committed_estimated)}</p></div>
    <div class="panel"><h2>Month (${esc(s.month)})</h2><div class="stat">${money(s.month_totals.total_counted)} <small>of ${money(s.limits.monthly)}</small></div></div>
    <div class="panel"><h2>Per video</h2><div class="stat">${money(s.limits.per_video)}</div><p class="small muted">${esc(s.note)}</p></div></div>
    <div class="panel"><h2>How estimates are made</h2><p>${esc(b.estimate_method)}</p><p class="small">Current price table: ${Object.entries(b.prices).map(([k, v]) => esc(k) + ' ' + v).join(' · ')}</p></div>
    <div class="grid g2"><div class="panel tablewrap"><h2>Ledger</h2>${b.ledger.length ? `<table><tr><th>When</th><th>Category</th><th>Provider</th><th>Status</th><th>Estimate</th><th>Actual</th></tr>${b.ledger.map(x => `<tr><td class="small">${when(x.created_at)}</td><td>${esc(x.category)}</td><td>${esc(x.provider)}</td><td>${chip(x.status)}</td><td>${money(x.estimate)}</td><td>${x.actual == null ? '—' : money(x.actual) + ' ' + esc(x.actual_kind)}</td></tr>`).join('')}</table>` : empty('No paid activity yet.')}</div>
    <div class="panel"><h2>Learning from your videos</h2><p class="small">${esc((a.status || {}).note || 'No analytics collected yet.')}</p>
      ${a.findings.length ? a.findings.map(f => `<div class="panel"><p>${chip(f.kind === 'finding' ? 'pass' : 'uncertain', f.kind)} <b>${esc(f.feature)}</b>: ${esc(f.body.better)} ${f.body.mean_better}% vs ${esc(f.body.worse)} ${f.body.mean_worse}% average viewed (n=${f.body.n_better} vs ${f.body.n_worse})</p><p class="small muted">${esc(f.body.caveat)} CI95 ${esc(JSON.stringify(f.body.ci95))}</p></div>`).join('') : empty('No comparisons yet. They need published videos with analytics older than the minimum age.')}</div></div>`);
}

/* ------------------------------------------------------------------ connections */
async function connections() {
  const c = await api('connections');
  const params = new URLSearchParams(location.search);
  const yt = c.youtube;
  setView(`${params.get('connection_error') ? `<div class="panel error">${esc(params.get('connection_error'))}</div>` : ''}${params.get('connection') === 'success' ? '<div class="panel good">YouTube connected. Confirm the channel below.</div>' : ''}
  <div class="grid g2"><div class="panel"><h2>Credentials</h2><p class="muted small">Stored encrypted on this server; never shown again. Leave a field empty to keep the saved value.</p><form id="sec">
    ${Object.entries(c.labels).map(([k, label]) => `<label>${esc(label)} ${c.secrets[k] ? '<span class="good">● saved</span>' : '<span class="muted">○ not set</span>'}<input name="${k}" type="${k.endsWith('URL') ? 'url' : 'password'}" autocomplete="off" placeholder="${c.secrets[k] ? '•••••• (saved)' : ''}"></label>`).join('')}
    <button class="primary">Save credentials</button></form></div>
  <div class="panel"><h2>YouTube channel</h2>
    <p>Add this exact redirect URI to your Google OAuth web client: <code>${esc(c.redirect_uri)}</code></p>
    <p class="small muted">Scopes requested: youtube.upload, youtube.readonly, and (optional) yt-analytics.readonly. Blox cannot edit or delete existing videos.</p>
    ${yt.connected ? `<p>Connected channel: <b>${esc(yt.channel.title || '?')}</b> <span class="mono small">${esc(yt.channel.id || '')}</span> ${yt.channel.confirmed ? chip('pass', 'confirmed') : chip('uncertain', 'not confirmed')}</p>
      ${yt.channel.confirmed ? '' : `<button class="primary" id="confirm-ch" data-id="${esc(yt.channel.id)}">Yes, publish to this channel</button>`}
      <p class="small">Scopes granted: ${esc(yt.scopes.join(', '))}</p>${yt.error ? `<p class="error">${esc(yt.error)}</p>` : ''}
      <div class="actions"><a class="button" href="/oauth/start?analytics=1">Reconnect</a><button class="danger" id="disconnect">Disconnect</button></div>` :
      `<div class="actions">${yt.oauth_client ? '<a class="button primary" href="/oauth/start?analytics=1">Connect YouTube (with analytics)</a><a class="button" href="/oauth/start?analytics=0">Connect without analytics</a>' : '<p class="muted">Save the Google OAuth client ID and secret first.</p>'}</div>`}
    <h3>Publishing requirements to check with Google</h3><ul class="small"><li>Unaudited API projects can only upload private videos; scheduled publishing needs a project that passed YouTube’s API compliance audit.</li><li>Upload calls use a separate daily quota bucket; check your project’s current quota in Google Cloud Console.</li><li>Channels have their own daily upload limits; Blox reports YouTube’s refusals instead of retrying blindly.</li></ul></div></div>
  <div class="grid g2"><div class="panel"><h2>Tools</h2><p>Blender: ${c.blender ? '<span class="good">' + esc(c.blender) + '</span>' : '<span class="error">not found</span>'}</p><p>FFmpeg: ${c.ffmpeg ? '<span class="good">' + esc(c.ffmpeg) + '</span>' : '<span class="error">not found</span>'}</p></div>
  <div class="panel"><h2>Circuit breakers</h2>${Object.keys(c.breakers).length ? Object.entries(c.breakers).map(([p, b]) => `<p>${chip(b.state === 'closed' ? 'pass' : 'fail', p + ' ' + b.state)} failures ${b.failures} ${b.retry_at ? '· retry ' + when(b.retry_at) : ''}<br><span class="small muted">${esc(b.last_error)}</span></p>`).join('') : '<p class="muted">All providers healthy so far.</p>'}</div></div>`);
  bind('#sec', 'submit', async e => { const body = {}; new FormData(e.target).forEach((v, k) => { if (String(v).trim()) body[k] = v; }); const r = await api('secrets', 'POST', body); toast('Saved: ' + (r.saved.join(', ') || 'nothing changed')); connections(); });
  bind('#confirm-ch', 'click', async e => { await api('youtube/confirm', 'POST', {channel_id: e.target.dataset.id}); toast('Channel confirmed'); connections(); });
  bind('#disconnect', 'click', async () => { if (!confirm('Disconnect YouTube? Autopilot will pause.')) return; await api('youtube/disconnect', 'POST', {}); connections(); });
}

/* ------------------------------------------------------------------ settings */
function inp(sec, key, val, type, extra) { return `<label>${esc(key.replace(/_/g, ' '))}<input data-sec="${sec}" data-key="${key}" type="${type || 'text'}" value="${esc(val)}" ${extra || ''}></label>`; }
function sel(sec, key, val, options) { return `<label>${esc(key.replace(/_/g, ' '))}<select data-sec="${sec}" data-key="${key}">${options.map(o => `<option value="${esc(String(o[0]))}" ${String(o[0]) === String(val) ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select></label>`; }
async function settings() {
  const {prefs: p, vocab} = await api('settings');
  const tri = v => v === true ? 'true' : v === false ? 'false' : '';
  const a = p.autopilot, s = p.schedule, pub = p.publishing, pr = p.production, r = p.research, q = p.qa, b = p.budget;
  setView(`<div class="panel"><h2>Autopilot activation</h2><p>Currently: <b>${a.enabled ? (a.paused ? 'paused' : 'enabled') : 'off'}</b> · mode ${esc(a.mode)}${a.emergency_stop ? ' · <span class="error">emergency stop active</span>' : ''}</p>
    <p class="small muted">Autopilot produces ahead of the schedule, keeps a QA-approved buffer and publishes one video per slot. Review mode waits for your approval of each QA-approved video; autopilot mode schedules QA-approved videos without asking. Videos that fail or are uncertain in QA are never published automatically. A skipped slot is preferred to publishing a known defect.</p>
    <form id="activate"><div class="row"><label>Mode<select name="mode"><option value="review" ${a.mode === 'review' ? 'selected' : ''}>Review each video before publishing</option><option value="autopilot" ${a.mode === 'autopilot' ? 'selected' : ''}>Publish QA-approved videos automatically</option></select></label>
    <label>Type ENABLE to confirm<input name="confirm" autocomplete="off"></label></div><button class="primary">Enable autopilot</button> <button type="button" id="ap-off" class="quiet">Turn off</button></form></div>
  <form id="prefs">
  <div class="grid g2"><div class="panel"><h2>Schedule</h2><div class="row">${inp('schedule', 'timezone', s.timezone)}${inp('schedule', 'interval_minutes', s.interval_minutes, 'number')}${inp('schedule', 'anchor_local', s.anchor_local)}</div>
    <div class="row">${sel('schedule', 'policy', s.policy, [['local_wall_clock', 'Local wall-clock times'], ['fixed_interval_utc', 'Fixed real-time interval']])}${inp('schedule', 'max_per_rolling_24h', s.max_per_rolling_24h, 'number')}</div>
    <div class="row">${inp('schedule', 'buffer_target', s.buffer_target, 'number')}${inp('schedule', 'max_in_production', s.max_in_production, 'number')}${inp('schedule', 'upload_lead_minutes', s.upload_lead_minutes, 'number')}${inp('schedule', 'min_lead_minutes', s.min_lead_minutes, 'number')}${inp('schedule', 'horizon_hours', s.horizon_hours, 'number')}</div></div>
  <div class="panel"><h2>Publishing</h2><div class="row">${sel('publishing', 'made_for_kids', tri(pub.made_for_kids), [['', 'Choose…'], ['false', 'No, not made for kids'], ['true', 'Yes, made for kids']])}
    ${sel('publishing', 'synthetic_disclosure', tri(pub.synthetic_disclosure), [['', 'Choose…'], ['true', 'Yes: disclose altered/synthetic content'], ['false', 'No disclosure needed']])}</div>
    <p class="small muted">The synthetic-content flag is YouTube’s disclosure for realistic altered or synthetic media. You decide whether it applies to your stories; the description always states the video is computer-animated with AI-assisted writing and voices.</p>
    <div class="row">${inp('publishing', 'category_id', pub.category_id)}${inp('publishing', 'default_language', pub.default_language)}${inp('publishing', 'publish_verify_grace_minutes', pub.publish_verify_grace_minutes, 'number')}</div>
    <label>Tags (comma separated)<input data-sec="publishing" data-key="tags" data-list="1" value="${esc(pub.tags.join(', '))}"></label>
    <label>Description footer<textarea data-sec="publishing" data-key="description_footer">${esc(pub.description_footer)}</textarea></label>
    <label class="check"><input type="checkbox" data-sec="publishing" data-key="notify_subscribers" ${pub.notify_subscribers ? 'checked' : ''}> Notify subscribers</label>
    <label class="check"><input type="checkbox" data-sec="publishing" data-key="upload_thumbnail" ${pub.upload_thumbnail ? 'checked' : ''}> Upload generated thumbnail (Shorts may not display it)</label></div></div>
  <div class="grid g2"><div class="panel"><h2>Channel & niche</h2><label>Niche<textarea data-sec="channel" data-key="niche">${esc(p.channel.niche)}</textarea></label>
    <label>Game names (comma separated)<input data-sec="channel" data-key="game_names" data-list="1" value="${esc(p.channel.game_names.join(', '))}"></label>
    <label>Negative keywords<input data-sec="channel" data-key="negative_keywords" data-list="1" value="${esc(p.channel.negative_keywords.join(', '))}"></label>
    <label>Audience note<input data-sec="channel" data-key="audience_note" value="${esc(p.channel.audience_note)}"></label></div>
  <div class="panel"><h2>Production</h2><div class="row">${sel('production', 'renderer', pr.renderer, vocab.renderers.map(x => [x, x]))}${sel('production', 'tts_provider', pr.tts_provider, vocab.tts.map(x => [x, {local_test: 'local test voice (not for publishing)', piper: 'Piper (free, offline)', openai: 'OpenAI (paid)', elevenlabs: 'ElevenLabs (paid)'}[x] || x]))}${sel('production', 'blender_engine', pr.blender_engine, [['BLENDER_EEVEE', 'EEVEE'], ['CYCLES', 'Cycles'], ['BLENDER_WORKBENCH', 'Workbench (fast, flat)']])}</div>
    <div class="row">${inp('production', 'min_seconds', pr.min_seconds, 'number')}${inp('production', 'target_seconds', pr.target_seconds, 'number')}${inp('production', 'max_seconds', pr.max_seconds, 'number')}${inp('production', 'fps', pr.fps, 'number')}${inp('production', 'width', pr.width, 'number')}${inp('production', 'height', pr.height, 'number')}</div>
    <div class="row">${inp('production', 'pace', pr.pace, 'number', 'step="0.05" min="1" max="2"')}${inp('production', 'speech_rate', pr.speech_rate, 'number', 'step="0.05" min="0.8" max="1.6"')}</div>
    <p class="small muted">Pace (1.0–2.0) is the speed of the whole video: stories are written in story time and pace 1.5 plays every action, camera move and pause 1.5 times faster (a 30 s story becomes a 20 s video). Speech rate (0.8–1.6) is how fast the voices speak; above about 1.3 speech stops sounding natural, so the rest of the speed-up comes from shorter pauses between lines. A line that still does not fit is sped up slightly, never above 1.5× in total. Lengths above are finished-video seconds.</p>
    <div class="row">${inp('production', 'text_model', pr.text_model)}${inp('production', 'vision_model', pr.vision_model)}${inp('production', 'tts_model', pr.tts_model)}${inp('production', 'asr_model', pr.asr_model)}${inp('production', 'runway_model', pr.runway_model)}</div>
    <div class="row">${sel('production', 'music', pr.music, [['generated', 'Generated original music'], ['asset', 'My licensed track'], ['none', 'No music']])}${inp('production', 'music_asset', pr.music_asset)}${inp('production', 'music_gain_db', pr.music_gain_db, 'number')}${inp('production', 'duck_db', pr.duck_db, 'number')}</div>
    <label class="check"><input type="checkbox" data-sec="production" data-key="captions" ${pr.captions ? 'checked' : ''}> Burn in captions</label>
    <label class="check"><input type="checkbox" data-sec="production" data-key="depth_of_field" ${pr.depth_of_field ? 'checked' : ''}> Soft background behind faces (depth of field, about 25% slower renders)</label>
    ${sel('production', 'story_source', pr.story_source || 'auto', [['auto', 'Automatic: LLM API if connected, otherwise the story backlog'], ['backlog', 'Story backlog only (no AI API cost)'], ['llm', 'LLM API only (paid)']])}
    <label class="check"><input type="checkbox" data-sec="production" data-key="allow_template_stories" ${pr.allow_template_stories ? 'checked' : ''}> Allow offline template stories (dry runs)</label>
    <label>Pronunciations (JSON object)<textarea data-sec="production" data-key="pronunciations" data-json="1">${esc(JSON.stringify(pr.pronunciations, null, 1))}</textarea></label></div></div>
  <div class="grid g2"><div class="panel"><h2>Research</h2><label class="check"><input type="checkbox" data-sec="research" data-key="enabled" ${r.enabled ? 'checked' : ''}> Research enabled</label>
    <label>Queries (comma separated)<input data-sec="research" data-key="queries" data-list="1" value="${esc(r.queries.join(', '))}"></label>
    <div class="row">${inp('research', 'interval_minutes', r.interval_minutes, 'number')}${inp('research', 'snapshot_interval_minutes', r.snapshot_interval_minutes, 'number')}${inp('research', 'max_search_calls_per_run', r.max_search_calls_per_run, 'number')}${inp('research', 'region_code', r.region_code)}</div>
    <label>Windows in hours (comma separated)<input data-sec="research" data-key="windows_hours" data-list="num" value="${esc(r.windows_hours.join(', '))}"></label>
    <label>Quota limits (JSON)<textarea data-sec="research" data-key="quota" data-json="1">${esc(JSON.stringify(r.quota, null, 1))}</textarea></label>
    <label>Ranking weights (JSON)<textarea data-sec="research" data-key="weights" data-json="1">${esc(JSON.stringify(r.weights, null, 1))}</textarea></label></div>
  <div class="panel"><h2>Quality & budget</h2><div class="row">${inp('qa', 'max_repairs_per_target', q.max_repairs_per_target, 'number')}${inp('qa', 'max_repair_rounds', q.max_repair_rounds, 'number')}${sel('qa', 'uncertain_policy', q.uncertain_policy, [['hold', 'Hold uncertain videos for review'], ['allow_minor', 'Allow uncertain major checks']])}${sel('qa', 'vision_review', q.vision_review, [['when_available', 'When available'], ['required', 'Required'], ['off', 'Off']])}</div>
    <div class="row">${inp('qa', 'loudness_target_lufs', q.loudness_target_lufs, 'number', 'step="0.5"')}${inp('qa', 'max_silence_s', q.max_silence_s, 'number', 'step="0.1"')}${inp('qa', 'foot_slide_mm_per_frame', q.foot_slide_mm_per_frame, 'number', 'step="0.5"')}</div>
    <label class="check"><input type="checkbox" data-sec="qa" data-key="require_asr" ${q.require_asr ? 'checked' : ''}> Require speech recognition of the final mix</label>
    <div class="row">${inp('budget', 'per_video_usd', b.per_video_usd, 'number', 'step="0.01"')}${inp('budget', 'daily_usd', b.daily_usd, 'number', 'step="0.01"')}${inp('budget', 'monthly_usd', b.monthly_usd, 'number', 'step="0.01"')}${inp('budget', 'repair_share', b.repair_share, 'number', 'step="0.05"')}${inp('budget', 'ambiguous_retry_max_usd', b.ambiguous_retry_max_usd, 'number', 'step="0.001"')}</div>
    <label>Price table (JSON; estimates only)<textarea data-sec="budget" data-key="prices" data-json="1">${esc(JSON.stringify(b.prices, null, 1))}</textarea></label>
    <p class="small muted">Also set billing limits in each provider dashboard: they are the hard ceiling. Blox reserves estimated cost before every paid call and never retries an ambiguous paid request automatically unless you raise the ambiguous-retry ceiling.</p></div></div>
  <div class="actions"><button class="primary">Save settings</button></div></form>`);
  bind('#prefs', 'submit', async () => {
    const patch = {};
    $$('[data-sec]').forEach(el => {
      const sec = el.dataset.sec, key = el.dataset.key; let v;
      if (el.type === 'checkbox') v = el.checked;
      else if (el.dataset.json) v = JSON.parse(el.value);
      else if (el.dataset.list === 'num') v = el.value.split(',').map(x => Number(x.trim())).filter(x => !isNaN(x));
      else if (el.dataset.list) v = el.value.split(',').map(x => x.trim()).filter(Boolean);
      else if (el.type === 'number') v = Number(el.value);
      else if (['made_for_kids', 'synthetic_disclosure'].includes(key)) v = el.value === '' ? null : el.value === 'true';
      else v = el.value;
      (patch[sec] = patch[sec] || {})[key] = v;
    });
    await api('settings', 'POST', patch); toast('Settings saved'); settings();
  });
  bind('#activate', 'submit', async e => { await api('autopilot/enable', 'POST', {mode: e.target.mode.value, confirm: e.target.confirm.value}); toast('Autopilot enabled'); settings(); });
  bind('#ap-off', 'click', async () => { await api('autopilot/disable', 'POST', {}); toast('Autopilot off'); settings(); });
}

const VIEWS = {dashboard, research, productions, stories, quality, calendar, characters, library, queue, budget, connections, settings};

async function sidebar() {
  try {
    const s = await api('state'); S.tz = s.timezone;
    const a = s.autopilot;
    $('#ap-chip').outerHTML = `<div id="ap-chip">${chip(a.emergency_stop ? 'fail' : !a.enabled ? 'skipped' : a.paused ? 'uncertain' : 'pass', 'Autopilot: ' + (a.emergency_stop ? 'E-STOP' : !a.enabled ? 'off' : a.paused ? 'paused' : a.mode))}</div>`;
    const w = s.readiness.worker;
    $('#worker-chip').outerHTML = `<div id="worker-chip">${chip(w.ok ? 'pass' : 'fail', w.ok ? 'Worker online' : 'Worker offline')}</div>`;
    const banner = $('#banner');
    if (a.emergency_stop) { banner.hidden = false; banner.textContent = 'Emergency stop is active: no paid work or uploads will start. Clear it on the dashboard when ready.'; }
    else banner.hidden = true;
  } catch (e) { /* shown on next view load */ }
}

$$('#nav button').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));
$('#estop').addEventListener('click', guard(async () => { if (!confirm('Emergency stop: cancel queued paid work and stop new generation and uploads now?')) return; await api('autopilot/estop', 'POST', {}); toast('Emergency stop active'); sidebar(); show('dashboard'); }));
$('#logout').addEventListener('click', guard(async () => { await api('logout', 'POST', {}); location.href = '/login'; }));
show((location.hash || '#dashboard').slice(1) in VIEWS ? location.hash.slice(1) : 'dashboard');
sidebar(); setInterval(sidebar, 15000);
setInterval(() => { if (['dashboard', 'queue'].includes(S.view) && !document.hidden) VIEWS[S.view]().catch(() => {}); }, 20000);
