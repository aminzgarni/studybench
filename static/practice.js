'use strict';
/* Step 4: Practice hub - review (spaced repetition), question bank, exam, oral exam, recall, teach, mnemonics, weak points */
const TYPE_NAME = { mcq: 'Multiple choice', case: 'Case', open: 'Open question', audit: 'Audit question', jury: 'Oral exam', elab: 'Why / how / what if' };
const TABS = [['review', 'Review'], ['bank', 'Question bank'], ['exam', 'Exam'], ['jury', 'Oral exam'], ['recall', 'Recall'], ['teach', 'Teach it'], ['mnemonic', 'Mnemonics'], ['weak', 'Weak points']];
const sub = {};

views.practice = async root => {
  if (needTopic(root)) return;
  const cur = S.sub && sub[S.sub] ? S.sub : 'review';
  root.innerHTML = `<div class="wrap"><h1>Practice</h1>
  <div class="disclaimer">Exercises are written by AI from <b>your</b> files. Treat the model answers as a study aid and verify important facts in the source.</div>
  <div class="tabs">${TABS.map(([k, n]) => `<a data-k="${k}" class="${k === cur ? 'on' : ''}">${n}</a>`).join('')}</div><div id="sub"></div></div>`;
  $$('.tabs a', root).forEach(a => a.onclick = () => go('practice', a.dataset.k));
  S.busy = false; await sub[cur]($('#sub'));
};

/* ---------------- shared pickers */
const topicChecks = (sel = [S.topic]) => `<div class="row">${S.topics.map(x => `<label><input type="checkbox" class="tp" value="${x.id}" ${sel.includes(x.id) ? 'checked' : ''}> ${esc(x.name)}</label>`).join('')}</div>`;
const pickedTopics = r => $$('.tp', r).filter(c => c.checked).map(c => +c.value);
const docChecks = docs => `<div class="list-check" style="max-height:220px;overflow:auto"><label><input type="checkbox" id="dall"> <b>All documents with “Use” ticked</b></label>${docs.map(d => `<label><input type="checkbox" class="dc" value="${d.id}"> ${esc(d.filename)}</label>`).join('')}</div>`;
const pickedDocs = r => $$('.dc', r).filter(c => c.checked).map(c => +c.value);
const wireAll = r => { const a = $('#dall', r); if (a) a.onchange = e => $$('.dc', r).forEach(c => c.checked = false); $$('.dc', r).forEach(c => c.onchange = () => { if (c.checked && a) a.checked = false; }); };
const langSel = id => `<select id="${id}">${[['', 'default'], ['de', 'German'], ['en', 'English'], ['fr', 'French']].map(([v, n]) => `<option value="${v}">${n}</option>`).join('')}</select>`;

/* ---------------- microphone -> local Whisper -> text */
function micButton(getTa) {
  const b = document.createElement('button'); b.className = 'btn sm'; b.type = 'button'; b.textContent = '🎙 Record'; let rec = null, chunks = [];
  b.onclick = guard(async () => {
    if (rec) { rec.stop(); return; }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    rec = new MediaRecorder(stream); chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = guard(async () => {
      stream.getTracks().forEach(t => t.stop()); rec = null; b.textContent = '⏳ Transcribing…'; b.disabled = true;
      try { const fd = new FormData(); fd.append('audio', new Blob(chunks, { type: 'audio/webm' }), 'rec.webm');
        const { job } = await api('/teach/transcribe', { method: 'POST', body: fd }); track();
        const res = await waitJob(job); const ta = getTa(); ta.value = (ta.value ? ta.value + '\n' : '') + res.text; }
      finally { b.textContent = '🎙 Record'; b.disabled = false; }
    });
    rec.start(); b.textContent = '⏹ Stop';
  });
  return b;
}

