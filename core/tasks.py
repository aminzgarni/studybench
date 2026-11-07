"""AI-driven tasks that combine retrieval, prompts and the database."""
import hashlib
import json
import random
import re
import time
from pathlib import Path

from . import config, db, literature, llm, prompts, rag

TYPES = ["mcq", "case", "open", "audit", "jury", "elab"]


def lang_of(language=None):
    return language or config.get()["content_language"]


def budget():
    return int(config.get()["context_chars"])


def topic_goal(topic_id):
    t = db.one("SELECT goal FROM topics WHERE id=?", (topic_id,))
    return (t or {}).get("goal", "")


def doc_text(doc):
    p = config.HOME / "topics" / str(doc["topic_id"]) / "text" / f"{doc['id']}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ------------------------------------------------------------------ auto-tag
def autotag(doc_ids, job):
    known = sorted({r["category"] for r in db.q("SELECT DISTINCT category FROM documents WHERE category!=''")})
    done = 0
    for did in doc_ids:
        job.check()
        d = db.one("SELECT * FROM documents WHERE id=?", (did,))
        text = doc_text(d)[:1800] if d else ""
        if not d or not text.strip():
            continue
        try:
            r = llm.complete_json(prompts.p_tags(d["filename"], text, known), temperature=0.1, max_tokens=400)
        except llm.LLMError:
            raise
        if not isinstance(r, dict):
            continue
        tags = [str(t).lower()[:30] for t in (r.get("tags") or [])][:5]
        cat = str(r.get("category") or "")[:40]
        old_tags = json.loads(d["tags"] or "[]")
        sets, args = [], []
        if not old_tags and tags:
            sets.append("tags=?"), args.append(json.dumps(tags, ensure_ascii=False))
        if not d["category"] and cat:
            sets.append("category=?"), args.append(cat)
            known = sorted(set(known) | {cat})
        pr = r.get("priority")
        if not d["priority_set"] and isinstance(pr, int) and 1 <= pr <= 5:
            sets.append("priority=?"), args.append(pr)
        if sets:
            db.x(f"UPDATE documents SET {','.join(sets)} WHERE id=?", (*args, did))
        done += 1
        job.set(done / len(doc_ids), d["filename"])
    return {"tagged": done}


# ------------------------------------------------------------------ summaries
LEVELS = {"quick": (120, 420), "standard": (250, 750), "detailed": (420, 1200)}   # words and max tokens per section
SECTION_HEAD = {"de": "Abschnitt für Abschnitt", "en": "Section by section", "fr": "Section par section"}
SECTION_RE = re.compile(r"\n## (?:Section by section|Abschnitt für Abschnitt|Section par section)\b.*", re.S)


def part_budget():
    """Characters of source text per step. Bigger steps = fewer AI calls (the main cost for long documents)."""
    s = config.get()
    cc = int(s["context_chars"])
    if s["provider"] == "ollama":
        return min(int(cc * 1.3), int((int(s["ollama"].get("num_ctx", 8192)) - 2500) * 2.8))
    return cc


def fast_model():
    s = config.get()
    return (s["ollama"].get("fast_model") or None) if s["provider"] == "ollama" else None


def range_label(chunks):
    locs = [c["locator"] for c in chunks if c.get("locator")]
    if not locs:
        return ""
    a, b = locs[0], locs[-1]
    ma, mb = re.match(r"Page (\d+)", a), re.match(r"Page (\d+)", b)
    if ma and mb:
        return f"pp. {ma.group(1)}-{mb.group(1)}" if ma.group(1) != mb.group(1) else f"p. {ma.group(1)}"
    return a if a == b else f"{a} → {b}"


def _eta(t0, done, total):
    if not done:
        return ""
    left = (time.time() - t0) / done * (total - done)
    return f" · about {max(1, round(left / 60))} min left" if left > 45 else " · almost done"


