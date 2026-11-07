'use strict';
/* StudyBench core: helpers, navigation, jobs, focus timer, inventory, transcription, summaries, chat, settings, progress */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const I18N = {};              // future UI translations: I18N.de = {"Inventory": "Inventar", ...}
let LANG = 'en';
const t = s => (I18N[LANG] && I18N[LANG][s]) || s;
function applyTheme(mode) {
  const r = document.documentElement;
  if (mode === 'light' || mode === 'dark') r.setAttribute('data-theme', mode); else r.removeAttribute('data-theme');
}
applyTheme((() => { try { return JSON.parse(localStorage.getItem('sb_theme')); } catch { return null; } })());
const sleep = ms => new Promise(r => setTimeout(r, ms));
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err) {
  const d = document.createElement('div'); d.textContent = msg; if (err) d.className = 'err';
  $('#toast').appendChild(d); setTimeout(() => d.remove(), err ? 7000 : 3500);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
function modal(html) { $('#modalBox').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); $('#modalBox').innerHTML = ''; }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

function md(src) {
  const inline = x => x.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  let html = '', list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of esc(src || '').split('\n')) {
    const l = raw.trimEnd(); let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { close(); const n = m[1].length + 1; html += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[1])}</li>`; }
    else if (!l.trim()) close();
    else { close(); html += `<p>${inline(l)}</p>`; }
  }
  close(); return html;
}
const KIND = { slides: 'Slides', document: 'Word', pdf: 'PDF', audio: 'Audio', image: 'Image', text: 'Text', transcript: 'Transcript', unsupported: '?' };
const fmtDur = s => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h} h ${m} min` : `${m} min`; };
const fmtMMSS = ms => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };

/* ------------------------------------------------------------------ state + router */
const S = { topics: [], topic: null, view: 'inventory', sub: null, settings: null, docs: [], jobs: [], inv: { q: '', kind: '', status: '', cat: '', prio: '' }, busy: false, expect: 0 };
const views = {};
const NAV = [
  ['inventory', '1', 'Inventory'], ['transcribe', '2', 'Transcribe'], ['interpret', '3', 'Interpret'],
  ['practice', '4', 'Practice'], ['chat', '5', 'Chat'], ['recommend', '6', 'Recommend'], ['present', '7', 'Present'],
  null, ['focus', '⏱', 'Focus & recovery'], ['progress', '▤', 'Progress'], ['settings', '⚙', 'Settings'],
];
function drawNav() {
  $('#nav').innerHTML = NAV.map(n => n ? `<a data-v="${n[0]}" class="${S.view === n[0] ? 'on' : ''} ${n[3] || ''}"><span class="n">${n[1]}</span>${t(n[2])}${n[3] ? ' <small>(soon)</small>' : ''}</a>` : '<hr>').join('');
}
$('#nav').addEventListener('click', e => { const a = e.target.closest('a[data-v]'); if (a) go(a.dataset.v); });
function go(view, sub) { S.view = view; S.sub = sub || null; location.hash = `#/${view}${sub ? '/' + sub : ''}`; }
window.addEventListener('hashchange', () => { const [v, s] = location.hash.replace('#/', '').split('/'); if (views[v]) { S.view = v; S.sub = s || null; render(); } });
async function render() {
  drawNav();
  const name = (NAV.find(n => n && n[0] === S.view) || [])[2] || '';
  const tp = S.topics.find(x => x.id === S.topic);
  $('#crumb').innerHTML = `${esc(t(name))}<small>${tp ? esc(tp.name) : ''}</small>`;
  const root = $('#main'); root.innerHTML = '<div class="wrap"><p class="mute">Loading…</p></div>';
  try { await views[S.view](root); } catch (e) { root.innerHTML = `<div class="wrap"><div class="card">Something went wrong: ${esc(e.message)}</div></div>`; }
}
async function loadTopics() {
  S.topics = await api('/topics');
  if (!S.topics.find(x => x.id === S.topic)) S.topic = store.get('sb_topic', null);
  if (!S.topics.find(x => x.id === S.topic)) S.topic = S.topics[0]?.id ?? null;
  $('#topicSel').innerHTML = S.topics.length ? S.topics.map(x => `<option value="${x.id}" ${x.id === S.topic ? 'selected' : ''}>${esc(x.name)}</option>`).join('') : '<option>(no topic yet)</option>';
}
$('#topicSel').addEventListener('change', e => { S.topic = +e.target.value; store.set('sb_topic', S.topic); render(); });
$('#newTopic').addEventListener('click', () => topicDialog());
function topicDialog(edit) {
  const tp = edit ? S.topics.find(x => x.id === S.topic) : null;
  modal(`<h2>${tp ? 'Edit topic' : 'New topic'}</h2>
    <div class="kv"><label>Name</label><input type="text" id="tn" value="${esc(tp?.name || '')}" placeholder="e.g. Organic chemistry">
    <label>Goal</label><input type="text" id="tg" value="${esc(tp?.goal || '')}" placeholder="e.g. Pass the exam / become an expert in diagnosis and therapy">
    <label>Search keywords</label><input type="text" id="tk" value="${esc(tp?.keywords || '')}" placeholder="optional, for Recommend, e.g. project risk management"></div>
    <p class="mute small">The goal is shown to the AI so summaries and questions aim at what you want to achieve.</p>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="tok">Save</button><button class="btn" onclick="closeModal()">Cancel</button>
    ${tp ? '<button class="btn danger" id="tdel" style="margin-left:auto">Delete topic…</button>' : ''}</div>`);
  $('#tok').onclick = guard(async () => {
    const body = { name: $('#tn').value, goal: $('#tg').value, keywords: $('#tk').value };
    const r = tp ? await api(`/topics/${tp.id}`, { method: 'PATCH', body }) : await api('/topics', { method: 'POST', body });
    closeModal(); S.topic = r.id; store.set('sb_topic', S.topic); await loadTopics(); render();
  });
  if (tp) $('#tdel').onclick = guard(async () => {
    if (!confirm(`Delete "${tp.name}" with all its documents, summaries and exercises? This cannot be undone.`)) return;
    await api(`/topics/${tp.id}`, { method: 'DELETE' }); closeModal(); S.topic = null; await loadTopics(); render();
  });
}
const needTopic = root => { if (S.topic) return false;
  root.innerHTML = `<div class="wrap"><div class="empty"><h1>Welcome to StudyBench</h1><p>Create a topic to start: one topic per course or subject.</p>
  <p><button class="btn primary" onclick="topicDialog()">Create a topic</button></p>
</div></div>`;
  return true; };

