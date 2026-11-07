'use strict';
/* Step 6 Recommend, Step 7 Present */
const LANG_NAME = { de: 'German', en: 'English', fr: 'French' };
const KIND_NAME = { article: 'Study', review: 'Review / guideline', book: 'Book', guideline: 'Guideline' };

/* ------------------------------------------------------------------ 6 Recommend */
views.recommend = async root => {
  if (needTopic(root)) return;
  const [docs, rd, st] = await Promise.all([api(`/topics/${S.topic}/docs`), api(`/topics/${S.topic}/reading`), api(`/stats?tz=0`)]);
  const ready = docs.filter(d => d.text_len > 0);
  const me = st.topics.find(x => x.id === S.topic) || {}; const tp = S.topics.find(x => x.id === S.topic);
  const f = S.readFilter || { lang: '', status: '' };
  const items = rd.items.filter(i => (!f.lang || i.lang === f.lang) && (!f.status || i.status === f.status));
  root.innerHTML = `<div class="wrap"><h1>Recommend</h1><p class="sub">Real studies, reviews and books on what you need most, found in open literature databases (PubMed, OpenAlex, Open Library), in German, English and French. The AI only chooses search terms. It never invents references.</p>
  <div class="card"><div class="row"><b>Base the search on</b>
  <label><input type="radio" name="sc" value="weak" ${me.weak ? 'checked' : ''}> my weak points (${me.weak || 0})</label>
  <label><input type="radio" name="sc" value="topic" ${me.weak ? '' : 'checked'}> this topic's material and summaries</label>
  <label><input type="radio" name="sc" value="docs"> selected documents</label></div>
  <details id="dd" style="margin-top:8px"><summary class="small mute">Choose documents</summary>${docChecks(ready)}</details>
  <div class="row" style="margin-top:10px"><b>Languages</b>${['de', 'en', 'fr'].map(l => `<label><input type="checkbox" class="lg" value="${l}" ${['de', 'en'].includes(l) ? 'checked' : ''}> ${LANG_NAME[l]}</label>`).join('')}
  <b style="margin-left:12px">Types</b>${[['article', 'Studies'], ['review', 'Reviews / guidelines'], ['book', 'Books']].map(([k, n]) => `<label><input type="checkbox" class="kd" value="${k}" checked> ${n}</label>`).join('')}</div>
  <div class="row" style="margin-top:10px"><label style="flex:1;min-width:260px"><b>Topic keywords</b> <input type="text" id="kw" value="${esc(tp?.keywords || '')}" placeholder="the core subject in a few words, e.g. project risk management" style="width:100%"></label></div>
  <p class="mute small" style="margin:4px 0">Specific search terms give specific results. Press “Suggest search terms”, edit them (one per line), then search. Results that do not match the terms are filtered out.</p>
  <button class="btn" id="sg">✨ Suggest search terms</button>
  <div id="qbox" style="margin-top:10px">${Object.entries(LANG_NAME).map(([l, n]) => `<div class="qrow" data-l="${l}" style="display:${['de', 'en'].includes(l) ? 'block' : 'none'}"><div class="mute small">${n}</div><textarea class="qt" data-l="${l}" style="min-height:64px" placeholder="one search per line"></textarea></div>`).join('')}</div>
  <div style="margin-top:10px"><button class="btn primary" id="go">Find reading</button> <span class="mute small">Needs internet; only search terms are sent, never your documents.</span></div></div>
  <div class="card"><div class="row"><b>Search guidelines and other sources yourself:</b>${rd.links.map(([n, u]) => `<a class="btn sm" href="${esc(u)}" target="_blank" rel="noopener">${esc(n)} ↗</a>`).join('')}</div></div>
  <div class="row sp"><h2 style="margin:0">Reading list (${rd.items.length})</h2><span class="row">
  <select id="fl"><option value="">All languages</option>${Object.entries(LANG_NAME).map(([k, n]) => `<option value="${k}" ${f.lang === k ? 'selected' : ''}>${n}</option>`).join('')}</select>
  <select id="fs"><option value="">All</option>${[['suggested', 'New'], ['saved', 'Saved'], ['read', 'Read']].map(([k, n]) => `<option value="${k}" ${f.status === k ? 'selected' : ''}>${n}</option>`).join('')}</select></span></div>
  <div style="margin-top:10px">${items.map(i => `<div class="card" data-id="${i.id}" style="${i.status === 'read' ? 'opacity:.6' : ''}">
    <div class="row sp"><span><span class="badge">${esc(KIND_NAME[i.kind] || i.kind)}</span> <span class="badge">${esc((i.lang || '').toUpperCase())}</span> <span class="mute small">${esc(i.source)}${i.cited ? ' · cited ' + i.cited + '×' : ''}</span></span>
    <span><button class="btn sm ${i.status === 'saved' ? 'primary' : ''}" data-s="saved">${i.status === 'saved' ? '★ Saved' : '☆ Save'}</button> <button class="btn sm" data-s="read">${i.status === 'read' ? 'Unread' : '✓ Read'}</button> <button class="btn sm ghost danger" data-s="dismissed" title="Remove">✕</button></span></div>
    <div style="margin-top:6px"><a href="${esc(i.url)}" target="_blank" rel="noopener" style="font-weight:600">${esc(i.title)} ↗</a></div>
    <div class="mute small">${esc([i.authors, i.venue, i.year].filter(Boolean).join(' · '))}</div>
    <div class="mute small">found with: “${esc(i.why)}”</div></div>`).join('') || '<div class="empty">Nothing yet. Press “Find reading”.</div>'}</div></div>`;
  wireAll(root);
  $('#fl').onchange = e => { S.readFilter = { ...f, lang: e.target.value }; render(); }; $('#fs').onchange = e => { S.readFilter = { ...f, status: e.target.value }; render(); };
  $$('[data-id]', root).forEach(c => $$('[data-s]', c).forEach(b => b.onclick = guard(async () => {
    const i = rd.items.find(x => x.id == c.dataset.id); let st = b.dataset.s; if (st === i.status || (st === 'read' && i.status === 'read')) st = 'suggested';
    await api(`/reading/${i.id}`, { method: 'PATCH', body: { status: st } }); render(); })));
  const langs = () => $$('.lg', root).filter(c => c.checked).map(c => c.value), kinds = () => $$('.kd', root).filter(c => c.checked).map(c => c.value);
  $$('.lg', root).forEach(c => c.onchange = () => { const q = $(`.qrow[data-l=${c.value}]`, root); q.style.display = c.checked ? 'block' : 'none'; });
  $('#kw').onchange = guard(async () => { const r = await api(`/topics/${S.topic}`, { method: 'PATCH', body: { keywords: $('#kw').value } }); const t = S.topics.find(x => x.id === S.topic); if (t) t.keywords = r.keywords; toast('Keywords saved'); });
  const scope = () => ({ topic_id: S.topic, scope: $('input[name=sc]:checked').value, doc_ids: pickedDocs(root), langs: langs() });
  $('#sg').onclick = guard(async () => { $('#sg').textContent = 'Thinking…'; try { const { queries } = await api('/recommend/queries', { method: 'POST', body: scope() });
    Object.keys(LANG_NAME).forEach(l => { $(`.qt[data-l=${l}]`, root).value = (queries[l] || []).join('\n'); }); if (!Object.values(queries).some(v => v.length)) toast('No suggestions. Type your own search terms or fill in the topic keywords.', true); } finally { $('#sg').textContent = '✨ Suggest search terms'; } });
  $('#go').onclick = guard(async () => {
    const queries = {}; $$('.qt', root).forEach(t => { queries[t.dataset.l] = t.value.split('\n').map(x => x.trim()).filter(Boolean); });
    const { job } = await api('/recommend', { method: 'POST', body: { ...scope(), kinds: kinds(), queries } });
    toast('Searching literature databases…'); track();
    waitJob(job).then(r => { if (r.errors?.length) toast('Some sources could not be reached: ' + r.errors.join(', '), true); else toast(`${r.new} new reference(s) added (${r.found} matched your search terms)`); }).catch(() => {});
  });
};