def part_notes(text, language, level, job):
    """Notes for one section of a long document. Cached, so a cancelled or failed run can resume where it stopped."""
    words, tokens = LEVELS[level]
    model = fast_model()
    key = hashlib.sha1(f"{language}|{level}|{model}|{text}".encode()).hexdigest()
    hit = db.one("SELECT content FROM notes WHERE key=?", (key,))
    if hit:
        return hit["content"]
    out = llm.complete(prompts.p_notes(text, language, words), max_tokens=tokens, model=model).strip()
    db.x("INSERT OR REPLACE INTO notes(key,content,created) VALUES(?,?,?)", (key, out, db.iso()))
    return out


def _split_title(note):
    lines = note.strip().split("\n", 1)
    first = lines[0].strip().strip("#*").strip()
    if len(lines) == 2 and first and not lines[0].lstrip().startswith(("-", "•", "*  ")) and len(first) < 140:
        return first, lines[1].strip()
    return "", note.strip()


def _digest(sections, limit):
    """Shortened view of all section notes, used to write the overview of a long document."""
    for bullets in (3, 1, 0):
        out = []
        for label, title, body in sections:
            bl = [l.strip() for l in body.split("\n") if l.strip().startswith(("-", "•", "*"))][:bullets]
            out.append(f"[{label}] {title}\n" + "\n".join(l[:170] for l in bl))
        txt = "\n\n".join(out)
        if len(txt) <= limit:
            return txt
    return txt[:limit]


def summarize_doc(doc, language, job, lo=0.0, hi=1.0, focus="", level="standard"):
    level = level if level in LEVELS else "standard"
    chunks = [c for c in rag.load_chunks([doc["id"]]) if c["text"].strip()]
    if not chunks:
        raise ValueError(f"'{doc['filename']}' has no text yet (transcribe or OCR it first).")
    goal = topic_goal(doc["topic_id"])
    parts = rag.pack(chunks, part_budget())
    if len(parts) == 1:
        job.set(lo + (hi - lo) * 0.3, f"{doc['filename']}")
        material = "\n\n".join(rag.fmt(c) for c in parts[0])
        return llm.complete(prompts.p_summary(material, language, doc["filename"], goal, focus=focus),
                            max_tokens=2200).strip()
    sections, t0 = [], time.time()
    for i, part in enumerate(parts):
        job.check()
        job.set(lo + (hi - lo) * 0.9 * i / len(parts), f"{doc['filename']}: section {i + 1}/{len(parts)}{_eta(t0, i, len(parts))}")
        note = part_notes("\n\n".join(rag.fmt(c) for c in part), language, level, job)
        title, body = _split_title(note)
        sections.append((range_label(part), title, body))
    job.check()
    job.set(lo + (hi - lo) * 0.92, f"{doc['filename']}: writing the overview")
    digest = _digest(sections, part_budget())
    overview = llm.complete(prompts.p_summary(digest, language, doc["filename"], goal, focus=focus, digest=True),
                            max_tokens=2200).strip()
    head = SECTION_HEAD.get(language, SECTION_HEAD["en"])
    body = "\n\n".join(f"### {t or 'Section ' + str(n)}{' (' + lab + ')' if lab else ''}\n{b}"
                       for n, (lab, t, b) in enumerate(sections, 1))
    return f"{overview}\n\n## {head}\n{body}"