/* ---------------- REVIEW (spaced repetition + interleaving + weak boost) */
sub.review = async root => {
  const cnt = async () => api(`/queue/counts?topics=${pickedTopics(root).join(',')}`);
  const c0 = await api(`/queue/counts?topics=${S.topic}`);
  const p = S.settings.practice;
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Review session</h3>
  <p class="mute small">Cards you know come back later and later; cards you miss come back within minutes, then hours, then days. Weak cards are mixed in more often.</p>
  <div class="mute small" style="margin-bottom:6px">Topics (select several to interleave them):</div>${topicChecks()}
  <div class="row" id="cn" style="margin:12px 0"></div>
  <div class="row"><label>Cards <input type="number" id="lim" value="${p.session_size}" min="3" max="100" style="width:70px"></label>
  <label>Mode <select id="mode"><option value="due">Due + new (recommended)</option><option value="weak">Weak points only</option><option value="new">New cards only</option><option value="all">Everything, mixed (cram)</option></select></label>
  <label><input type="checkbox" id="il" checked> mix topics &amp; types</label></div>
  <div class="row" id="types" style="margin-top:8px">${Object.entries(TYPE_NAME).map(([k, n]) => `<label><input type="checkbox" class="ty" value="${k}" checked> ${n}</label>`).join('')}</div>
  <div style="margin-top:14px"><button class="btn primary" id="go">Start session</button></div></div><div id="stage"></div>`;
  const upd = guard(async () => { const c = await cnt(); $('#cn').innerHTML = `<span class="chip">${c.due} due</span><span class="chip">${c.new} new</span><span class="chip" style="background:var(--bad-soft);color:var(--bad)">${c.weak} weak</span><span class="chip">${c.total} total</span>${c.total ? '' : ' <span class="mute small">No questions yet. Create some in “Question bank”.</span>'}`; });
  $$('.tp', root).forEach(c => c.onchange = upd); upd();
  $('#go').onclick = guard(async () => {
    const types = $$('.ty', root).filter(c => c.checked).map(c => c.value);
    const items = await api(`/queue?topics=${pickedTopics(root).join(',')}&mode=${$('#mode').value}&limit=${$('#lim').value}&types=${types.join(',')}&interleave=${$('#il').checked ? 1 : 0}`);
    if (!items.length) return toast($('#mode').value === 'weak' ? 'No weak points. Well done!' : 'Nothing due right now. Create more questions or come back later.', true);
    runCards(items, $('#stage'), 'review');
  });
};

function runCards(items, stage, mode) {
  const st = { q: items.slice(), i: 0, done: 0, good: 0, again: 0, t0: Date.now(), total: items.length };
  S.busy = true; stage.scrollIntoView({ behavior: 'smooth' });
  const finish = () => { S.busy = false; const mins = Math.round((Date.now() - st.t0) / 60000);
    stage.innerHTML = `<div class="card"><h2 style="margin-top:0">Session complete ✔</h2><div class="grid"><div><div class="stat">${st.done}</div><div class="mute">cards answered</div></div>
      <div><div class="stat">${st.done ? Math.round(100 * st.good / st.done) : 0}%</div><div class="mute">remembered</div></div><div><div class="stat">${mins || '<1'} min</div><div class="mute">time</div></div></div>
      <p>${st.again ? `${st.again} card(s) will come back within minutes or hours; the weak ones are mixed into your next sessions.` : 'Everything remembered. Intervals grow.'}</p>
      <div class="tip"><b>Next:</b> take a short break (water, a few steps). Before sleep, glance once at the cards you missed; sleep does the consolidation.</div>
      <button class="btn primary" onclick="render()">Back</button></div>`; };
  const draw = () => {
    if (st.i >= st.q.length) return finish();
    const it = st.q[st.i], d = it.data, t0 = Date.now(); let revealed = false;
    const head = `<div class="row sp"><div class="bar" style="flex:1;max-width:340px"><i style="width:${Math.round(100 * st.i / st.q.length)}%"></i></div>
      <span class="mute small">${st.i + 1} / ${st.q.length} · ${TYPE_NAME[it.type]} ${it.weak ? '· <span class="badge error">weak</span>' : ''} ${it.retry ? '· again' : ''}</span></div>`;
    const grade = async g => { const secs = (Date.now() - t0) / 1000; await api(`/items/${it.id}/review`, { method: 'POST', body: { grade: g, seconds: secs, mode } });
      st.done++; if (g >= 2) st.good++; if (g === 0) { st.again++; st.q.splice(Math.min(st.i + 5, st.q.length), 0, { ...it, retry: true }); } st.i++; draw(); };
    if (it.type === 'mcq') {
      stage.innerHTML = `<div class="card">${head}<div class="qbox" style="margin:14px 0 8px">${esc(it.question)}</div>
        ${d.options.map((o, k) => `<button class="opt" data-k="${k}"><b>${'ABCDE'[k]}</b> &nbsp;${esc(o)}</button>`).join('')}<div id="fb"></div></div>`;
      $$('.opt', stage).forEach(b => b.onclick = () => {
        const k = +b.dataset.k, ok = k === d.answer_index; $$('.opt', stage).forEach(x => { x.disabled = true; if (+x.dataset.k === d.answer_index) x.classList.add('right'); }); if (!ok) b.classList.add('wrong');
        $('#fb').innerHTML = `<div class="answer"><b>${ok ? '✔ Correct' : '✘ Not quite'}</b><div class="md">${md(d.explanation)}</div>${it.source ? `<div class="mute small">Source: ${esc(it.source)}</div>` : ''}</div>
          <div class="grades">${ok ? '<button class="btn primary" data-g="2">Next<small>remembered</small></button><button class="btn" data-g="1">I guessed<small>count as hard</small></button><button class="btn" data-g="3">Too easy<small>longer gap</small></button>' : '<button class="btn primary" data-g="0">Next<small>see it again soon</small></button>'}</div>`;
        $$('[data-g]', stage).forEach(x => x.onclick = guard(() => grade(+x.dataset.g)));
      });
      return;
    }
    const qtxt = esc(it.question) + (d.tasks?.length ? '\n\n' + d.tasks.map(x => '• ' + esc(x)).join('\n') : '');
    const delay = +S.settings.practice.think_delay || 0;
    stage.innerHTML = `<div class="card">${head}<div class="qbox" style="margin:14px 0 10px">${qtxt}</div>
      <textarea id="ans" placeholder="Answer from memory: type here, or say it aloud / write it on paper. Then reveal."></textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary" id="rev" ${delay ? 'disabled' : ''}>Show answer</button>${micButton(() => $('#ans')).outerHTML ? '' : ''}<button class="btn sm" id="ai" title="Your AI compares your typed answer with the key points">✨ Check my answer</button>
      <button class="btn sm ghost" id="flag" title="Mark as weak point">⚑ weak</button></div><div id="fb"></div></div>`;
    const mic = micButton(() => $('#ans')); $('#rev').after(mic);
    const rev = $('#rev'); if (delay) { let n = delay; rev.textContent = `Think… ${n}`; const iv = setInterval(() => { n--; if (n <= 0) { clearInterval(iv); rev.disabled = false; rev.textContent = 'Show answer'; } else rev.textContent = `Think… ${n}`; }, 1000); }
    $('#flag').onclick = guard(async () => { await api(`/items/${it.id}/flag`, { method: 'POST', body: { weak: true } }); toast('Marked as weak point'); });
    $('#ai').onclick = guard(async () => { const a = $('#ans').value; $('#ai').textContent = '…'; try { const r = await api(`/items/${it.id}/evaluate`, { method: 'POST', body: { answer: a } }); showRef(r); } finally { $('#ai').textContent = '✨ Check my answer'; } });
    const showRef = ev => {
      if (!revealed) { revealed = true; }
      const list = (a, t) => a?.length ? `<h4 style="margin:8px 0 2px">${t}</h4><ul>${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
      $('#fb').innerHTML = `${ev ? `<div class="answer" style="background:var(--warn-soft)"><b>AI check: ${ev.score ?? '?'}/100</b> ${esc(ev.feedback || '')}${list(ev.covered, 'Covered')}${list(ev.missing, 'Missing')}${list(ev.wrong, 'Incorrect')}</div>` : ''}
        <div class="answer"><b>Model answer</b><div class="md">${md(d.model_answer || '')}</div>${list(d.key_points, 'Key points')}${list(d.expected_evidence, 'Evidence an auditor expects')}${list(d.pitfalls, 'Typical findings / pitfalls')}${list(d.follow_ups, 'Likely follow-up questions')}
        ${it.source ? `<div class="mute small">Source: ${esc(it.source)}</div>` : ''}</div>
        <div class="grades">${['Again', 'Hard', 'Good', 'Easy'].map((n, g) => `<button class="btn ${g === 2 ? 'primary' : ''}" data-g="${g}">${n}<small>${it.preview[g]}</small></button>`).join('')}</div>
        <p class="mute small">Be honest: what counts is how well you recalled it <i>before</i> looking. Keys 1-4 also work.</p>`;
      $$('[data-g]', stage).forEach(x => x.onclick = guard(() => grade(+x.dataset.g)));
      const kh = e => { if (['1', '2', '3', '4'].includes(e.key) && document.activeElement.tagName !== 'TEXTAREA') { document.removeEventListener('keydown', kh); grade(+e.key - 1); } }; document.addEventListener('keydown', kh);
    };
    rev.onclick = () => { showRef(null); rev.disabled = true; };
  };
  draw();
}