/* ------------------------------------------------------------------ background jobs */
const seen = {}; let pollTimer = null;
async function pollJobs() {
  try {
    const js = await api('/jobs'); S.jobs = js; let refresh = false;
    try { S.setup = await api('/setup'); } catch { S.setup = []; }
    for (const j of js) {
      const prev = seen[j.id]; seen[j.id] = j.status;
      if (prev && prev !== j.status && ['done', 'error', 'cancelled'].includes(j.status)) {
        if (j.status === 'error') toast(`${j.label}: ${j.error}`, true);
        else if (j.status === 'done') toast(`${j.label}: finished`);
        refresh = true; j.finishedAt = Date.now();
      } else if (!prev && ['done', 'error', 'cancelled'].includes(j.status) && Date.now() - S.expect < 30000) {
        if (j.status === 'error') toast(`${j.label}: ${j.error}`, true); else if (j.status === 'done') toast(`${j.label}: finished`);
        refresh = true; S.expect = 0;                       // fast job finished before the first poll
      }
    }
    drawJobs();
    if (refresh && !S.busy && (S.view !== 'practice' || S.sub === 'bank') && !['chat', 'settings', 'focus'].includes(S.view)) render();
  } catch { /* server restarting */ }
  const active = S.jobs.some(j => ['queued', 'running'].includes(j.status)) || (S.setup || []).some(x => x.status === 'running');
  clearTimeout(pollTimer); pollTimer = setTimeout(pollJobs, active ? 1200 : 5000);
}
const finishedAt = {};
function drawJobs() {
  const now = Date.now();
  const vis = S.jobs.filter(j => { if (['queued', 'running'].includes(j.status)) return true; finishedAt[j.id] ??= now; return j.status === 'error' ? now - finishedAt[j.id] < 60000 : now - finishedAt[j.id] < 6000; });
  const su = (S.setup || []).filter(x => x.status === 'running' || (x.status === 'error' && Date.now() / 1000 - x.ts < 600));
  $('#jobs').innerHTML = su.map(x => `<div class="job ${x.status === 'error' ? 'error' : ''}"><b>${esc(x.label)}</b><br><span class="mute">${esc(x.msg || '')}${x.pct != null && x.status === 'running' ? ' ' + x.pct + '%' : ''}</span>
    ${x.status === 'running' ? `<div class="bar"><i style="${x.pct != null ? 'width:' + x.pct + '%' : 'width:100%;opacity:.4'}"></i></div>` : ''}</div>`).join('') + vis.map(j => `<div class="job ${j.status}"><button data-c="${j.id}" title="Cancel">✕</button><b>${esc(j.label)}</b><br>
    <span class="mute">${j.status === 'running' ? esc(j.message || 'working…') : j.status === 'error' ? esc(j.error) : j.status}</span>
    ${j.status === 'running' ? `<div class="bar"><i style="width:${Math.round(j.progress * 100)}%"></i></div>` : ''}</div>`).join('');
}
$('#jobs').addEventListener('click', e => { const b = e.target.closest('[data-c]'); if (b) api(`/jobs/${b.dataset.c}/cancel`, { method: 'POST' }); });
function track() { S.expect = Date.now(); clearTimeout(pollTimer); pollJobs(); }
async function waitJob(job, onProgress) {
  for (;;) {
    const js = await api('/jobs'); const j = js.find(x => x.id === job.id);
    if (j) { if (onProgress) onProgress(j); if (j.status === 'done') return j.result; if (j.status === 'error') throw new Error(j.error); if (j.status === 'cancelled') throw new Error('Cancelled'); }
    await sleep(900);
  }
}

/* ------------------------------------------------------------------ focus timer (focus -> recall -> break) */
const TIPS = [
  ['Sleep', 'Sleep is when new memories are consolidated. Protect 7-9 hours; a short review of the hardest items before bed, then a full night, beats an all-nighter before an exam.'],
  ['Water', 'Drink a glass of water at every break. Mild dehydration makes concentration and mood worse, and it is the easiest thing to fix.'],
  ['Food', 'Eat regular meals with protein and slow-release carbohydrates (oats, whole grain, legumes, nuts). Avoid studying on an empty stomach or right after a heavy meal.'],
  ['Move', 'Stand up and walk for two or three minutes every break, ideally near a window. Light movement restores attention better than scrolling your phone.'],
  ['Caffeine', 'Coffee or tea helps most when used sparingly and early; stop 6-8 hours before bed so it does not cost you the sleep your memory needs.'],
  ['Eyes', 'Look at something far away for 20 seconds every 20 minutes to rest your eyes.'],
  ['Breathe', 'Before an exam-style session, take five slow breaths (longer out than in). A calm start improves recall.'],
  ['Phone', 'Put the phone in another room during focus blocks. Even a silent phone on the desk costs attention.'],
];
const PH = { focus: 'Focus', recall: 'Recall', break: 'Break' };
let TM = store.get('sb_timer', { phase: 'idle' }); let tickTimer = null;
function tmSave() { store.set('sb_timer', TM); }
function beep() {
  if (!S.settings?.focus?.sound) return;
  try { const c = new (window.AudioContext || window.webkitAudioContext)(); [660, 880].forEach((f, i) => { const o = c.createOscillator(), g = c.createGain(); o.frequency.value = f; o.connect(g); g.connect(c.destination); g.gain.setValueAtTime(.15, c.currentTime + i * .25); g.gain.exponentialRampToValueAtTime(.001, c.currentTime + i * .25 + .22); o.start(c.currentTime + i * .25); o.stop(c.currentTime + i * .25 + .25); }); } catch { /* no audio */ }
}
function notify(msg) { try { if (Notification.permission === 'granted') new Notification('StudyBench', { body: msg }); } catch { /* unsupported */ } }
function tmStart(phase, mins) {
  const f = S.settings.focus; TM.cycle = TM.cycle || 0;
  if (!mins) mins = phase === 'focus' ? f.focus_min : phase === 'recall' ? 3 : (TM.cycle % f.cycles === 0 ? f.long_break_min : f.break_min);
  Object.assign(TM, { phase, mins, endsAt: Date.now() + mins * 60000, topic: TM.topic || S.topic }); tmSave(); drawTimer();
  if (phase === 'break') { const tip = TIPS[Math.floor(Math.random() * TIPS.length)]; toast(`${tip[0]}: ${tip[1]}`); }
}
function tmStop(log = true) {
  if (TM.phase === 'focus' && log) { const el = (Date.now() - (TM.endsAt - TM.mins * 60000)) / 60000; if (el >= 3) api('/focus', { method: 'POST', body: { topic_id: TM.topic, minutes: Math.round(el * 10) / 10 } }).catch(() => {}); }
  TM = { phase: 'idle', cycle: TM.cycle || 0 }; tmSave(); drawTimer();
}
function tmEnd() {
  const f = S.settings.focus; beep();
  if (TM.phase === 'focus') {
    api('/focus', { method: 'POST', body: { topic_id: TM.topic, minutes: TM.mins } }).catch(() => {}); TM.cycle = (TM.cycle || 0) + 1;
    notify('Focus block finished. Close your notes.');
    if (f.recall_pause) {
      tmStart('recall', 3);
      modal(`<h2>Focus block done ✔</h2><p>Now the most effective minute of the session: <b>close your notes</b> and write down everything you remember, on paper or on screen. A 3-minute recall timer is running.</p>
        <div class="row"><button class="btn primary" onclick="closeModal();go('practice','recall')">Open recall page</button><button class="btn" onclick="closeModal()">I'll use paper</button><button class="btn ghost" onclick="closeModal();tmStart('break')">Skip to break</button></div>`);
    } else { tmStart('break'); }
  } else if (TM.phase === 'recall') {
    notify('Recall time is over. Take a break.'); tmStart('break');
    modal(`<h2>Recall over</h2><p>Now check your notes against the source (or use <b>Practice → Recall</b> to compare), then rest. Your break has started.</p><div class="tip"><b>${esc(TIPS[1][0])}:</b> ${esc(TIPS[1][1])}</div><button class="btn primary" onclick="closeModal()">OK</button>`);
  } else if (TM.phase === 'break') {
    notify('Break over.'); TM = { phase: 'idle', cycle: TM.cycle }; tmSave(); drawTimer();
    modal(`<h2>Break over</h2><p>Ready for the next focus block?</p><div class="row"><button class="btn primary" onclick="closeModal();tmStart('focus')">Start focus</button><button class="btn" onclick="closeModal()">Not now</button></div>`);
  }
}
function drawTimer() {
  const el = $('#timer'); el.className = TM.phase;
  if (TM.phase === 'idle') {
    el.innerHTML = `<span class="ph">Focus ${S.settings?.focus?.focus_min || 25}/${S.settings?.focus?.break_min || 5}</span><button class="btn sm primary" id="tmGo">▶ Start</button>`;
    $('#tmGo').onclick = () => { try { Notification.requestPermission?.(); } catch { /* ignore */ } tmStart('focus'); };
  } else {
    el.innerHTML = `<span class="ph">${PH[TM.phase]}</span><span class="t" id="tmT">${fmtMMSS(TM.endsAt - Date.now())}</span><button class="btn sm" id="tmX">Stop</button>`;
    $('#tmX').onclick = () => tmStop();
  }
}
function tick() { if (TM.phase === 'idle') return; const left = TM.endsAt - Date.now(); if (left <= 0) tmEnd(); else { const e = $('#tmT'); if (e) e.textContent = fmtMMSS(left); } }
setInterval(tick, 500);

