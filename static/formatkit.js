'use strict';
/* First-person story calibration (Story backlog page): upload calibration.json and/or pace_profile.json measured on
   your own channel, preview the parsed numbers next to the defaults, save or reset. Loaded after app.js; uses its
   helpers ($, api, esc, bind, toast, when, chip). */
window.FormatKit = (() => {
  const ROWS = [
    ['duration_s', 'Video length (s)'], ['wps', 'Narration words per second'], ['wps_sections', 'Pace by section (w/s)'],
    ['beat_wps_max', 'Fastest line (w/s)'], ['hook_max_s', 'Hook line ends by (s)'], ['first_word_s', 'First word at (s)'],
    ['max_shot_s', 'Longest shot (s)'], ['title_words', 'Title words'], ['mid_gag_window', 'Mid-video gag starts'],
    ['mid_gag_max_s', 'Mid-video gag lasts at most (s)'], ['winning_mechanisms', 'Winning mechanisms']];

  function val(k, v) {
    if (v === undefined || v === null) return '<span class="muted">–</span>';
    if (k === 'mid_gag_window') return esc(Math.round(v[0] * 100) + '–' + Math.round(v[1] * 100) + '% of the video');
    if (k === 'wps_sections') return ['hook', 'body', 'end'].filter(s => v[s]).map(s => `${s} ${esc(v[s][0])}–${esc(v[s][1])}`).join('<br>');
    if (k === 'winning_mechanisms') return v.length ? esc(v.map(x => x.replace(/_/g, ' ')).join(', ')) : '<span class="muted">none yet</span>';
    if (Array.isArray(v)) return esc(v.join('–'));
    if (typeof v === 'object') return esc(v.min + '–' + v.max) + (v.target !== undefined ? ` <span class="muted">(target ${esc(v.target)})</span>` : '');
    return esc(v);
  }

  function table(cols) {
    return `<table class="small"><tr><th></th>${cols.map(c => `<th>${esc(c[0])}</th>`).join('')}</tr>${ROWS.map(([k, label]) =>
      `<tr><td>${esc(label)}</td>${cols.map(c => `<td>${val(k, c[1][k])}</td>`).join('')}</tr>`).join('')}</table>`;
  }

  async function mount(el) {
    if (!el) return;
    const s = await api('format-calibration');
    const a = s.active;
    el.innerHTML = `<div class="panel"><h2>First-person story calibration</h2>
      <p class="small muted">The numbers the first-person story format is checked against. Upload the files you measured on your own channel: <code>calibration.json</code> (from the capture script or the pace script) and/or <code>pace_profile.json</code>. Only the numbers are kept. Anything unknown or out of range is dropped and listed. Nothing is saved until you press Save.</p>
      <p class="small">${s.imported ? `${chip('pass', 'imported')} from ${esc(a.source)} · ${when(a.imported_at)}` : chip('info', 'defaults')} </p>
      ${table([['Active', a], ['Defaults', s.defaults]])}
      <form id="fk-form" class="row">
        <label>calibration.json<input type="file" name="calibration" accept=".json,application/json"></label>
        <label>pace_profile.json<input type="file" name="pace_profile" accept=".json,application/json"></label>
        <label id="fk-channel-wrap" hidden>Channel<select name="channel"></select></label>
        <label>&nbsp;<span><button type="button" id="fk-preview">Preview</button> <button class="primary" type="button" id="fk-save">Save</button></span></label>
      </form>
      <div id="fk-out"></div>
      ${s.imported ? '<div class="actions"><button type="button" class="small quiet" id="fk-reset">Reset to defaults</button></div>' : ''}</div>`;
    const form = $('#fk-form');
    const data = () => {
      const fd = new FormData();
      if (form.calibration.files[0]) fd.append('calibration', form.calibration.files[0]);
      if (form.pace_profile.files[0]) fd.append('pace_profile', form.pace_profile.files[0]);
      if (!$('#fk-channel-wrap').hidden && form.channel.value) fd.append('channel', form.channel.value);
      if (!fd.has('calibration') && !fd.has('pace_profile')) throw Error('Choose calibration.json and/or pace_profile.json');
      return fd;
    };
    const channels = r => {
      if (!r.channels || r.channels.length < 2) return;
      const sel = form.channel, cur = sel.value;
      sel.innerHTML = r.channels.map(c => `<option value="${esc(c)}" ${c === (r.channel || cur) ? 'selected' : ''}>${esc(c)}</option>`).join('');
      $('#fk-channel-wrap').hidden = false;
    };
    bind('#fk-preview', 'click', async () => {
      const r = await api('format-calibration/preview', 'POST', data());
      channels(r);
      $('#fk-out').innerHTML = (r.needs_channel ? '<p class="small warn">This calibration.json has several channels. Choose yours above, then preview again.</p>' : '') +
        (Object.keys(r.parsed).length ? `<h3>Preview</h3>${table([['From the files', r.parsed], ['After saving', r.after_save], ['Defaults', r.defaults]])}` : '<p class="small muted">No usable values yet.</p>') +
        (r.dropped.length ? `<p class="small warn">Dropped:</p><ul class="small">${r.dropped.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '');
    });
    bind('#fk-save', 'click', async () => {
      const r = await api('format-calibration', 'POST', data());
      toast('Calibration saved: ' + r.saved.map(x => x.replace(/_/g, ' ')).join(', '));
      mount(el);
    });
    bind('#fk-reset', 'click', async () => {
      if (!confirm('Go back to the default calibration?')) return;
      await api('format-calibration', 'DELETE');
      toast('Back to the defaults');
      mount(el);
    });
  }

  return {mount};
})();
