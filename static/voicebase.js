'use strict';
/* Voice base: engine choice per character, reference voice profiles and the voice bake-off (Characters page).
   Loaded after app.js; uses its helpers ($, $$, api, esc, bind, toast, media, chip). */
window.VoiceBase = (() => {
  let E = null;
  const num = (x, d = 1) => (x === null || x === undefined || x === '' || Number.isNaN(Number(x))) ? '–' : Number(x).toFixed(d);
  const ENGINE_LABEL = {'': 'studio default', piper: 'Piper (free, offline)', kokoro: 'Kokoro (free, offline)',
    speechify: 'Speechify (paid, your key)', openai: 'OpenAI (paid)', elevenlabs: 'ElevenLabs (paid)'};

  function measuredRow(m) {
    m = m || {};
    return `pitch ${num(m.f0_median_hz, 0)} Hz (p10–p90 ${num(m.f0_range_st)} st) · ${m.words_per_s ? num(m.words_per_s) + ' w/s' : num(m.syllables_per_s) + ' syl/s'} · pauses ${num(m.pause_mean_s, 2)} s × ${num(m.pauses_per_sentence)}/sentence · dynamics ${num(m.dynamics_lu)} LU · brightness ${num(m.centroid_hz, 0)} Hz`;
  }

  function profilesHtml(e) {
    const rows = (e.profiles || []).map(p => `<tr><td><b>${esc(p.name)}</b><br><span class="small muted">${esc(p.source)}</span></td><td class="small">${measuredRow(p.measured)}<br><span class="muted">${p.measured.clips || 0} clips, ${num(p.measured.speech_s, 1)} s of speech</span></td><td><button type="button" class="small danger" data-del-profile="${esc(p.id)}">Delete</button></td></tr>`).join('');
    return `<h2>Reference voice profiles</h2>
    <p class="small muted">Measure clips of a voice you like the sound of (a zip or audio files; add an <code>index.csv</code> with <code>file</code> and <code>line</code> columns to get words per second, and a <code>voice</code> column to split one upload into several profiles). Only the numbers and where they came from are kept; the audio is deleted right after measuring and is never sent to any voice engine. Blox then scores its own catalogue voices by pitch, pace, pauses and energy; it does not copy anyone's voice.</p>
    ${rows ? `<table><tr><th>Profile</th><th>Measured</th><th></th></tr>${rows}</table>` : '<p class="muted">No profiles yet.</p>'}
    <form id="vb-measure" class="row"><label>Name<input name="name" placeholder="e.g. warm narrator"></label><label>Where the clips come from<input name="source" placeholder="provenance note (kept with the measurements)"></label><label>Clips<input name="files" type="file" multiple accept=".zip,.wav,.mp3,.m4a,.ogg,.flac,.aac,.opus,.csv"></label><label>&nbsp;<button class="primary">Measure reference clips</button></label></form><div id="vb-measure-status" class="small muted"></div>
    <p class="small muted">Engines: Piper ${e.engines.piper.installed ? '<span class="good">installed</span>' : '<span class="error">not installed</span>'} · Kokoro ${e.engines.kokoro.installed && e.engines.kokoro.package ? '<span class="good">installed</span>' : `<span class="muted">not installed</span> <button type="button" class="small" id="vb-install-kokoro">Install Kokoro (free, ~340 MB from GitHub, checksum verified)</button>`} · Speechify ${e.engines.speechify.configured ? '<span class="good">key saved</span>' : '<span class="muted">no key (paid; add it on Connections)</span>'}</p>`;
  }

  function charHtml(id, c, e) {
    const v = c.voice || {};
    const opts = (list, cur, labels) => list.map(x => `<option value="${esc(x)}" ${x === (cur || '') ? 'selected' : ''}>${esc(labels ? labels[x] : x)}</option>`).join('');
    const profiles = [''].concat((e.profiles || []).map(p => p.id));
    const plabels = {'': 'none'};
    (e.profiles || []).forEach(p => { plabels[p.id] = p.name; });
    return `<h3>Voice engine</h3><div class="row">
      <label>Engine for this character<select name="engine">${opts([''].concat(e.choices), v.engine, ENGINE_LABEL)}</select></label>
      <label>Kokoro voice<select name="kokoro_voice">${opts([''].concat(e.engines.kokoro.presets), v.kokoro_voice, Object.assign({'': 'default'}, ...e.engines.kokoro.presets.map(p => ({[p]: p}))))}</select></label>
      <label>Kokoro speed<input name="kokoro_speed" type="number" step="0.05" min="${e.engines.kokoro.speed_range[0]}" max="${e.engines.kokoro.speed_range[1]}" value="${v.kokoro_speed ?? ''}" placeholder="1.0"></label>
      <label>Speechify voice id<input name="speechify_voice_id" value="${esc(v.speechify_voice_id || '')}" placeholder="from your Speechify account"></label>
      <label>Target voice profile<select name="target_profile">${opts(profiles, v.target_profile, plabels)}</select></label></div>
      <div class="actions"><button type="button" data-bakeoff="${esc(id)}">Voice bake-off</button></div><div id="bo-${esc(id)}"></div>`;
  }

  function resultHtml(cid, r) {
    const m = r.measured || {};
    return `<div class="panel"><p><b>${esc(r.engine)} · ${esc(r.voice)}</b> ${r.score !== null && r.score !== undefined ? chip(r.score >= 70 ? 'pass' : r.score >= 45 ? 'uncertain' : 'fail', 'match ' + r.score + '/100') : '<span class="muted small">no target profile</span>'} <span class="small muted">${esc(r.license)}</span>
      <button type="button" class="small primary" data-use="${esc(cid)}" data-engine="${esc(r.engine)}" data-voice="${esc(r.voice)}">Use this voice</button></p>
      <p class="small">${measuredRow(m)}</p>
      <table class="small">${(r.lines || []).map(l => `<tr><td>${esc(l.role.replace(/_/g, ' '))}</td><td>${esc(l.text)}</td><td>${num(l.duration_s, 2)} s · ${num(l.words_per_s)} w/s</td><td><audio controls preload="none" src="${media(l.file)}"></audio></td></tr>`).join('')}</table></div>`;
  }

  async function bakeoff(cid) {
    const box = $('#bo-' + cid);
    box.innerHTML = '<p class="muted small">Loading…</p>';
    const b = await api('voice-bakeoff/' + cid);
    const done = {};
    b.results.forEach(r => { done[r.engine + ':' + r.voice] = r; });
    box.innerHTML = `<p class="small muted">Each candidate reads the same 12 fixed lines (${b.script.map(s => s.role.replace(/_/g, ' ')).filter((x, i, a) => a.indexOf(x) === i).join(', ')}) through the production voice chain; free engines run when you click Run, paid ones only on their own explicit button and only with the key saved.</p>
      <table class="small"><tr><th>Candidate</th><th>Engine</th><th></th></tr>${b.candidates.map(c => `<tr><td>${esc(c.label)}</td><td>${esc(c.engine)}${c.free ? '' : ' <span class="muted">(paid)</span>'}</td><td>${done[c.engine + ':' + c.voice] ? chip('pass', 'read') : ''} ${c.available ? `<button type="button" class="small" data-run="${esc(cid)}" data-engine="${esc(c.engine)}" data-voice="${esc(c.voice)}" data-paid="${c.free ? '' : '1'}">${c.free ? 'Run' : 'Run (paid, charges your account)'}</button>` : `<span class="muted">${c.free ? 'not installed' : 'no key on Connections'}</span>`}</td></tr>`).join('')}</table>
      <div class="row"><label>Another Piper speaker (0–903)<input id="bo-sp-${esc(cid)}" type="number" min="0" max="903"></label><label>&nbsp;<button type="button" class="small" data-run="${esc(cid)}" data-engine="piper" data-voice="" data-paid="">Run speaker</button></label></div>
      <div id="bo-status-${esc(cid)}" class="small muted"></div>
      <div id="bo-results-${esc(cid)}">${b.results.map(r => resultHtml(cid, r)).join('')}</div>`;
    bind(`#bo-${cid} [data-run]`, 'click', async e => {
      const el = e.target; let voice = el.dataset.voice;
      if (!voice && el.dataset.engine === 'piper') voice = $('#bo-sp-' + cid).value;
      if (!voice) throw Error('Choose a voice');
      if (el.dataset.paid && !confirm('Run the 12 test lines with a paid provider using your own key? This charges your account.')) return;
      $('#bo-status-' + cid).textContent = 'Reading the test script with ' + el.dataset.engine + ' ' + voice + '… (free engines take 20–60 s)';
      await api('voice-bakeoff/' + cid + '/run', 'POST', {engine: el.dataset.engine, voice, paid: Boolean(el.dataset.paid)});
      toast('Candidate read the script');
      bakeoff(cid);
    });
    bind(`#bo-${cid} [data-use]`, 'click', async e => {
      const el = e.target;
      await api('voice-bakeoff/' + cid + '/use', 'POST', {engine: el.dataset.engine, voice: el.dataset.voice});
      toast('Voice saved for this character: ' + el.dataset.engine + ' ' + el.dataset.voice);
      if (typeof characters === 'function') characters();
    });
  }

  async function mount(r) {
    E = await api('voice-engines');
    const view = $('#view');
    const intro = view.querySelector('p');
    const top = document.createElement('div');
    top.className = 'panel';
    top.innerHTML = profilesHtml(E);
    intro.after(top);
    $$('form[data-char]').forEach(f => {
      const div = document.createElement('div');
      div.innerHTML = charHtml(f.dataset.char, r.characters[f.dataset.char], E);
      f.querySelector('.actions').before(div);
    });
    bind('#vb-measure', 'submit', async e => {
      const f = e.target;
      if (!f.files.files.length) throw Error('Choose clips first');
      const fd = new FormData();
      fd.append('name', f.name.value);
      fd.append('source', f.source.value);
      [...f.files.files].forEach(x => fd.append('files', x));
      $('#vb-measure-status').textContent = 'Measuring… (the audio is deleted afterwards)';
      const res = await api('voice-profiles/measure', 'POST', fd);
      toast('Measured ' + res.profiles.length + ' profile(s)');
      characters();
    });
    bind('[data-del-profile]', 'click', async e => { if (!confirm('Delete this profile?')) return; await api('voice-profiles/' + e.target.dataset.delProfile, 'DELETE'); characters(); });
    bind('#vb-install-kokoro', 'click', async e => { e.target.disabled = true; e.target.textContent = 'Downloading…'; await api('voice-engines/kokoro/install', 'POST', {}); toast('Kokoro installed'); characters(); });
    bind('[data-bakeoff]', 'click', e => bakeoff(e.target.dataset.bakeoff));
  }

  function fields(f) {
    if (!f.engine) return {};
    return {engine: f.engine.value, kokoro_voice: f.kokoro_voice.value, speechify_voice_id: f.speechify_voice_id.value,
      kokoro_speed: f.kokoro_speed.value === '' ? null : Number(f.kokoro_speed.value), target_profile: f.target_profile.value};
  }

  return {mount, fields, bakeoff};
})();