def summarize(topic_id, doc_ids, language, combine, job, focus="", level="standard"):
    language = lang_of(language)
    docs = [d for d in (db.one("SELECT * FROM documents WHERE id=?", (i,)) for i in doc_ids) if d]
    ids = []
    ins = "INSERT INTO summaries(topic_id,doc_ids,title,language,content,created) VALUES(?,?,?,?,?,?)"
    if not combine:
        for n, d in enumerate(docs):
            job.set(n / len(docs), d["filename"])
            text = summarize_doc(d, language, job, n / len(docs), (n + 1) / len(docs), focus, level)
            ids.append(db.x(ins, (topic_id, json.dumps([d["id"]]), d["filename"], language, text, db.iso())))
        return {"summaries": ids}
    mats = []
    for n, d in enumerate(docs):
        job.check()
        old = db.one("SELECT content FROM summaries WHERE doc_ids=? ORDER BY id DESC", (json.dumps([d["id"]]),))
        text = old["content"] if old else summarize_doc(d, language, job, n / (len(docs) + 1), (n + 1) / (len(docs) + 1), focus, level)
        if not old:
            db.x(ins, (topic_id, json.dumps([d["id"]]), d["filename"], language, text, db.iso()))
        mats.append(f"## DOCUMENT: {d['filename']}\n{SECTION_RE.sub('', text)}")     # overview part only
    material = "\n\n".join(mats)
    if len(material) > part_budget():
        per = part_budget() // len(mats)
        material = "\n\n".join(m[:per] for m in mats)
    title = "Combined: " + ", ".join(d["filename"] for d in docs[:3]) + ("…" if len(docs) > 3 else "")
    out = llm.complete(prompts.p_summary(material, language, title, topic_goal(topic_id), multi=True, focus=focus),
                       max_tokens=2500).strip()
    return {"summaries": [db.x(ins, (topic_id, json.dumps([d["id"] for d in docs]), title, language, out, db.iso()))]}


# ------------------------------------------------------------------ exercises
def _clean_list(v):
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def normalize_item(typ, raw):
    """Validate an AI item. Returns (question, data, source) or None."""
    if not isinstance(raw, dict):
        return None
    src = str(raw.get("source") or "")[:200]
    key = _clean_list(raw.get("key_points"))
    model = str(raw.get("model_answer") or "").strip()
    if typ == "mcq":
        opts = _clean_list(raw.get("options"))
        q = str(raw.get("question") or "").strip()
        try:
            ai = int(raw.get("answer_index"))
        except (TypeError, ValueError):
            return None
        if not q or len(opts) < 3 or not 0 <= ai < len(opts):
            return None
        order = list(range(len(opts)))
        random.shuffle(order)                       # models love putting the answer at B or C
        opts2 = [opts[i] for i in order]
        return q, {"options": opts2, "answer_index": order.index(ai),
                   "explanation": str(raw.get("explanation") or "")}, src
    if typ == "case":
        v = str(raw.get("vignette") or raw.get("question") or "").strip()
        tasks = _clean_list(raw.get("tasks"))
        if not v:
            return None
        return v, {"tasks": tasks, "key_points": key, "model_answer": model}, src
    q = str(raw.get("question") or "").strip()
    if not q:
        return None
    data = {"key_points": key, "model_answer": model}
    if typ == "audit":
        data["expected_evidence"] = _clean_list(raw.get("expected_evidence"))
        data["pitfalls"] = _clean_list(raw.get("pitfalls"))
    elif typ == "jury":
        data["follow_ups"] = _clean_list(raw.get("follow_ups"))
    elif typ == "elab":
        data["kind"] = str(raw.get("kind") or "why")
    return q, data, src


def match_doc(source, doc_ids):
    if len(doc_ids) == 1:
        return doc_ids[0]
    for did in doc_ids:
        d = db.one("SELECT filename FROM documents WHERE id=?", (did,))
        if d and d["filename"] and d["filename"].lower() in source.lower():
            return did
    return None