/* ------------------------------------------------------------------ 1 Inventory */
const PRIO = { 1: 'P1 · top', 2: 'P2', 3: 'P3', 4: 'P4', 5: 'P5 · background' };
views.inventory = async root => {
  if (needTopic(root)) return;
  S.docs = await api(`/topics/${S.topic}/docs`);
  const tp = S.topics.find(x => x.id === S.topic);
  root.innerHTML = `<div class="wrap"><h1>Inventory</h1><p class="sub">Add everything for <b>${esc(tp.name)}</b>${tp.goal ? ` — goal: ${esc(tp.goal)}` : ''} (<a href="#" id="editTopic">edit</a>). Filter, prioritise, tag and put the files in the order you want to study them.</p>
  <div class="card"><div class="drop" id="drop"><b>Drop files here</b> or click to choose<br><span class="small">Allowed types: ${esc(S.settings.allowed_extensions.split(/[\s,]+/).filter(Boolean).join(' · '))} <a href="#/settings" style="text-decoration:underline">change</a></span>
  <input type="file" id="file" multiple hidden accept="${S.settings.allowed_extensions.split(/[\s,]+/).filter(Boolean).map(e => '.' + e).join(',')}"></div>
  <div class="row" style="margin-top:12px"><input type="text" id="folder" placeholder="…or paste a folder path, e.g. C:\\Users\\me\\Courses or /Users/me/Courses" style="flex:1;min-width:260px">
  <label class="small"><input type="checkbox" id="copy" checked> copy files (uncheck to link in place; saves disk for big recordings)</label>
  <button class="btn" id="imp">Import folder</button></div></div>
  <div id="invbody"></div></div>`;
  $('#editTopic').onclick = e => { e.preventDefault(); topicDialog(true); };
  const drop = $('#drop'), file = $('#file');
  drop.onclick = () => file.click();
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => upload(e.dataTransfer.files)); file.onchange = () => upload(file.files);
  $('#imp').onclick = guard(async () => { const r = await api(`/topics/${S.topic}/import-folder`, { method: 'POST', body: { path: $('#folder').value, copy: $('#copy').checked } }); toast('Import started (only allowed file types are imported)'); track(); });
  drawInv();
};
const upload = guard(async files => {
  if (!files.length) return; const fd = new FormData(); [...files].forEach(f => fd.append('files', f));
  toast(`Uploading ${files.length} file(s)…`); const r = await api(`/topics/${S.topic}/upload`, { method: 'POST', body: fd });
  const sk = r.filter(x => x.skipped).length; toast(`${r.filter(x => !x.duplicate && !x.skipped).length} added, ${r.filter(x => x.duplicate).length} duplicate(s)${sk ? `, ${sk} ignored (file type not allowed in Settings)` : ''}`); render();
});
function drawInv() {
  const f = S.inv, all = S.docs, box = $('#invbody');
  const cats = [...new Set(all.map(d => d.category).filter(Boolean))].sort();
  if (!all.length) { box.innerHTML = '<div class="empty">No files yet. Drop your first files above.</div>'; return; }
  const rows = all.filter(d => (!f.q || (d.filename + ' ' + d.tags.join(' ') + ' ' + d.category + ' ' + d.note).toLowerCase().includes(f.q.toLowerCase()))
    && (!f.kind || d.kind === f.kind) && (!f.status || d.status === f.status) && (!f.cat || d.category === f.cat) && (!f.prio || d.priority == f.prio));
  const kinds = [...new Set(all.map(d => d.kind))], sts = [...new Set(all.map(d => d.status))];
  const opt = (v, cur, label) => `<option value="${esc(v)}" ${String(cur) === String(v) ? 'selected' : ''}>${esc(label ?? v)}</option>`;
  const pending = all.filter(d => d.status === 'pending_transcription').length;
  box.innerHTML = `<div class="card"><div class="row">
    <input type="text" id="fq" placeholder="Search name, tag, category…" value="${esc(f.q)}" style="min-width:220px">
    <select id="fk"><option value="">All types</option>${kinds.map(k => opt(k, f.kind, KIND[k] || k)).join('')}</select>
    <select id="fs"><option value="">All states</option>${sts.map(k => opt(k, f.status)).join('')}</select>
    <select id="fc"><option value="">All categories</option>${cats.map(k => opt(k, f.cat)).join('')}</select>
    <select id="fp"><option value="">All priorities</option>${[1, 2, 3, 4, 5].map(k => opt(k, f.prio, PRIO[k])).join('')}</select>
    <span class="mute small">${rows.length} of ${all.length}</span></div>
    <div class="row" style="margin-top:10px"><span class="small mute">Order:</span>
    <button class="btn sm" data-sort="priority">by priority</button><button class="btn sm" data-sort="name">by name</button><button class="btn sm" data-sort="kind">by type</button>
    <span style="flex:1"></span><button class="btn sm" id="autotag" title="Uses your AI to suggest category, tags and priority. Manual values are kept.">✨ Suggest tags &amp; priority</button></div>
    <div class="row" id="bulk" style="margin-top:10px;display:none"><b class="small" id="bulkn"></b>
    <select id="bp"><option value="">Priority…</option>${[1, 2, 3, 4, 5].map(k => opt(k, '', PRIO[k])).join('')}</select>
    <input type="text" id="bc" placeholder="Category…" list="cats" style="width:150px"><button class="btn sm primary" id="bapply">Apply</button>
    <button class="btn sm" id="binc">Include</button><button class="btn sm" id="bexc">Exclude</button><button class="btn sm danger" id="bdel">Delete</button></div>
    <datalist id="cats">${cats.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
    ${pending ? `<div class="disclaimer">🎙 ${pending} recording(s) waiting for transcription. <a href="#/transcribe">Go to Transcribe →</a></div>` : ''}
    <div class="card" style="padding:4px 8px;overflow:auto"><table><thead><tr><th><input type="checkbox" id="all"></th><th title="Use in chat, summaries and exercises">Use</th><th>#</th><th>File</th><th>Category</th><th>Tags</th><th>Priority</th><th>Size</th><th></th></tr></thead><tbody>
    ${rows.map(d => { const i = all.indexOf(d); return `<tr data-id="${d.id}" class="${d.include ? '' : 'off'}">
      <td><input type="checkbox" class="sel"></td><td><input type="checkbox" class="inc" ${d.include ? 'checked' : ''}></td>
      <td style="white-space:nowrap"><button class="btn sm ghost" data-mv="-1" ${i === 0 ? 'disabled' : ''}>▲</button><button class="btn sm ghost" data-mv="1" ${i === all.length - 1 ? 'disabled' : ''}>▼</button></td>
      <td><a href="#" class="view"><b>${esc(d.filename)}</b></a><br><span class="badge">${esc(KIND[d.kind] || d.kind)}</span> <span class="badge ${esc(d.status)}">${esc(d.status.replace('_', ' '))}</span>
        ${d.has_summary ? '<span class="badge ready">summary</span>' : ''}${d.n_items ? `<span class="badge">${d.n_items} Q</span>` : ''}<span class="mute small"> ${esc(d.status_msg)}</span></td>
      <td><input type="text" class="cat" list="cats" value="${esc(d.category)}"></td>
      <td><input type="text" class="tags" value="${esc(d.tags.join(', '))}"></td>
      <td><select class="prio">${[1, 2, 3, 4, 5].map(k => opt(k, d.priority, PRIO[k])).join('')}</select></td>
      <td class="small mute">${d.kind === 'audio' ? fmtDur(d.duration) : d.text_len ? (d.text_len < 1000 ? d.text_len + ' chars' : Math.round(d.text_len / 1000) + 'k chars') : '–'}</td>
      <td><button class="btn sm ghost danger del" title="Delete">🗑</button></td></tr>`; }).join('')}</tbody></table></div>`;
  const rd = () => { S.inv = { q: $('#fq').value, kind: $('#fk').value, status: $('#fs').value, cat: $('#fc').value, prio: $('#fp').value }; };
  $('#fq').oninput = () => { rd(); const p = $('#fq').selectionStart; drawInv(); const q = $('#fq'); q.focus(); q.setSelectionRange(p, p); };
  ['fk', 'fs', 'fc', 'fp'].forEach(id => $('#' + id).onchange = () => { rd(); drawInv(); });
  $$('[data-sort]').forEach(b => b.onclick = guard(async () => {
    const k = b.dataset.sort, srt = [...all].sort((a, c) => k === 'priority' ? a.priority - c.priority || a.seq - c.seq : k === 'name' ? a.filename.localeCompare(c.filename, undefined, { numeric: true }) : (a.kind + a.filename).localeCompare(c.kind + c.filename, undefined, { numeric: true }));
    await api(`/topics/${S.topic}/reorder`, { method: 'POST', body: { ids: srt.map(d => d.id) } }); render();
  }));
  $('#autotag').onclick = guard(async () => { const ids = selected(); await api(`/topics/${S.topic}/autotag`, { method: 'POST', body: { doc_ids: ids.length ? ids : undefined } }); toast('Asking the AI…'); track(); });
  const selected = () => $$('tr[data-id]', box).filter(r => $('.sel', r).checked).map(r => +r.dataset.id);
  const updBulk = () => { const n = selected().length; $('#bulk').style.display = n ? 'flex' : 'none'; $('#bulkn').textContent = `${n} selected`; };
  $('#all').onchange = e => { $$('.sel', box).forEach(c => c.checked = e.target.checked); updBulk(); };
  box.addEventListener('change', e => { if (e.target.classList.contains('sel')) updBulk(); });
  const patch = (id, body) => api(`/docs/${id}`, { method: 'PATCH', body });
  $('#bapply').onclick = guard(async () => { for (const id of selected()) { const b = {}; if ($('#bp').value) b.priority = +$('#bp').value; if ($('#bc').value) b.category = $('#bc').value; await patch(id, b); } render(); });
  $('#binc').onclick = guard(async () => { for (const id of selected()) await patch(id, { include: true }); render(); });
  $('#bexc').onclick = guard(async () => { for (const id of selected()) await patch(id, { include: false }); render(); });
  $('#bdel').onclick = guard(async () => { const ids = selected(); if (!confirm(`Delete ${ids.length} file(s)? Exercises made from them are kept.`)) return; for (const id of ids) await api(`/docs/${id}`, { method: 'DELETE' }); render(); });
  $$('tr[data-id]', box).forEach(tr => {
    const id = +tr.dataset.id;
    $('.inc', tr).onchange = guard(async e => { await patch(id, { include: e.target.checked }); tr.classList.toggle('off', !e.target.checked); });
    $('.cat', tr).onchange = guard(e => patch(id, { category: e.target.value }));
    $('.tags', tr).onchange = guard(e => patch(id, { tags: e.target.value }));
    $('.prio', tr).onchange = guard(e => patch(id, { priority: +e.target.value }));
    $('.view', tr).onclick = e => { e.preventDefault(); viewDoc(id); };
    $('.del', tr).onclick = guard(async () => { if (!confirm('Delete this file? Exercises made from it are kept.')) return; await api(`/docs/${id}`, { method: 'DELETE' }); render(); });
    $$('[data-mv]', tr).forEach(b => b.onclick = guard(async () => {
      const ids = all.map(d => d.id), i = ids.indexOf(id), j = i + +b.dataset.mv; [ids[i], ids[j]] = [ids[j], ids[i]];
      await api(`/topics/${S.topic}/reorder`, { method: 'POST', body: { ids } }); render();
    }));
  });
}
const viewDoc = guard(async (id, startEdit) => {
  const d = await api(`/docs/${id}/text`);
  const draw = edit => {
    modal(`<div class="row sp"><h2 style="margin:0">${esc(d.filename)}</h2><button class="btn sm" onclick="closeModal()">Close</button></div>
      ${d.text ? (edit ? `<textarea id="dt" style="min-height:55vh;margin-top:10px">${esc(d.text)}</textarea><div class="row" style="margin-top:10px"><button class="btn primary" id="dsave">Save corrections</button><button class="btn" id="dcancel">Cancel</button></div>`
        : `<div class="md" style="margin-top:10px">${md(d.text)}</div><div class="row" style="margin-top:10px"><button class="btn" id="dedit">Edit text</button><a class="btn" href="/api/docs/${id}/file">Download original</a></div>`)
        : `<p class="mute">No text yet (${esc(d.status)}).</p>`}`);
    if (!edit && d.text) $('#dedit').onclick = () => draw(true);
    if (edit) { $('#dcancel').onclick = () => draw(false); $('#dsave').onclick = guard(async () => { d.text = $('#dt').value; await api(`/docs/${id}/text`, { method: 'PUT', body: { text: d.text } }); toast('Saved'); draw(false); }); }
  };
  draw(startEdit);
});