/* ---------------- QUESTION BANK */
sub.bank = async root => {
  const [docs, items] = await Promise.all([api(`/topics/${S.topic}/docs`), api(`/topics/${S.topic}/items`)]);
  const ready = docs.filter(d => d.text_len > 0);
  const by = {}; items.forEach(i => by[i.type] = (by[i.type] || 0) + 1);
  const f = sub.bankFilter || '';
  const shown = items.filter(i => !f || i.type === f);
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Create exercises</h3>
  ${!ready.length ? '<p class="mute">No documents with text in this topic yet.</p>' : `<div class="mute small">Based on (leave empty = all documents marked “Use”):</div>${docChecks(ready)}
  <div class="row" style="margin-top:10px">${Object.entries(TYPE_NAME).map(([k, n]) => `<label><input type="checkbox" class="ty" value="${k}" ${['mcq', 'case', 'open'].includes(k) ? 'checked' : ''}> ${n}</label>`).join('')}</div>
  <div class="row" style="margin-top:8px"><label>Per type <input type="number" id="n" value="5" min="1" max="30" style="width:64px"></label>
  <label>Level <select id="df"><option>mixed</option><option>basic recall</option><option>applied (practice)</option><option>exam level (hard)</option></select></label><label>Language ${langSel('lg')}</label>
  <button class="btn primary" id="gen">✨ Create</button></div>
  <p class="mute small">Types: <b>Case</b> practical scenario · <b>Audit</b> “prove it” questions, as in an inspection or compliance check · <b>Oral exam</b> committee questions with follow-ups · <b>Why / how / what if</b> elaboration.</p>`}</div>
  <div class="row sp"><h2 style="margin:0">Your questions (${items.length})</h2><select id="ft"><option value="">All types</option>${Object.entries(TYPE_NAME).map(([k, n]) => `<option value="${k}" ${f === k ? 'selected' : ''}>${n} (${by[k] || 0})</option>`).join('')}</select></div>
  <div style="margin-top:10px">${shown.slice(0, 80).map(i => `<div class="card" data-id="${i.id}"><div class="row sp"><span><span class="badge">${TYPE_NAME[i.type]}</span> ${i.weak ? '<span class="badge error">weak</span>' : ''} ${i.state === 'new' ? '<span class="badge new">new</span>' : `<span class="mute small">next in ${i.ivl >= 1 ? Math.round(i.ivl) + ' d' : 'minutes/hours'}</span>`}</span>
    <span><button class="btn sm ghost tg">details</button><button class="btn sm ghost danger rm">🗑</button></span></div>
    <div style="margin-top:6px;white-space:pre-wrap">${esc(i.question.slice(0, 280))}${i.question.length > 280 ? '…' : ''}</div><div class="dt" style="display:none"></div></div>`).join('') || '<div class="empty">No questions yet.</div>'}
    ${shown.length > 80 ? `<p class="mute">Showing the first 80 of ${shown.length}.</p>` : ''}</div>`;
  const gen = $('#gen'); if (gen) { wireAll(root);
    gen.onclick = guard(async () => { const types = $$('.ty', root).filter(c => c.checked).map(c => c.value);
      await api('/generate', { method: 'POST', body: { topic_id: S.topic, doc_ids: pickedDocs(root), types, n: +$('#n').value, difficulty: $('#df').value, language: $('#lg').value || undefined } }); toast('Creating exercises in the background…'); track(); }); }
  $('#ft').onchange = e => { sub.bankFilter = e.target.value; render(); };
  $$('[data-id]', root).forEach(c => { const it = items.find(x => x.id == c.dataset.id);
    $('.rm', c).onclick = guard(async () => { await api(`/items/${it.id}`, { method: 'DELETE' }); c.remove(); });
    $('.tg', c).onclick = () => { const b = $('.dt', c); if (b.style.display === 'none') { const d = it.data;
      b.innerHTML = `<div class="answer">${it.type === 'mcq' ? `<ol type="A">${d.options.map((o, k) => `<li ${k === d.answer_index ? 'style="font-weight:700"' : ''}>${esc(o)}</li>`).join('')}</ol><div class="md">${md(d.explanation)}</div>` : `<div class="md">${md(d.model_answer || '')}</div>${(d.key_points || []).length ? '<ul>' + d.key_points.map(k => `<li>${esc(k)}</li>`).join('') + '</ul>' : ''}`}
        <div class="mute small">${esc(it.source)}</div></div>`; b.style.display = 'block'; } else b.style.display = 'none'; };
  });
};

/* ---------------- EXAM (timed, no feedback until the end, calibration) */
sub.exam = async root => {
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Exam simulation</h3><p class="mute small">Timed, no hints, no feedback until you hand in. First predict your result: knowing how well you judge yourself is part of the training.</p>
  ${topicChecks()}<div class="row" style="margin-top:10px"><label>Questions <input type="number" id="n" value="10" min="3" max="60" style="width:64px"></label>
  <label>Minutes <input type="number" id="m" value="20" min="1" max="240" style="width:70px"></label>
  <label>Expected score <input type="range" id="ex" min="0" max="100" value="70"> <b id="exv">70%</b></label></div>
  <div class="row" style="margin-top:8px">${Object.entries(TYPE_NAME).map(([k, n]) => `<label><input type="checkbox" class="ty" value="${k}" ${['mcq', 'case', 'open'].includes(k) ? 'checked' : ''}> ${n}</label>`).join('')}</div>
  <div style="margin-top:12px"><button class="btn primary" id="go">Start exam</button></div></div><div id="stage"></div>`;
  $('#n').oninput = () => { $('#m').value = Math.max(1, Math.round($('#n').value * 2)); }; $('#ex').oninput = () => $('#exv').textContent = $('#ex').value + '%';
  $('#go').onclick = guard(async () => {
    const ex = await api('/exam/start', { method: 'POST', body: { topic_ids: pickedTopics(root), n: +$('#n').value, minutes: +$('#m').value, expected_pct: +$('#ex').value, types: $$('.ty', root).filter(c => c.checked).map(c => c.value) } });
    runExam(ex, +$('#ex').value, $('#stage'));
  });
};
function runExam(ex, expected, stage) {
  S.busy = true; const answers = {}; let i = 0; const end = Date.now() + ex.minutes * 60000; let over = false;
  const per = ex.minutes / ex.items.length;
  const draw = () => {
    const it = ex.items[i];
    stage.innerHTML = `<div class="card"><div class="row sp"><b>Question ${i + 1} / ${ex.items.length}</b><span id="xt" style="font-size:20px;font-weight:700;font-variant-numeric:tabular-nums"></span></div>
      <div class="mute small">${TYPE_NAME[it.type]} · suggested ${per.toFixed(1)} min per question</div>
      <div class="qbox" style="margin:12px 0">${esc(it.question)}${it.tasks?.length ? '\n\n' + it.tasks.map(x => '• ' + esc(x)).join('\n') : ''}</div>
      ${it.type === 'mcq' ? it.options.map((o, k) => `<label class="opt" style="display:block"><input type="radio" name="o" value="${k}" ${answers[it.id] == k ? 'checked' : ''}> <b>${'ABCDE'[k]}</b> ${esc(o)}</label>`).join('') : `<textarea id="ta" placeholder="Your answer…">${esc(answers[it.id] || '')}</textarea>`}
      <div class="row" style="margin-top:12px"><button class="btn" id="pv" ${i === 0 ? 'disabled' : ''}>◀ Previous</button><button class="btn" id="nx" ${i === ex.items.length - 1 ? 'disabled' : ''}>Next ▶</button>
      <span style="flex:1"></span>${ex.items.map((x, k) => `<button class="btn sm ${k === i ? 'primary' : ''}" data-k="${k}" style="${answers[x.id] != null && answers[x.id] !== '' ? 'border-color:var(--good)' : ''}">${k + 1}</button>`).join('')}
      <button class="btn primary" id="sub">Hand in</button></div></div>`;
    const save = () => { if (it.type === 'mcq') { const c = $('input[name=o]:checked', stage); if (c) answers[it.id] = +c.value; } else answers[it.id] = $('#ta').value; };
    $('#pv').onclick = () => { save(); i--; draw(); }; $('#nx').onclick = () => { save(); i++; draw(); };
    $$('[data-k]', stage).forEach(b => b.onclick = () => { save(); i = +b.dataset.k; draw(); });
    $('#sub').onclick = () => { save(); const un = ex.items.filter(x => answers[x.id] == null || answers[x.id] === '').length; if (!un || confirm(`${un} question(s) unanswered. Hand in anyway?`)) submit(); };
    tickX();
  };
  const tickX = () => { const e = $('#xt'); if (!e) return; const left = end - Date.now(); e.textContent = fmtMMSS(left); e.style.color = left < 60000 ? 'var(--bad)' : ''; };
  const iv = setInterval(() => { if (!document.body.contains(stage) || over) return clearInterval(iv); tickX(); if (end - Date.now() <= 0) { over = true; clearInterval(iv); toast('Time is up. Your answers were handed in.'); const it = ex.items[i]; if (it.type !== 'mcq' && $('#ta')) answers[it.id] = $('#ta').value; else { const c = $('input[name=o]:checked', stage); if (c) answers[it.id] = +c.value; } submit(); } }, 500);
  const submit = guard(async () => {
    over = true; const r = await api(`/exam/${ex.exam_id}/submit`, { method: 'POST', body: { answers } }); const grades = {}; r.items.forEach(x => { if (x.correct !== null) grades[x.id] = x.correct ? 1 : 0; });
    const drawR = () => { const open = r.items.filter(x => x.correct === null), todo = open.filter(x => grades[x.id] === undefined);
      stage.innerHTML = `<div class="card"><h2 style="margin-top:0">Review and self-grade</h2><p class="mute small">Multiple-choice is scored automatically. For the other questions compare your answer with the model answer and grade yourself honestly: ✗ wrong, ~ partly, ✓ right.</p>
      ${r.items.map((x, k) => `<div class="card" style="border-left:4px solid ${x.correct === true ? 'var(--good)' : x.correct === false ? 'var(--bad)' : 'var(--line)'}"><b>${k + 1}. ${esc(x.question)}</b>
      ${x.type === 'mcq' ? `<div class="small">Your answer: ${x.answer != null ? esc(x.data.options[x.answer]) : '<i>none</i>'}<br>Correct: <b>${esc(x.data.options[x.data.answer_index])}</b></div><div class="md small">${md(x.data.explanation)}</div>`
        : `<div class="small" style="margin:6px 0"><b>You:</b> ${esc(x.answer || '(blank)')}</div><div class="answer"><div class="md">${md(x.data.model_answer || '')}</div>${(x.data.key_points || []).length ? '<ul>' + x.data.key_points.map(p => `<li>${esc(p)}</li>`).join('') + '</ul>' : ''}</div>
        <div class="row" style="margin-top:6px" data-q="${x.id}">${[[0, '✗'], [0.5, '~'], [1, '✓']].map(([v, l]) => `<button class="btn sm ${grades[x.id] === v ? 'primary' : ''}" data-v="${v}">${l}</button>`).join('')}</div>`}</div>`).join('')}
      <button class="btn primary" id="fin" ${todo.length ? 'disabled' : ''}>${todo.length ? `Grade ${todo.length} more` : 'Finish and see result'}</button></div>`;
      $$('[data-q]', stage).forEach(g => $$('[data-v]', g).forEach(b => b.onclick = () => { grades[g.dataset.q] = +b.dataset.v; drawR(); }));
      $('#fin').onclick = guard(async () => { const res = await api(`/exam/${ex.exam_id}/finish`, { method: 'POST', body: { grades } }); S.busy = false; const cal = res.calibration;
        stage.innerHTML = `<div class="card"><h2 style="margin-top:0">Result</h2><div class="grid"><div><div class="stat">${res.score_pct}%</div><div class="mute">your score</div></div><div><div class="stat">${res.expected_pct ?? '–'}%</div><div class="mute">what you expected</div></div></div>
        <p>${cal === null ? '' : Math.abs(cal) <= 7 ? '🎯 Your self-assessment was accurate.' : cal < 0 ? '⚠ You overestimated yourself by ' + Math.abs(cal) + ' points. Practise more under exam conditions and rely less on the feeling of “I know this”.' : '👍 You underestimated yourself by ' + cal + ' points. Trust your preparation a bit more.'}</p>
        <p class="mute small">Missed questions were scheduled for early review and appear in “Weak points”.</p><button class="btn primary" onclick="render()">Done</button></div>`; }); };
    drawR();
  });
  draw();
}