def generate_items(topic_id, doc_ids, types, n, difficulty, language, job):
    language = lang_of(language)
    goal = topic_goal(topic_id)
    total, made_all = len(types) * n, 0
    for typ in types:
        made, attempts = 0, 0
        while made < n and attempts < n + 2:
            job.check()
            k = min(5, n - made)
            ctx, picked = rag.sample_context(doc_ids, budget())
            if not picked:
                raise ValueError("The selected documents have no text yet.")
            existing = [r["question"][:140] for r in db.q(
                "SELECT question FROM items WHERE topic_id=? AND type=? ORDER BY id DESC LIMIT 12", (topic_id, typ))]
            res = llm.complete_json(prompts.p_items(typ, k, ctx, difficulty, language, goal, existing),
                                    temperature=0.7, max_tokens=3500)
            raw = res.get("items") if isinstance(res, dict) else res
            for r in (raw or [])[:k]:
                it = normalize_item(typ, r)
                if not it:
                    continue
                q, data, src = it
                db.x("INSERT INTO items(topic_id,doc_id,type,question,data,source,created,due) VALUES(?,?,?,?,?,?,?,?)",
                     (topic_id, match_doc(src, doc_ids), typ, q, json.dumps(data, ensure_ascii=False), src, db.iso(), db.iso()))
                made += 1
                made_all += 1
            attempts += 1
            job.set(min(0.99, (made_all) / total), f"{typ}: {made}/{n}")
    return {"created": made_all}


def evaluate(item, answer, language=None):
    return llm.complete_json(prompts.p_eval(item, answer, lang_of(language)), temperature=0.1, max_tokens=900)


# ------------------------------------------------------------------ chat
def included_docs(topic_id):
    return [r["id"] for r in db.q(
        "SELECT id FROM documents WHERE topic_id=? AND include=1 AND text_len>0", (topic_id,))]


def chat_stream(topic_id, doc_ids, message, history, allow_general):
    doc_ids = doc_ids or included_docs(topic_id)
    query = message
    if len(message.split()) < 6 and history:
        prev = [m["content"] for m in history if m["role"] == "user"]
        if prev:
            query = prev[-1] + " " + message
    hits = rag.search(doc_ids, query, k=6) if doc_ids else []
    sources = [{"n": i, "doc_id": h["doc_id"], "filename": h["filename"], "locator": h["locator"],
                "snippet": h["text"][:500]} for i, h in enumerate(hits, 1)]
    yield {"type": "sources", "sources": sources}
    if not hits:
        yield {"type": "delta", "text": "Not in your sources. I found no matching passage in the selected documents."
               if not allow_general else ""}
        if not allow_general:
            yield {"type": "done"}
            return
    msgs = prompts.p_chat(hits, history, message, allow_general)
    full = []
    for piece in llm.stream(msgs):
        full.append(piece)
        yield {"type": "delta", "text": piece}
    db.x("INSERT INTO chat(topic_id,ts,role,content,sources) VALUES(?,?,?,?,?)",
         (topic_id, db.iso(), "user", message, "[]"))
    db.x("INSERT INTO chat(topic_id,ts,role,content,sources) VALUES(?,?,?,?,?)",
         (topic_id, db.iso(), "assistant", "".join(full), json.dumps(sources, ensure_ascii=False)))
    yield {"type": "done"}


# ------------------------------------------------------------------ recommend
def _headings(text, limit=14):
    return [h.strip() for h in re.findall(r"^###? +(.+)$", text, re.M)
            if h.strip().lower() not in {x.lower() for x in ("Essence (2-3 sentences)", "Essence", "Key points", "Kernaussage", "Kernpunkte")}][:limit]