/* ------------------------------------------------------------------ 2 Transcribe */
views.transcribe = async root => {
  if (needTopic(root)) return;
  const [docs, st] = await Promise.all([api(`/topics/${S.topic}/docs`), api('/transcribe/status')]);
  const audio = docs.filter(d => d.kind === 'audio'), trs = docs.filter(d => d.kind === 'transcript');
  const targets = docs.filter(d => !['audio', 'transcript'].includes(d.kind) && d.text_len > 0);
  const w = S.settings.whisper;
  const linkSel = (cur, cls) => `<select class="${cls}"><option value="">not linked</option>${targets.map(x => `<option value="${x.id}" ${x.id === cur ? 'selected' : ''}>${esc(x.filename)}</option>`).join('')}</select>`;
  root.innerHTML = `<div class="wrap"><h1>Transcribe</h1><p class="sub">Turn recordings into text on your own computer. A transcript can be linked to the slides it belongs to; it then works like any other document.</p>
  ${st.available ? '' : `<div class="disclaimer"><b>Speech-to-text is not installed.</b> Close the app and run <code>pip install faster-whisper</code> in the StudyBench folder (first use downloads the model, then it works offline). You can still use everything else.</div>`}
  ${!audio.length ? '<div class="card empty">No recordings in this topic. Upload audio/video files in <a href="#/inventory">Inventory</a>.</div>' : `
  <div class="card"><div class="row"><label>Language <select id="tl"><option value="auto">auto-detect</option>${['de', 'en', 'fr', 'ar'].map(l => `<option ${w.language === l ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
  <label>Model <select id="tm">${['tiny', 'base', 'small', 'medium', 'large-v3'].map(m => `<option ${w.model === m ? 'selected' : ''}>${m}</option>`).join('')}</select></label>
  <span class="mute small">small = fast on CPU · medium/large-v3 = better with accents and technical terms, slower (GPU recommended)</span></div>
  <div class="row" style="margin-top:10px"><button class="btn sm" id="tsel">Select all</button><button class="btn sm" id="tnone">Deselect all</button><button class="btn sm" id="tpend">Only not yet transcribed</button></div>
  <table style="margin-top:10px"><thead><tr><th><input type="checkbox" id="tall"></th><th>Recording</th><th>Status</th><th>Link transcript to</th></tr></thead><tbody>
  ${audio.map(d => { const tr = trs.find(x => x.parent_id === d.id); return `<tr data-id="${d.id}"><td><input type="checkbox" class="sel" ${d.status === 'pending_transcription' ? 'checked' : ''}></td>
    <td><b>${esc(d.filename)}</b><br><span class="mute small">${d.duration ? fmtDur(d.duration) : ''}</span></td>
    <td><span class="badge ${esc(d.status)}">${esc(d.status.replace('_', ' '))}</span>${tr ? ` <a href="#" class="vt" data-id="${tr.id}">view / correct</a>` : ''}</td>
    <td>${linkSel(tr?.linked_to, 'lnk')}</td></tr>`; }).join('')}</tbody></table>
  <div class="row" style="margin-top:12px"><button class="btn primary" id="go" ${st.available ? '' : 'disabled'}>Transcribe selected</button><span class="mute small">Runs in the background, one at a time. Long recordings take a while; keep working.</span></div></div>`}
  ${trs.length ? `<h2>Transcripts</h2><div class="card"><table><tbody>${trs.map(x => `<tr><td><b>${esc(x.filename)}</b><br><span class="mute small">${esc(x.status_msg)}</span></td><td>${linkSel(x.linked_to, 'tlnk').replace('<select', `<select data-id="${x.id}"`)}</td><td><button class="btn sm vt" data-id="${x.id}">View / correct</button></td></tr>`).join('')}</tbody></table>
  <p class="mute small">Tip: fix names and drug terms once in the transcript; summaries, chat and questions then use the corrected text. Add specialist words in Settings → glossary to improve future transcripts.</p></div>` : ''}</div>`;
  $$('.vt', root).forEach(a => a.onclick = e => { e.preventDefault(); viewDoc(+a.dataset.id); });
  $$('.tlnk', root).forEach(s => s.onchange = guard(() => api(`/docs/${s.dataset.id}`, { method: 'PATCH', body: { linked_to: s.value ? +s.value : null } })));
  const setSel = f => { $$('tr[data-id]', root).forEach(r => { const c = $('.sel', r); if (c) c.checked = f(r); }); const all = $('#tall'); if (all) all.checked = $$('tr[data-id] .sel', root).every(c => c.checked); };
  if ($('#tsel')) { $('#tsel').onclick = () => setSel(() => true); $('#tnone').onclick = () => setSel(() => false);
    $('#tpend').onclick = () => setSel(r => /pending/.test($('.badge', r).className)); $('#tall').onchange = e => setSel(() => e.target.checked); }
  const go = $('#go'); if (go) go.onclick = guard(async () => {
    const items = $$('tr[data-id]', root).filter(r => $('.sel', r)?.checked).map(r => ({ doc_id: +r.dataset.id, link_to: +$('.lnk', r).value || null }));
    if (!items.length) return toast('Select at least one recording', true);
    await api('/transcribe', { method: 'POST', body: { items, language: $('#tl').value, model: $('#tm').value } }); toast('Queued'); track();
  });
};

/* ------------------------------------------------------------------ 3 Interpret */
views.interpret = async root => {
  if (needTopic(root)) return;
  const [docs, sums] = await Promise.all([api(`/topics/${S.topic}/docs`), api(`/topics/${S.topic}/summaries`)]);
  const ready = docs.filter(d => d.text_len > 0);
  const cur = S.sel && ready.find(d => d.id === S.sel) ? S.sel : ready[0]?.id;
  S.sel = cur;
  const combined = sums.filter(s => s.doc_ids.length > 1);
  const lang = S.settings.content_language;
  root.innerHTML = `<div class="wrap"><h1>Interpret</h1><p class="sub">Practical summaries: the essence, what to do differently, what to memorise, typical pitfalls. Select documents, then summarise one by one or merge them into one summary.</p>
  <div class="disclaimer">AI summaries can contain mistakes. They always cite where a point comes from; check important facts (doses, criteria, legal rules) in the original.</div>
  ${!ready.length ? '<div class="card empty">No documents with text yet.</div>' : `
  <div class="card"><div class="row"><label>Language <select id="sl">${[['de', 'German'], ['en', 'English'], ['fr', 'French'], ['auto', 'as source']].map(([v, n]) => `<option value="${v}" ${v === lang ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
  <label>Detail <select id="sd"><option value="quick">Quick</option><option value="standard" selected>Standard</option><option value="detailed">Detailed</option></select></label>
  <input type="text" id="sf" placeholder="Optional focus, e.g. 'only what changes my daily practice'" style="flex:1;min-width:240px">
  <button class="btn primary" id="s1">Summarise each selected</button><button class="btn" id="s2">Merge selected into one</button></div>
  <p class="mute small" style="margin:8px 0 0">Long documents are summarised section by section, so the result grows with the document: an overview first, then every section. Time left is shown in the sidebar. If you cancel, finished sections are kept and reused next time. “Quick” is about twice as fast as “Detailed”. Setting a smaller “fast model” in Settings speeds long documents up further.</p></div>
  <div style="display:grid;grid-template-columns:340px 1fr;gap:16px" id="two"><div>
  <div class="card list-check" style="max-height:60vh;overflow:auto"><label><input type="checkbox" id="sall"> <b>Select all</b></label>
  ${ready.map(d => `<label><input type="checkbox" class="ds" value="${d.id}"> <a href="#" class="pick ${d.id === cur ? 'on' : ''}" data-id="${d.id}" style="${d.id === cur ? 'font-weight:700' : ''}">${esc(d.filename)}</a> ${d.has_summary ? '<span class="chip">✓</span>' : ''}</label>`).join('')}</div>
  ${combined.length ? `<h3>Merged summaries</h3><div class="card">${combined.map(s => `<div><a href="#" class="pickc" data-id="${s.id}">${esc(s.title)}</a> <span class="mute small">${s.doc_ids.length} docs</span></div>`).join('')}</div>` : ''}</div>
  <div id="sumview"></div></div>`}</div>`;
  if (!ready.length) return;
  const show = list => {
    $('#sumview').innerHTML = list.length ? list.slice(0, 3).map((s, i) => `<div class="card sumcard" data-sid="${s.id}"><div class="sumtools"><button class="btn sm cp">Copy</button><button class="btn sm ed">Edit</button></div><div class="row sp"><b>${esc(s.title)}</b><span class="mute small">${esc(s.created.replace('T', ' '))} · ${esc(s.language)}</span></div>
      <div class="md body">${md(s.content)}</div><div class="row" style="margin-top:8px;clear:both"><button class="btn sm danger rm">Delete</button></div></div>`).join('') : '<div class="card empty">No summary yet. Select this document and press “Summarise”.</div>';
    $$('[data-sid]', $('#sumview')).forEach(c => { const s = sums.find(x => x.id == c.dataset.sid);
      $('.cp', c).onclick = () => navigator.clipboard.writeText(s.content).then(() => toast('Copied'));
      $('.rm', c).onclick = guard(async () => { if (confirm('Delete this summary?')) { await api(`/summaries/${s.id}`, { method: 'DELETE' }); render(); } });
      $('.ed', c).onclick = () => { $('.body', c).outerHTML = `<textarea class="body" style="min-height:300px">${esc(s.content)}</textarea>`; $('.ed', c).textContent = 'Save';
        $('.ed', c).onclick = guard(async () => { await api(`/summaries/${s.id}`, { method: 'PUT', body: { content: $('.body', c).value } }); render(); }); };
    });
  };
  show(sums.filter(s => s.doc_ids.length === 1 && s.doc_ids[0] === cur));
  $$('.pick', root).forEach(a => a.onclick = e => { e.preventDefault(); S.sel = +a.dataset.id; $$('.pick', root).forEach(x => x.style.fontWeight = ''); a.style.fontWeight = '700'; show(sums.filter(s => s.doc_ids.length === 1 && s.doc_ids[0] === S.sel)); });
  $$('.pickc', root).forEach(a => a.onclick = e => { e.preventDefault(); show(sums.filter(s => s.id == a.dataset.id)); });
  $('#sall').onchange = e => $$('.ds', root).forEach(c => c.checked = e.target.checked);
  const ids = () => $$('.ds', root).filter(c => c.checked).map(c => +c.value);
  const run = combine => guard(async () => { const d = ids(); if (!d.length) return toast('Select documents on the left', true);
    await api('/summarize', { method: 'POST', body: { doc_ids: d, combine, language: $('#sl').value, focus: $('#sf').value, level: $('#sd').value } }); toast('Summarising in the background…'); track(); });
  $('#s1').onclick = run(false); $('#s2').onclick = run(true);
};

/* ------------------------------------------------------------------ 5 Chat */
views.chat = async root => {
  if (needTopic(root)) return;
  const [docs, hist] = await Promise.all([api(`/topics/${S.topic}/docs`), api(`/topics/${S.topic}/chat`)]);
  const ready = docs.filter(d => d.text_len > 0);
  const history = hist.map(h => ({ role: h.role, content: h.content, sources: h.sources }));
  root.innerHTML = `<div class="wrap"><h1>Chat</h1><p class="sub">Ask anything. Answers come from your own files and show where they were found. Pick which documents may be used (default: all marked “Use” in Inventory).</p>
  <div class="card"><div class="row"><label><input type="checkbox" id="call" checked> All documents with “Use” ticked (${ready.filter(d => d.include).length})</label>
  <details><summary class="btn sm">Choose documents</summary><div class="row" style="margin:8px 0"><button class="btn sm" id="cadd">Select all</button><button class="btn sm" id="cclr">Deselect all</button></div><div class="list-check" style="max-height:240px;overflow:auto">
  ${ready.map(d => `<label><input type="checkbox" class="cd" value="${d.id}"> ${esc(d.filename)}</label>`).join('')}</div></details>
  <label class="small"><input type="checkbox" id="gen"> allow general knowledge (clearly marked as not from your files)</label>
  <button class="btn sm danger" id="clr" style="margin-left:auto">Clear history</button></div></div>
  <div class="chat" id="log"></div>
  <div class="card"><textarea id="q" placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)" style="min-height:60px"></textarea>
  <div class="row" style="margin-top:8px"><button class="btn primary" id="send">Send</button><span class="mute small" id="cstat"></span></div></div></div>`;
  const log = $('#log');
  const draw = (m, i) => `<div class="msg ${m.role}" data-i="${i}">${m.role === 'assistant' ? md(m.content) : esc(m.content)}${(m.sources || []).map(s => `<span class="src" data-i="${i}" data-n="${s.n}">[${s.n}] ${esc(s.filename)} · ${esc(s.locator || '')}</span>`).join('')}</div>`;
  const redraw = () => { log.innerHTML = history.length ? history.map(draw).join('') : '<div class="card empty">Ask your first question, e.g. “What are the criteria for …?”</div>'; log.lastElementChild?.scrollIntoView({ block: 'end' }); };
  redraw();
  log.onclick = e => { const s = e.target.closest('.src'); if (!s) return; const src = history[+s.dataset.i].sources.find(x => x.n == s.dataset.n);
    modal(`<h2>${esc(src.filename)}</h2><p class="mute">${esc(src.locator || '')}</p><div class="md">${md(src.snippet)}${src.snippet.length >= 500 ? '…' : ''}</div><p><button class="btn" onclick="closeModal()">Close</button> <button class="btn" onclick="closeModal();viewDoc(${src.doc_id})">Open full text</button></p>`); };
  const sync = () => { if ($$('.cd', root).some(c => c.checked)) $('#call').checked = false; };
  $$('.cd', root).forEach(c => c.onchange = sync);
  $('#cadd').onclick = () => { $$('.cd', root).forEach(c => c.checked = true); $('#call').checked = false; };
  $('#cclr').onclick = () => { $$('.cd', root).forEach(c => c.checked = false); $('#call').checked = true; };
  $('#call').onchange = e => { if (e.target.checked) $$('.cd', root).forEach(c => c.checked = false); };
  $('#clr').onclick = guard(async () => { if (confirm('Delete the chat history for this topic?')) { await api(`/topics/${S.topic}/chat`, { method: 'DELETE' }); history.length = 0; redraw(); } });
  let sending = false;
  const send = guard(async () => {
    const msg = $('#q').value.trim(); if (!msg || sending) return;
    if (!$('#call').checked && !$$('.cd', root).some(c => c.checked)) return toast('Choose at least one document, or tick “All documents with Use ticked”.', true); sending = true; $('#q').value = '';
    const past = history.slice(); history.push({ role: 'user', content: msg }); const a = { role: 'assistant', content: '', sources: [] }; history.push(a); redraw();
    $('#cstat').textContent = 'searching your files…'; S.busy = true;
    try {
      const r = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic_id: S.topic, message: msg, history: past, doc_ids: $('#call').checked ? [] : $$('.cd', root).filter(c => c.checked).map(c => +c.value), allow_general: $('#gen').checked }) });
      if (!r.ok) throw new Error((await r.json()).error);
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) { const { value, done } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true });
        let nl; while ((nl = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, nl); buf = buf.slice(nl + 1); if (!line) continue; const ev = JSON.parse(line);
          if (ev.type === 'sources') { a.sources = ev.sources; $('#cstat').textContent = ev.sources.length ? `${ev.sources.length} passages found, writing…` : 'nothing found'; }
          else if (ev.type === 'delta') a.content += ev.text; else if (ev.type === 'error') throw new Error(ev.message);
          const last = log.lastElementChild; if (last) last.outerHTML = draw(a, history.length - 1); } }
    } catch (e) { a.content += `\n\n⚠ ${e.message}`; toast(e.message, true); }
    finally { sending = false; S.busy = false; $('#cstat').textContent = ''; redraw(); }
  });
  $('#send').onclick = send; $('#q').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } };
};