/* ---------------- ORAL EXAM (jury) */
sub.jury = async root => {
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Oral exam with a committee</h3><p class="mute small">The examiner asks, listens, probes with follow-up questions, then moves on. Answer in full sentences, as you would in front of the committee. Uses your “Oral exam”, “Open”, “Case” and “Audit” questions.</p>
  ${topicChecks()}<div class="row" style="margin-top:10px"><label>Questions <input type="number" id="n" value="3" min="1" max="10" style="width:60px"></label><label>Language ${langSel('lg')}</label><button class="btn primary" id="go">Enter the room</button></div></div><div id="stage"></div>`;
  $('#go').onclick = guard(async () => {
    const r = await api('/jury/start', { method: 'POST', body: { topic_ids: pickedTopics(root), n: +$('#n').value, language: $('#lg').value || undefined } }); S.busy = true; const stage = $('#stage');
    const log = []; const draw = (state, final) => { stage.innerHTML = `<div class="card"><div class="mute small">Question ${state.index} of ${state.total}</div><div class="chat" style="margin-top:10px">${log.map(m => `<div class="msg ${m.who === 'e' ? 'assistant' : 'user'}">${esc(m.text)}</div>`).join('')}</div>
      ${final ? `<div class="answer"><h3 style="margin-top:0">Feedback: ${final.score ?? '?'}/100</h3><b>Strengths</b><ul>${(final.strengths || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul><b>To improve</b><ul>${(final.weaknesses || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul><p>${esc(final.advice || '')}</p></div><button class="btn primary" onclick="render()">Done</button>`
        : `<textarea id="ja" placeholder="Your answer… (Ctrl+Enter to send)"></textarea><div class="row" style="margin-top:8px"><button class="btn primary" id="snd">Answer</button></div>`}</div>`;
      if (!final) { const ta = $('#ja'); $('#snd').before(micButton(() => ta)); ta.focus(); const send = guard(async () => { const a = ta.value.trim(); if (!a) return; log.push({ who: 'c', text: a }); $('#snd').disabled = true; $('#snd').textContent = 'The examiner is thinking…';
        try { const t = await api('/jury/turn', { method: 'POST', body: { session: r.session, answer: a } }); if (t.say) log.push({ who: 'e', text: t.say }); S.busy = !t.done; draw({ index: t.index ?? state.total, total: t.total ?? state.total }, t.final); } catch (e) { log.pop(); draw(state); throw e; } });
        $('#snd').onclick = send; ta.onkeydown = e => { if (e.key === 'Enter' && e.ctrlKey) send(); }; } };
    log.push({ who: 'e', text: r.say }); draw({ index: r.index, total: r.total });
  });
};

/* ---------------- RECALL (blurting / brain dump) */
sub.recall = async root => {
  const docs = (await api(`/topics/${S.topic}/docs`)).filter(d => d.text_len > 0);
  const auto = TM.phase === 'recall';
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Active recall: close the notes, write what you remember</h3><p class="mute small">Retrieving from memory beats re-reading. Write on paper (best for deep memory) or type. Only afterwards look at what you missed.</p>
  <div class="mute small">Topic material to recall (empty = all documents marked “Use”):</div>${docChecks(docs)}
  <div class="row" style="margin-top:10px"><label>Minutes <input type="number" id="mn" value="${auto ? 3 : 5}" min="1" max="30" style="width:64px"></label>
  <label><input type="radio" name="md" value="paper" checked> on paper</label><label><input type="radio" name="md" value="type"> type here</label><label>Language ${langSel('lg')}</label>
  <button class="btn primary" id="go">Start</button></div></div><div id="stage"></div>`;
  wireAll(root);
  $('#go').onclick = guard(async () => {
    const stage = $('#stage'), mins = +$('#mn').value, typed = $('input[name=md]:checked').value === 'type'; const end = Date.now() + mins * 60000; S.busy = true;
    const body = { topic_id: S.topic, doc_ids: pickedDocs(root), language: $('#lg').value || undefined };
    const pts = api('/recall/points', { method: 'POST', body }).catch(e => { toast(e.message, true); return { points: [] }; });
    stage.innerHTML = `<div class="card"><div class="row sp"><h3 style="margin:0">Notes closed. Go!</h3><span id="rt" style="font-size:26px;font-weight:700;font-variant-numeric:tabular-nums"></span></div>
      <p class="mute">Write everything you remember: definitions, criteria, numbers, steps, examples, pitfalls. Do not peek.</p>
      ${typed ? '<textarea id="rx" style="min-height:260px" placeholder="Start writing…"></textarea>' : ''}<button class="btn primary" id="dn" style="margin-top:10px">I'm done</button></div>`;
    let finished = false; const done = async () => { if (finished) return; finished = true; clearInterval(iv); S.busy = false; const text = typed ? $('#rx').value : ''; const { points } = await pts;
      stage.innerHTML = `<div class="card"><h3 style="margin-top:0">Check yourself</h3>${points.length ? `<p class="mute small">Tick every point you had. Be strict: partially is not yet.</p><div class="list-check">${points.map((p, k) => `<label><input type="checkbox" class="kp" value="${k}"> ${esc(p)}</label>`).join('')}</div>
        <div class="row" style="margin-top:10px"><button class="btn" id="sc">Score me</button>${typed ? '<button class="btn" id="cmp">✨ Let the AI compare my text</button>' : ''}</div><div id="rr"></div>` : '<p class="mute">Could not create key points (is the AI reachable?). Compare with your summary in Interpret.</p>'}</div>`;
      const sc = $('#sc'); if (sc) sc.onclick = () => { const n = $$('.kp', stage).filter(c => c.checked).length; const miss = points.filter((_, k) => !$$('.kp', stage)[k].checked);
        $('#rr').innerHTML = `<div class="answer"><b>${n} of ${points.length} (${Math.round(100 * n / points.length)}%)</b>${miss.length ? '<p>To revisit:</p><ul>' + miss.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>' : '<p>Everything recalled 🎉</p>'}<p class="mute small">Repeat this recall tomorrow and in a week: it is the spacing that makes it last.</p></div>`; };
      const cmp = $('#cmp'); if (cmp) cmp.onclick = guard(async () => { cmp.textContent = '…'; const r = await api('/recall/compare', { method: 'POST', body: { points, text, language: body.language } }); cmp.textContent = '✨ Let the AI compare my text';
        const L = (a, t) => a?.length ? `<b>${t}</b><ul>${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
        $('#rr').innerHTML = `<div class="answer"><b>Coverage: ${r.coverage_pct ?? '?'}%</b>${L(r.recalled, 'Recalled')}${L(r.missing, 'Missing')}${L(r.incorrect, 'Incorrect')}<p>${esc(r.next_steps || '')}</p></div>`; }); };
    $('#dn').onclick = done; const iv = setInterval(() => { if (!document.body.contains(stage)) return clearInterval(iv); const l = end - Date.now(); const e = $('#rt'); if (e) e.textContent = fmtMMSS(l); if (l <= 0) done(); }, 500);
  });
};

/* ---------------- TEACH IT */
sub.teach = async root => {
  const docs = (await api(`/topics/${S.topic}/docs`)).filter(d => d.text_len > 0);
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Teach it</h3><p class="mute small">Explain the topic out loud as if teaching someone. Where you stumble, you have not understood it yet. Speak for 2-3 minutes (record) or type, then get feedback checked against your sources.</p>
  <div class="mute small">Material:</div>${docChecks(docs)}
  <div class="row" style="margin-top:10px"><label>Audience <select id="au"><option>a colleague</option><option>a classmate</option><option>a beginner (plain language)</option><option>a client or customer</option><option>an examination committee</option></select></label><label>Language ${langSel('lg')}</label></div>
  <textarea id="tx" style="margin-top:10px;min-height:170px" placeholder="Your explanation (type, or press Record and speak)…"></textarea>
  <div class="row" style="margin-top:8px" id="tr"><button class="btn primary" id="go">Get feedback</button></div></div><div id="fb"></div>`;
  wireAll(root); $('#go').before(micButton(() => $('#tx')));
  $('#go').onclick = guard(async () => { $('#go').textContent = 'Reading your explanation…'; try {
    const r = await api('/teach/feedback', { method: 'POST', body: { topic_id: S.topic, doc_ids: pickedDocs(root), audience: $('#au').value, text: $('#tx').value, language: $('#lg').value || undefined } });
    const L = (a, t) => a?.length ? `<h4>${t}</h4><ul>${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    $('#fb').innerHTML = `<div class="card"><h3 style="margin-top:0">Feedback: ${r.score ?? '?'}/100</h3>${L(r.strengths, 'What worked')}${L(r.errors, 'Mistakes to fix')}${L(r.missing, 'Missing')}${L(r.clarity_tips, 'Clarity')}${L(r.followup_questions, 'Questions your audience would ask')}${L(r.improved_outline, 'Better outline')}</div>`; } finally { $('#go').textContent = 'Get feedback'; } });
};

/* ---------------- MNEMONICS */
sub.mnemonic = async root => {
  root.innerHTML = `<div class="card"><h3 style="margin-top:0">Mnemonics</h3><p class="mute small">Paste a list you must remember in order (criteria, steps, drug classes…), one item per line. You get the initial letters at once, and the AI proposes an acronym and memorable phrases.</p>
  <textarea id="it" placeholder="Item one&#10;Item two&#10;Item three"></textarea><div class="row" style="margin-top:8px"><label>Style <select id="st"><option>vivid and a little funny</option><option>serious and professional</option><option>short rhyme</option></select></label><label>Language ${langSel('lg')}</label><button class="btn primary" id="go">Create</button></div></div><div id="out"></div>`;
  const items = () => $('#it').value.split('\n').map(x => x.trim()).filter(Boolean);
  $('#it').oninput = () => { const l = items().map(x => x.replace(/^\W+/, '')[0]?.toUpperCase() || '').join(''); $('#out').innerHTML = l.length > 1 ? `<div class="card"><span class="mute">First letters:</span> <b style="font-size:24px;letter-spacing:.2em">${esc(l)}</b></div>` : ''; };
  $('#go').onclick = guard(async () => { $('#go').textContent = '…'; try { const r = await api('/mnemonic', { method: 'POST', body: { items: items(), style: $('#st').value, language: $('#lg').value || undefined } });
    $('#out').innerHTML = `<div class="card"><span class="mute">First letters:</span> <b style="font-size:24px;letter-spacing:.2em">${esc(r.letters)}</b>${r.acronym ? `<h3>Acronym: ${esc(r.acronym)}</h3>` : ''}${(r.phrases || []).map(p => `<div class="tip">${esc(p)}</div>`).join('')}<p class="mute">${esc(r.tip || '')}</p></div>`; } finally { $('#go').textContent = 'Create'; } });
};

/* ---------------- WEAK POINTS */
sub.weak = async root => {
  const items = await api(`/topics/${S.topic}/items?weak=1`);
  root.innerHTML = `<div class="card"><div class="row sp"><div><h3 style="margin:0">Weak points (${items.length})</h3><span class="mute small">Cards you flagged, missed twice, or just got wrong. They come back more often until you get them right three times in a row.</span></div>
  <button class="btn primary" id="go" ${items.length ? '' : 'disabled'}>Practise them now</button></div></div>
  ${items.map(i => `<div class="card" data-id="${i.id}"><div class="row sp"><span><span class="badge">${TYPE_NAME[i.type]}</span> <span class="mute small">missed ${i.lapses}× · streak ${i.streak}</span></span><span><button class="btn sm ghost un">remove flag</button></span></div>
  <div style="margin-top:6px;white-space:pre-wrap">${esc(i.question.slice(0, 300))}</div></div>`).join('') || '<div class="empty">No weak points yet. They appear here as you practise.</div>'}`;
  $('#go').onclick = guard(async () => { const q = await api(`/queue?topics=${S.topic}&mode=weak&limit=30`); root.innerHTML = '<div id="stage"></div>'; runCards(q, $('#stage'), 'weak'); });
  $$('.un', root).forEach(b => b.onclick = guard(async () => { await api(`/items/${b.closest('[data-id]').dataset.id}/reset`, { method: 'POST' }); render(); }));
};