/* ------------------------------------------------------------------ 7 Present */
views.present = async root => {
  if (needTopic(root)) return;
  const [docs, sums, decks] = await Promise.all([api(`/topics/${S.topic}/docs`), api(`/topics/${S.topic}/summaries`), api(`/topics/${S.topic}/decks`)]);
  const ready = docs.filter(d => d.text_len > 0);
  if (S.deckId) { const d = decks.find(x => x.id === S.deckId); if (d) return editDeck(root, S.deckId); S.deckId = null; }
  root.innerHTML = `<div class="wrap"><h1>Present</h1><p class="sub">Turn your documents and summaries into a presentation for your team, class, clients or any audience. Edit the slides, then download a PowerPoint file.</p>
  <div class="disclaimer">Use only fictional examples on slides. Never put personal or confidential data into a presentation.</div>
  <div class="card"><div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div><b>Summaries</b><div class="list-check" style="max-height:200px;overflow:auto">${sums.map(s => `<label><input type="checkbox" class="sck" value="${s.id}"> ${esc(s.title)} <span class="mute small">${s.doc_ids.length > 1 ? 'merged' : ''}</span></label>`).join('') || '<span class="mute small">None yet (Interpret).</span>'}</div></div>
  <div><b>Documents</b><label style="display:flex;gap:8px;margin:4px 0"><input type="checkbox" id="pall"> All documents with “Use” ticked</label>${docChecks(ready).replace(/<label><input type="checkbox" id="dall">.*?<\/label>/, '')}</div></div>
  <div class="row" style="margin-top:12px"><label>Audience <select id="au"><option>colleagues in my team</option><option>management</option><option>classmates or students</option><option>beginners (plain language)</option><option>clients or customers</option><option>an examination committee</option></select></label>
  <label>Length <input type="number" id="mn" value="20" min="5" max="60" style="width:64px"> min</label><label>Language ${langSel('lg')}</label></div>
  <input type="text" id="ex" placeholder="Optional wishes, e.g. 'focus on practical examples, add a short case'" style="width:100%;margin-top:10px">
  <div style="margin-top:12px"><button class="btn primary" id="go">✨ Create presentation</button></div></div>
  <h2>Your presentations</h2>${decks.map(d => `<div class="card row sp" data-id="${d.id}"><span><b>${esc(d.title)}</b> <span class="mute small">${esc(d.updated.replace('T', ' '))}</span></span><span><button class="btn sm op">Open</button> <a class="btn sm" href="/api/decks/${d.id}/export">⬇ .pptx</a> <button class="btn sm ghost danger rm">🗑</button></span></div>`).join('') || '<div class="empty">No presentations yet.</div>'}</div>`;
  wireAll(root);
  $('#go').onclick = guard(async () => {
    const summary_ids = $$(".sck", root).filter(c => c.checked).map(c => +c.value);
    await api('/decks/generate', { method: 'POST', body: { topic_id: S.topic, doc_ids: pickedDocs(root), use_all: $('#pall').checked, summary_ids, audience: $('#au').value, minutes: +$('#mn').value, language: $('#lg').value || undefined, extra: $('#ex').value } });
    toast('Creating the slides in the background…'); track();
  });
  $$('[data-id]', root).forEach(c => { const id = +c.dataset.id;
    $('.op', c).onclick = () => { S.deckId = id; render(); };
    $('.rm', c).onclick = guard(async () => { if (confirm('Delete this presentation?')) { await api(`/decks/${id}`, { method: 'DELETE' }); render(); } }); });
};