/* 6 Recommend and 7 Present live in extra.js */

/* ------------------------------------------------------------------ Focus & recovery */
views.focus = async root => {
  const f = S.settings.focus; const st = await api(`/stats?tz=${-new Date().getTimezoneOffset()}`);
  const today = st.focus.find(x => x.d === st.today)?.m || 0;
  root.innerHTML = `<div class="wrap"><h1>Focus &amp; recovery</h1><p class="sub">Work in focused blocks, recall without notes, then rest. Memory needs all three.</p>
  <div class="grid"><div class="card"><div class="stat">${Math.round(today)} min</div><div class="mute">focused today</div></div>
  <div class="card"><div class="stat">${st.streak} d</div><div class="mute">study streak</div></div></div>
  <div class="card"><h3 style="margin-top:0">Your focus rhythm</h3><div class="row">
  <label>Focus <input type="number" id="f1" min="5" max="120" value="${f.focus_min}" style="width:70px"> min</label>
  <label>Break <input type="number" id="f2" min="1" max="60" value="${f.break_min}" style="width:70px"> min</label>
  <label>Long break <input type="number" id="f3" min="1" max="90" value="${f.long_break_min}" style="width:70px"> min every <input type="number" id="f4" min="2" max="10" value="${f.cycles}" style="width:60px"> blocks</label></div>
  <div class="row" style="margin-top:8px"><label><input type="checkbox" id="f5" ${f.recall_pause ? 'checked' : ''}> after each focus block: 3-minute recall (notes closed)</label>
  <label><input type="checkbox" id="f6" ${f.sound ? 'checked' : ''}> sound</label><button class="btn primary" id="fs">Save</button></div>
  <p class="mute small">The timer in the top bar keeps running while you move between pages. Suggested rhythm: 25 / 5, or 50 / 10 for deep reading.</p></div>
  <h2>Recovery basics</h2><div class="grid">${TIPS.map(x => `<div class="tip"><b>${x[0]}</b><br>${x[1]}</div>`).join('')}</div></div>`;
  $('#fs').onclick = guard(async () => {
    S.settings = await api('/settings', { method: 'POST', body: { focus: { focus_min: +$('#f1').value, break_min: +$('#f2').value, long_break_min: +$('#f3').value, cycles: +$('#f4').value, recall_pause: $('#f5').checked, sound: $('#f6').checked } } });
    drawTimer(); toast('Saved');
  });
};