def lit_context(topic_id, scope, doc_ids):
    t = db.one("SELECT name, goal, keywords FROM topics WHERE id=?", (topic_id,))
    parts = [f"Topic: {t['name']}"]
    if t.get("keywords"):
        parts.append(f"Core keywords chosen by the learner: {t['keywords']}")
    cats = db.q("SELECT DISTINCT category, tags FROM documents WHERE topic_id=? AND include=1", (topic_id,))
    tags = sorted({x for c in cats for x in ([c['category']] if c['category'] else []) + json.loads(c['tags'] or '[]')})
    if tags:
        parts.append("Categories and tags: " + ", ".join(tags[:25]))
    if scope == "weak":
        weak = [i for i in db.q("SELECT * FROM items WHERE topic_id=? AND suspended=0", (topic_id,))
                if i["flag"] or i["lapses"] >= 1 or i["last_grade"] == 0]
        weak.sort(key=lambda i: -i["lapses"])
        if not weak:
            return None
        parts.append("Questions the learner struggles with:\n- " + "\n- ".join(w["question"][:220] for w in weak[:15]))
        return "\n".join(parts), t
    ids = doc_ids or included_docs(topic_id) if scope == "docs" else included_docs(topic_id)
    sums = db.q("SELECT doc_ids, content FROM summaries WHERE topic_id=? ORDER BY id DESC", (topic_id,))
    heads, essence = [], []
    for sm in sums:
        sd = json.loads(sm["doc_ids"])
        if scope == "docs" and not set(sd) & set(ids):
            continue
        heads += _headings(sm["content"])
        m = re.search(r"^## +Essence[^\n]*\n(.+?)(?=\n## )", sm["content"], re.S | re.M) or re.search(r"^## +Kernaussage[^\n]*\n(.+?)(?=\n## )", sm["content"], re.S | re.M)
        if m:
            essence.append(m.group(1).strip()[:500])
    if heads:
        parts.append("Section titles of the course material:\n- " + "\n- ".join(list(dict.fromkeys(heads))[:30]))
    if essence:
        parts.append("Essence of the material:\n" + "\n".join(essence[:3]))
    if not heads and not essence:                         # no summaries yet: sample the raw text
        ctx, _ = rag.sample_context(ids, 5000)
        parts.append("Material (excerpts):\n" + ctx)
    return "\n".join(parts), t


def suggest_queries(topic_id, scope, doc_ids, langs):
    got = lit_context(topic_id, scope, doc_ids)
    if got is None:
        raise ValueError("No weak points yet. Practise first, or choose another basis.")
    ctx, t = got
    kw = (t.get("keywords") or "").strip()
    try:
        qs = llm.complete_json(prompts.p_lit_queries(ctx, langs, t["goal"], kw), temperature=0.2, max_tokens=1000)
        qs = {k: [str(x).strip() for x in v if str(x).strip()][:6] for k, v in qs.items() if isinstance(v, list) and k in langs} if isinstance(qs, dict) else {}
    except llm.LLMError:
        qs = {}
    for l in langs:                                       # fallback without AI: the learner's own keywords
        if not qs.get(l) and kw and l in ("de", "en"):
            qs[l] = [kw]
    return qs