async function editDeck(root, id) {
  const d = (await api(`/decks/${id}`)); const deck = d.data;
  const draw = () => {
    root.innerHTML = `<div class="wrap"><div class="row sp"><button class="btn sm" id="back">← All presentations</button><span class="row"><button class="btn sm" id="md">Copy outline</button><a class="btn sm" href="/api/decks/${id}/export?format=md">⬇ .md</a><button class="btn sm primary" id="sv">Save</button><a class="btn sm primary" id="pp" href="/api/decks/${id}/export">⬇ PowerPoint</a></span></div>
    <div class="card" style="margin-top:12px"><input type="text" id="tt" value="${esc(deck.title)}" style="width:100%;font-size:20px;font-weight:700"><input type="text" id="st" value="${esc(deck.subtitle)}" placeholder="Subtitle" style="width:100%;margin-top:6px"></div>
    ${deck.slides.map((s, i) => `<div class="card" data-i="${i}"><div class="row sp"><b class="mute">Slide ${i + 1}</b><span><button class="btn sm ghost" data-a="up">▲</button><button class="btn sm ghost" data-a="down">▼</button><button class="btn sm ghost danger" data-a="del">🗑</button></span></div>
    <input type="text" class="stt" value="${esc(s.title)}" style="width:100%;font-weight:600;margin:6px 0">
    <div class="mute small">Bullets (one per line)</div><textarea class="sbl" style="min-height:90px">${esc(s.bullets.join('\n'))}</textarea>
    <div class="mute small" style="margin-top:6px">Speaker notes</div><textarea class="snt" style="min-height:60px">${esc(s.notes || '')}</textarea></div>`).join('')}
    <button class="btn" id="add">+ Add slide</button>
    ${deck.sources?.length ? `<p class="mute small">A final “Sources” slide lists: ${esc(deck.sources.join(', '))}</p>` : ''}</div>`;
    $('#back').onclick = () => { S.deckId = null; render(); };
    $('#add').onclick = () => { collect(); deck.slides.push({ title: 'New slide', bullets: [], notes: '' }); draw(); };
    $$('[data-i]', root).forEach(c => { const i = +c.dataset.i; $$('[data-a]', c).forEach(b => b.onclick = () => { collect(); const a = b.dataset.a;
      if (a === 'del') deck.slides.splice(i, 1); else { const j = a === 'up' ? i - 1 : i + 1; if (j >= 0 && j < deck.slides.length) [deck.slides[i], deck.slides[j]] = [deck.slides[j], deck.slides[i]]; } draw(); }); });
    $('#sv').onclick = guard(save); $('#pp').onclick = async e => { e.preventDefault(); await save(true); location.href = `/api/decks/${id}/export`; };
    $('#md').onclick = () => { collect(); navigator.clipboard.writeText(deck.slides.map((s, i) => `${i + 1}. ${s.title}\n${s.bullets.map(b => '   - ' + b).join('\n')}`).join('\n\n')).then(() => toast('Outline copied')); };
  };
  const collect = () => { deck.title = $('#tt').value; deck.subtitle = $('#st').value;
    $$('[data-i]', root).forEach(c => { const s = deck.slides[+c.dataset.i]; if (!s) return; s.title = $('.stt', c).value; s.bullets = $('.sbl', c).value.split('\n').map(x => x.trim()).filter(Boolean); s.notes = $('.snt', c).value; }); };
  const save = async quiet => { collect(); await api(`/decks/${id}`, { method: 'PUT', body: { data: deck } }); if (quiet !== true) toast('Saved'); };
  draw();
}