/* ------------------------------------------------------------------ Progress */
views.progress = async root => {
  const st = await api(`/stats?tz=${-new Date().getTimezoneOffset()}`);
  const maxN = Math.max(1, ...st.reviews.map(r => r.n));
  root.innerHTML = `<div class="wrap"><h1>Progress</h1><p class="sub">Streak: <b>${st.streak}</b> day(s). Topics are listed in your priority order.</p>
  ${st.topics.length ? `<div class="card" style="overflow:auto"><table><thead><tr><th>Topic</th><th>Files</th><th>Summaries</th><th>Questions</th><th>Due now</th><th>Weak</th><th>Mastered</th><th>Accuracy 7 d</th></tr></thead><tbody>
  ${st.topics.map(x => `<tr><td><b>${esc(x.name)}</b>${x.audio_pending ? `<br><span class="badge new">${x.audio_pending} recording(s) not transcribed</span>` : ''}</td><td>${x.ready}/${x.docs}</td><td>${x.summaries}</td><td>${x.items}${x.new ? ` <span class="mute small">(${x.new} new)</span>` : ''}</td>
  <td>${x.due ? `<b>${x.due}</b>` : '0'}</td><td>${x.weak ? `<span class="badge error">${x.weak}</span>` : '0'}</td>
  <td>${x.items ? Math.round(100 * x.mature / x.items) + '%' : '–'}</td><td>${x.acc7 === null ? '–' : x.acc7 + '%'}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No topics yet.</div>'}
  <h2>Reviews, last 14 days</h2><div class="card">${st.reviews.length ? `<div class="minibars">${st.reviews.map(r => `<div style="height:${Math.round(100 * r.n / maxN)}%" title="${r.d}: ${r.n} reviews, ${r.ok} good"></div>`).join('')}</div><p class="mute small">Each bar is one day. “Mastered” = items whose next review is 3+ weeks away.</p>` : '<p class="mute">No reviews yet. Start in Practice → Review.</p>'}</div></div>`;
};

/* ------------------------------------------------------------------ Settings */
views.settings = async root => {
  const s = S.settings = await api('/settings'); let models = [];
  if (s.provider === 'ollama') { try { models = (await api('/llm/models')).models; } catch { /* offline */ } }
  const o = (v, c) => `<option value="${v}" ${v === c ? 'selected' : ''}>${v}</option>`;
  root.innerHTML = `<div class="wrap"><h1>Settings</h1><p class="sub">Interface language: English (more languages later). Content can be German, English or French.</p>
  <div class="card"><h3 style="margin-top:0">Appearance</h3><div class="row">${[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(([v, n]) => `<label><input type="radio" name="ap" value="${v}" ${v === (s.appearance || 'system') ? 'checked' : ''}> ${n}</label>`).join('')}</div></div>
  <div class="card"><h3 style="margin-top:0">AI engine</h3><div class="kv">
  <label>Provider</label><select id="pv">${[['ollama', 'Ollama (local, private)'], ['anthropic', 'Claude (API key)'], ['openai', 'OpenAI-compatible (API key)'], ['demo', 'Demo (no AI, sample output)']].map(([v, n]) => `<option value="${v}" ${v === s.provider ? 'selected' : ''}>${n}</option>`).join('')}</select>
  <div id="pcfg" style="grid-column:1/-1"></div>
  <label>Content language</label><select id="cl">${[['de', 'German'], ['en', 'English'], ['fr', 'French'], ['auto', 'Same as source']].map(([v, n]) => `<option value="${v}" ${v === s.content_language ? 'selected' : ''}>${n}</option>`).join('')}</select>
  <label>Text per prompt</label><span><input type="number" id="cc" value="${s.context_chars}" step="1000" min="3000" max="60000" style="width:100px"> characters <span class="mute small">(8000-12000 for local models; more for Claude/GPT)</span></span></div>
  <div class="disclaimer" id="priv" style="margin-top:12px"></div>
  <div class="row"><button class="btn primary" id="save">Save</button><button class="btn" id="test">Test connection</button>${s.provider === 'ollama' ? '<button class="btn" id="dl">Download model</button>' : ''}<span id="tres" class="small"></span></div></div>
  <div class="card"><h3 style="margin-top:0">Accepted file types</h3><p class="mute small" style="margin-top:0">Only these extensions are accepted when you upload or import a folder. Everything else is ignored.</p>
  <textarea id="ae" style="min-height:70px">${esc(s.allowed_extensions)}</textarea><div class="row" style="margin-top:6px"><button class="btn sm" id="aer">Reset to default</button></div></div>
  <div class="card"><h3 style="margin-top:0">Speech-to-text</h3><div class="kv"><label>Default model</label><select id="wm">${['tiny', 'base', 'small', 'medium', 'large-v3'].map(m => o(m, s.whisper.model)).join('')}</select>
  <label>Processor</label><span><select id="wd">${[['auto', 'automatic (GPU if it works, else CPU)'], ['cpu', 'CPU only'], ['cuda', 'NVIDIA GPU']].map(([v, n]) => `<option value="${v}" ${v === (s.whisper.device || 'auto') ? 'selected' : ''}>${n}</option>`).join('')}</select></span>
  <label>Default language</label><select id="wl">${['auto', 'de', 'en', 'fr', 'ar'].map(m => o(m, s.whisper.language)).join('')}</select>
  <label>Glossary</label><textarea id="wg" style="min-height:70px" placeholder="Specialist words to help recognition, e.g. names, technical terms and abbreviations from your subject">${esc(s.whisper.glossary)}</textarea></div></div>
  <div class="card" id="loc"></div>
  <div class="card"><h3 style="margin-top:0">Practice</h3><div class="row"><label>Cards per session <input type="number" id="ps" value="${s.practice.session_size}" min="5" max="100" style="width:70px"></label>
  <label>New cards per session <input type="number" id="pn" value="${s.practice.new_per_session}" min="0" max="50" style="width:70px"></label>
  <label>Think first (seconds before “Show answer”) <input type="number" id="pt" value="${s.practice.think_delay}" min="0" max="30" style="width:60px"></label></div></div></div>`;
  const cfg = () => {
    const p = $('#pv').value;
    $('#pcfg').innerHTML = p === 'ollama' ? `<div class="kv"><label>Server</label><input type="text" id="oh" value="${esc(s.ollama.host)}"><label>Model</label><span><input type="text" id="om" list="ml" value="${esc(s.ollama.model)}" style="width:240px"><datalist id="ml">${models.map(m => `<option value="${esc(m)}">`).join('')}</datalist> <span class="mute small">${models.length ? 'installed: ' + esc(models.join(', ')) : 'install from ollama.com, then e.g. <code>ollama pull qwen2.5:14b</code>'}</span></span>
      <label>Fast model</label><span><input type="text" id="ofm" list="ml" value="${esc(s.ollama.fast_model || '')}" placeholder="optional, e.g. llama3.2:3b" style="width:240px"> <span class="mute small">used for the many steps of long documents; empty = same model</span></span>
      <label>Context window</label><span><input type="number" id="on" value="${s.ollama.num_ctx}" step="1024" style="width:100px"> tokens</span></div>`
      : p === 'anthropic' ? `<div class="kv"><label>API key</label><input type="password" id="ak" value="${esc(s.anthropic.api_key)}" placeholder="sk-ant-…"><label>Model</label><input type="text" id="am" value="${esc(s.anthropic.model)}"></div>`
      : p === 'openai' ? `<div class="kv"><label>API key</label><input type="password" id="ok" value="${esc(s.openai.api_key)}"><label>Base URL</label><input type="text" id="ou" value="${esc(s.openai.base_url)}"><label>Model</label><input type="text" id="omm" value="${esc(s.openai.model)}"></div>`
      : '<p class="mute">Demo mode returns canned sample output so you can try the screens without any AI.</p>';
    $('#priv').innerHTML = p === 'ollama' ? '🔒 Everything stays on this computer.' : p === 'demo' ? 'Demo mode: nothing is sent anywhere.' : '⚠ Text from your documents and questions is sent to the provider you chose. Do not include personal or confidential data. Local Ollama keeps everything on your computer.';
  };
  $('#pv').onchange = cfg; cfg();
  drawLocation();
  $('#aer').onclick = () => $('#ae').value = 'pptx docx pdf txt md markdown csv vtt srt png jpg jpeg webp bmp tif tiff gif mp3 m4a wav ogg oga flac aac opus wma amr 3gp mp4 m4v mkv webm mov avi';
  $$('input[name=ap]').forEach(r => r.onchange = () => { applyTheme(r.value); store.set('sb_theme', r.value === 'system' ? null : r.value); });
  const dl = $('#dl'); if (dl) dl.onclick = guard(async () => { S.settings = await api('/settings', { method: 'POST', body: collect() }); await api('/llm/pull', { method: 'POST', body: { model: S.settings.ollama.model } }); toast('Download started, see the sidebar'); track(); });
  const collect = () => { const p = $('#pv').value, b = { provider: p, appearance: $('input[name=ap]:checked').value, allowed_extensions: $('#ae').value, content_language: $('#cl').value, context_chars: +$('#cc').value,
    whisper: { model: $('#wm').value, device: $('#wd').value, language: $('#wl').value, glossary: $('#wg').value }, practice: { session_size: +$('#ps').value, new_per_session: +$('#pn').value, think_delay: +$('#pt').value } };
    if (p === 'ollama') b.ollama = { host: $('#oh').value, model: $('#om').value, fast_model: $('#ofm').value.trim(), num_ctx: +$('#on').value };
    if (p === 'anthropic') b.anthropic = { api_key: $('#ak').value, model: $('#am').value };
    if (p === 'openai') b.openai = { api_key: $('#ok').value, base_url: $('#ou').value, model: $('#omm').value }; return b; };
  $('#save').onclick = guard(async () => { S.settings = await api('/settings', { method: 'POST', body: collect() }); toast('Saved'); });
  $('#test').onclick = guard(async () => { S.settings = await api('/settings', { method: 'POST', body: collect() }); $('#tres').textContent = 'testing…'; const r = await api('/llm/test'); $('#tres').textContent = (r.ok ? '✔ ' : '✘ ') + r.message; $('#tres').style.color = r.ok ? 'var(--good)' : 'var(--bad)'; });
};

/* ------------------------------------------------------------------ data location (shared drive) */
async function drawLocation() {
  const box = $('#loc'); if (!box) return;
  const i = await api('/storage');
  const cand = i.candidates.map(c => `<tr><td><b>${esc(c.label)}</b><div class="mute small">${esc(c.path)}</div></td>
    <td>${c.has_data ? '<span class="badge ready">StudyBench data found</span>' : '<span class="badge">new folder</span>'}</td>
    <td style="text-align:right">${c.path === i.path ? '<span class="mute small">in use</span>'
      : c.has_data ? `<button class="btn sm primary" data-use="${esc(c.path)}">Use this data</button>`
      : `<button class="btn sm" data-mv="${esc(c.path)}">${i.has_data_here ? 'Move my data here' : 'Use this folder'}</button>`}</td></tr>`).join('');
  box.innerHTML = `<h3 style="margin-top:0">Data location</h3>
  <p class="mute small" style="margin-top:0">Your topics, files, summaries and progress live in one folder. Put it in OneDrive, Google Drive, iCloud Drive or Dropbox and every computer you use (Windows or Mac) finds the same data automatically. Without a cloud drive the data stays on this computer only.</p>
  <div class="kv"><label>Current folder</label><span><code>${esc(i.path)}</code> ${i.in_cloud ? '<span class="badge ready">☁ in a cloud drive</span>' : '<span class="badge new">this computer only</span>'}</span></div>
  ${i.notice ? `<div class="disclaimer" style="margin-top:10px">${esc(i.notice)}</div>` : ''}
  ${i.candidates.length ? `<h3>Cloud drives found on this computer</h3><table>${cand}</table>` : '<p class="mute small">No OneDrive, Google Drive, iCloud Drive or Dropbox folder was found on this computer. You can type any folder below, for example one on a USB drive or a network share.</p>'}
  <h3>Another folder</h3><div class="row"><input type="text" id="locp" style="flex:1;min-width:240px" placeholder="e.g. D:\\Drive\\StudyBench-Data  or  ~/Library/CloudStorage/OneDrive/StudyBench-Data">
  <button class="btn" id="locuse">Use this folder</button><button class="btn" id="locmove">Move my data here</button>${i.is_default ? '' : '<button class="btn ghost" id="locdef">Back to default</button>'}</div>
  <ul class="mute small" style="margin:10px 0 0;padding-left:18px"><li>“Use” connects to data that is already in the folder (or an empty folder). “Move” copies your current data there first; the old copy stays where it was.</li>
  <li>Use StudyBench on one computer at a time and close it before switching; wait until the drive shows “synced”. A warning appears if another computer is using the folder.</li>
  <li>API keys are not stored in the shared folder. Enter them once on each computer. A daily backup of the database is kept in the folder (${i.backups} so far).</li></ul>`;
  const go = (path, mode) => guard(async () => {
    if (mode === 'move' && !confirm('Copy your current data to this folder and use it from now on?')) return;
    await api('/storage', { method: 'POST', body: { path, mode } }); toast('Data folder changed'); await loadTopics(); S.topic = null; store.set('sb_topic', null);
    if (S.topics && S.topics[0]) { S.topic = S.topics[0].id; store.set('sb_topic', S.topic); } checkStorage(); render();
  })();
  $$('#loc [data-use]').forEach(b => b.onclick = () => go(b.dataset.use, 'use'));
  $$('#loc [data-mv]').forEach(b => b.onclick = () => go(b.dataset.mv, 'move'));
  $('#locuse').onclick = () => go($('#locp').value, 'use'); $('#locmove').onclick = () => go($('#locp').value, 'move');
  const d = $('#locdef'); if (d) d.onclick = () => go('', 'default');
}
async function checkStorage() {
  let b = $('#banner'); if (!b) { b = document.createElement('div'); b.id = 'banner'; $('#top').insertAdjacentElement('afterend', b); }
  try {
    const i = await api('/storage'); const m = [];
    if (i.notice) m.push(`<div class="disclaimer" style="margin:0">${esc(i.notice)}</div>`);
    if (i.other_machine) m.push(`<div class="disclaimer" style="margin:0">⚠ <b>${esc(i.other_machine.name)}</b> (${esc(i.other_machine.os)}) is using this data folder right now. Using both at once can overwrite changes. Close StudyBench there first.</div>`);
    if (i.adopted && !store.get('sb_adopted_' + i.path)) { m.push(`<div class="tip" style="margin:0">${esc(i.adopted)} <button class="btn sm ghost" id="adok">OK</button></div>`); }
    b.innerHTML = m.length ? `<div style="padding:8px 28px;display:flex;flex-direction:column;gap:6px">${m.join('')}</div>` : '';
    const ok = $('#adok'); if (ok) ok.onclick = () => { store.set('sb_adopted_' + i.path, '1'); checkStorage(); };
  } catch { /* server not reachable */ }
}
setInterval(() => checkStorage(), 45000);

/* ------------------------------------------------------------------ boot */
(async function boot() {
  try { S.settings = await api('/settings'); } catch (e) { $('#main').innerHTML = `<div class="wrap"><div class="card">Cannot reach the StudyBench server: ${esc(e.message)}</div></div>`; return; }
  applyTheme(S.settings.appearance === 'system' ? null : S.settings.appearance); store.set('sb_theme', S.settings.appearance === 'system' ? null : S.settings.appearance);
  await loadTopics(); drawTimer();
  const [v, s] = location.hash.replace('#/', '').split('/'); if (views[v]) { S.view = v; S.sub = s || null; }
  await render(); pollJobs(); checkStorage();
})();