def recommend(topic_id, scope, doc_ids, langs, kinds, queries, job):
    job.set(0.02, "choosing search terms")
    if not queries or not any(queries.values()):
        queries = suggest_queries(topic_id, scope, doc_ids, langs)
    queries = {l: [q for q in (queries.get(l) or []) if q.strip()] for l in langs}
    if not any(queries.values()):
        raise ValueError("No search terms. Add keywords to the topic (Edit topic) or type search terms yourself.")
    items, errors = literature.search(queries, langs, kinds, job)
    new = 0
    for r in reversed(items):                              # best first in the list (shown newest first)
        if db.one("SELECT id FROM reading WHERE topic_id=? AND url=?", (topic_id, r["url"])):
            continue
        db.x("""INSERT INTO reading(topic_id,title,authors,year,venue,lang,kind,url,source,why,cited,added)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (topic_id, r["title"], r["authors"], r["year"], r["venue"], r["lang"], r["kind"],
                                                       r["url"], r["source"], r["why"], r["cited"], db.iso()))
        new += 1
    return {"new": new, "found": len(items), "errors": errors, "queries": queries}


# ------------------------------------------------------------------ present
def _para_chunks(name, text):
    out, loc = [], ""
    for block in re.split(r"\n{2,}", text):
        h = re.match(r"^#{2,4} +(.+)", block.strip())
        if h:
            loc = h.group(1).strip()
        if block.strip():
            out.append({"filename": name, "locator": loc, "text": block.strip(), "doc_id": None})
    return out


def make_deck(topic_id, doc_ids, summary_ids, audience, minutes, language, extra, job):
    language = lang_of(language)
    n = max(4, min(30, round(minutes / 2)))
    goal = topic_goal(topic_id)
    corpus, names, overview = [], [], []
    for sid in summary_ids:
        sm = db.one("SELECT title, content FROM summaries WHERE id=?", (sid,))
        if sm:
            corpus += _para_chunks(f"summary: {sm['title']}", sm["content"])
            overview.append(f"## {sm['title']}\n{SECTION_RE.sub('', sm['content'])}" + "\nSections: " + "; ".join(_headings(sm["content"], 40)))
            names.append(sm["title"])
    for did in doc_ids:
        d = db.one("SELECT filename FROM documents WHERE id=?", (did,))
        if d:
            names.append(d["filename"])
            if not summary_ids:                                           # no summary chosen: use an existing one for the overview
                sm = db.one("SELECT content FROM summaries WHERE doc_ids=? ORDER BY id DESC", (json.dumps([did]),))
                if sm:
                    overview.append(f"## {d['filename']}\n{SECTION_RE.sub('', sm['content'])}\nSections: " + "; ".join(_headings(sm["content"], 40)))
    corpus += rag.load_chunks(doc_ids)
    corpus = [c for c in corpus if c["text"].strip()]
    if not corpus:
        raise ValueError("Choose at least one document or summary with text")
    if not overview:                                                      # raw documents only: sample for the outline
        ctx, _ = rag.sample_context(doc_ids, part_budget())
        overview.append(ctx)
    index = rag.Index(corpus)
    job.set(0.03, "planning the slides")
    plan = llm.complete_json(prompts.p_outline("\n\n".join(overview)[:part_budget()], audience, n, minutes, language, goal, extra),
                             temperature=0.4, max_tokens=2500)
    plan_slides = [x for x in (plan.get("slides") if isinstance(plan, dict) else []) or [] if isinstance(x, dict) and x.get("title")]
    if not plan_slides:
        raise llm.LLMError("The AI did not produce a slide plan. Try again.")
    slides, t0 = [], time.time()
    for i, ps in enumerate(plan_slides[:n + 3]):
        job.check()
        job.set(0.08 + 0.9 * i / len(plan_slides), f"slide {i + 1}/{len(plan_slides)}: {ps['title'][:40]}{_eta(t0, i, len(plan_slides))}")
        hits = index.search(f"{ps['title']} {ps.get('focus', '')} {ps.get('query', '')}", k=5)
        passages, used = [], 0
        for h in hits:
            if used + len(h["text"]) > part_budget() * 0.7:
                break
            passages.append(rag.fmt(h))
            used += len(h["text"])
        bullets, notes = [], ""
        if passages:
            try:
                r = llm.complete_json(prompts.p_slide(ps["title"], ps.get("focus", ""), "\n\n".join(passages), audience, language),
                                      temperature=0.4, max_tokens=900)
                if isinstance(r, dict):
                    bullets, notes = _clean_list(r.get("bullets"))[:8], str(r.get("notes") or "").strip()
            except llm.LLMError:
                pass
        if not bullets:                                                   # AI step failed: fall back to the passage's own lines
            bullets = [l.strip("-• ").strip()[:110] for l in (hits[0]["text"].split("\n") if hits else []) if len(l.strip()) > 20][:4]
        slides.append({"title": str(ps["title"]).strip(), "bullets": bullets, "notes": notes})
    deck = {"title": str(plan.get("title") or "Presentation"), "subtitle": str(plan.get("subtitle") or ""),
            "slides": slides, "sources": sorted(set(names))}
    did = db.x("INSERT INTO decks(topic_id,title,data,language,created,updated) VALUES(?,?,?,?,?,?)",
               (topic_id, deck["title"], json.dumps(deck, ensure_ascii=False), language, db.iso(), db.iso()))
    return {"deck_id": did}
