"""StudyBench - local study tool. Run:  python app.py   (then open the address it prints, default http://127.0.0.1:8731)"""
import atexit
import json
import os
import platform
import random
import re
import shutil
import threading
import time
import uuid
import webbrowser
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from core import config, db, deck as deckmod, extract, jobs, llm, prompts, rag, setup, srs, tasks, transcribe

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = None


def api(fn):
    @wraps(fn)
    def w(*a, **kw):
        try:
            return fn(*a, **kw)
        except llm.LLMError as e:
            return jsonify(error=str(e)), 502
        except (ValueError, KeyError) as e:
            return jsonify(error=str(e.args[0]) if e.args else str(e)), 400
        except Exception as e:  # noqa: BLE001
            app.logger.exception("error")
            return jsonify(error=f"{e.__class__.__name__}: {e}"), 500
    return w


def body():
    return request.get_json(silent=True) or {}


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ====================================================================== settings
@app.get("/api/settings")
@api
def get_settings():
    return jsonify(config.public())


@app.post("/api/settings")
@api
def post_settings():
    config.update(body())
    return jsonify(config.public())


@app.get("/api/llm/test")
@api
def llm_test():
    ok, msg = llm.ping()
    return jsonify(ok=ok, message=msg)


@app.get("/api/setup")
@api
def setup_state():
    return jsonify(setup.listing())


@app.post("/api/llm/pull")
@api
def llm_pull():
    name = (body().get("model") or config.get()["ollama"]["model"]).strip()
    threading.Thread(target=lambda: _quiet(setup.pull_model, name), daemon=True).start()
    return jsonify(ok=True)


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception:  # noqa: BLE001 - recorded in setup.STATE
        pass


@app.get("/api/llm/models")
@api
def llm_models():
    try:
        return jsonify(models=llm.list_models())
    except Exception:  # noqa: BLE001
        return jsonify(models=[])


# ====================================================================== topics
def topic_dir(tid):
    d = config.HOME / "topics" / str(tid)
    (d / "files").mkdir(parents=True, exist_ok=True)
    (d / "text").mkdir(exist_ok=True)
    return d


@app.get("/api/topics")
@api
def list_topics():
    return jsonify(db.q("SELECT * FROM topics ORDER BY ord, id"))


@app.post("/api/topics")
@api
def add_topic():
    b = body()
    name = (b.get("name") or "").strip()
    if not name:
        raise ValueError("Name required")
    if db.one("SELECT id FROM topics WHERE name=?", (name,)):
        raise ValueError("A topic with this name exists")
    n = (db.one("SELECT COALESCE(MAX(ord),0)+1 AS n FROM topics") or {"n": 1})["n"]
    tid = db.x("INSERT INTO topics(name,ord,goal,keywords,created) VALUES(?,?,?,?,?)",
               (name, n, b.get("goal", ""), b.get("keywords", ""), db.iso()))
    topic_dir(tid)
    return jsonify(db.one("SELECT * FROM topics WHERE id=?", (tid,)))


@app.patch("/api/topics/<int:tid>")
@api
def edit_topic(tid):
    b = body()
    for k in ("name", "goal", "ord", "keywords"):
        if k in b:
            db.x(f"UPDATE topics SET {k}=? WHERE id=?", (b[k], tid))
    return jsonify(db.one("SELECT * FROM topics WHERE id=?", (tid,)))


@app.delete("/api/topics/<int:tid>")
@api
def del_topic(tid):
    for d in db.q("SELECT id FROM documents WHERE topic_id=?", (tid,)):
        rag.delete_chunks(d["id"])
    for t in ("documents", "summaries", "items", "reviews", "chat"):
        db.x(f"DELETE FROM {t} WHERE topic_id=?", (tid,))
    db.x("DELETE FROM topics WHERE id=?", (tid,))
    shutil.rmtree(config.HOME / "topics" / str(tid), ignore_errors=True)
    return jsonify(ok=True)


# ====================================================================== documents
def doc_row(did):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise ValueError("Unknown document")
    r = config.resolve(d["stored_path"])              # stored relative to the data folder: same on Windows and Mac
    d["stored_path"] = str(r) if r else ""
    return d


def set_doc_text(doc, text, status="ready", msg=""):
    p = topic_dir(doc["topic_id"]) / "text" / f"{doc['id']}.md"
    p.write_text(text, encoding="utf-8")
    db.x("UPDATE documents SET text_len=?, status=?, status_msg=? WHERE id=?", (len(text), status, msg, doc["id"]))
    rag.store_chunks(doc["id"], doc["topic_id"], text)


def need_file(doc):
    if not doc["stored_path"]:
        raise ValueError(f"The file “{doc['filename']}” is not available on this computer. If it was imported "
                         "without copying, add it again; otherwise wait until your cloud drive has finished syncing.")
    return Path(doc["stored_path"])


def run_extract(doc):
    need_file(doc)
    res = extract.extract(Path(doc["stored_path"]), doc["kind"])
    if res.text.strip():
        set_doc_text(doc, res.text, "ready", res.msg)
    else:
        db.x("UPDATE documents SET status=?, status_msg=? WHERE id=?", (res.status, res.msg, doc["id"]))


def ingest(tid, src: Path, name=None, copy=True):
    """Register a file. Returns (doc_id, duplicate?)."""
    h = extract.sha256(src)
    dup = db.one("SELECT id FROM documents WHERE topic_id=? AND sha256=?", (tid, h))
    if dup:
        return dup["id"], True
    name = name or src.name
    kind = extract.classify(src)
    if copy:
        dest = topic_dir(tid) / "files" / f"{uuid.uuid4().hex[:8]}_{secure_filename(name) or 'file'}"
        shutil.copy2(src, dest)
    else:
        dest = src
    seq = db.one("SELECT COALESCE(MAX(seq),0)+1 AS n FROM documents WHERE topic_id=?", (tid,))["n"]
    status = "pending_transcription" if kind == "audio" else "unsupported" if kind == "unsupported" else "new"
    msg = ""
    if kind == "unsupported":
        ext = src.suffix.lower()
        msg = ("Old/other Office format: save as .pptx / .docx / PDF and upload again."
               if ext in extract.LEGACY else "Unsupported file type")
    did = db.x("""INSERT INTO documents(topic_id,filename,kind,stored_path,sha256,size,status,status_msg,seq,added)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (tid, name, kind, config.rel_path(dest), h, src.stat().st_size, status, msg, seq, db.iso()))
    if kind in ("slides", "document", "pdf", "text", "image"):
        run_extract(doc_row(did))
    return did, False


def doc_out(d, summ_docs, item_counts):
    d = dict(d)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["has_summary"] = d["id"] in summ_docs
    d["n_items"] = item_counts.get(d["id"], 0)
    d.pop("stored_path", None)
    return d


@app.get("/api/topics/<int:tid>/docs")
@api
def list_docs(tid):
    singles = set()
    for s in db.q("SELECT doc_ids FROM summaries WHERE topic_id=?", (tid,)):
        ids = json.loads(s["doc_ids"])
        if len(ids) == 1:
            singles.add(ids[0])
    counts = {r["doc_id"]: r["n"] for r in db.q(
        "SELECT doc_id, COUNT(*) n FROM items WHERE topic_id=? AND doc_id IS NOT NULL GROUP BY doc_id", (tid,))}
    rows = db.q("SELECT * FROM documents WHERE topic_id=? ORDER BY seq, id", (tid,))
    return jsonify([doc_out(r, singles, counts) for r in rows])


@app.post("/api/topics/<int:tid>/upload")
@api
def upload(tid):
    res = []
    tmp = config.tmp_dir()
    ok_ext = config.allowed_exts()
    for f in request.files.getlist("files"):
        name = Path(f.filename or "file").name
        if Path(name).suffix.lower().lstrip(".") not in ok_ext:
            res.append({"name": name, "skipped": True})
            continue
        p = tmp / f"{uuid.uuid4().hex}_{secure_filename(name) or 'file'}"
        f.save(p)
        try:
            did, dup = ingest(tid, p, name=name, copy=True)
            res.append({"id": did, "name": name, "duplicate": dup})
        finally:
            p.unlink(missing_ok=True)
    return jsonify(res)


@app.post("/api/topics/<int:tid>/import-folder")
@api
def import_folder(tid):
    b = body()
    folder = Path(os.path.expanduser(b.get("path", "").strip().strip('"')))
    if not folder.is_dir():
        raise ValueError(f"Folder not found: {folder}")
    copy = bool(b.get("copy", True))
    ok_ext = config.allowed_exts()
    every = [p for p in (folder.rglob("*") if b.get("recursive", True) else folder.glob("*"))
             if p.is_file() and not p.name.startswith(("~$", ".")) and p.name.lower() != "thumbs.db"]
    files = [p for p in every if p.suffix.lower().lstrip(".") in ok_ext]
    skipped = len(every) - len(files)
    files.sort(key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))])

    def work(job):
        added = dup = 0
        for i, p in enumerate(files):
            job.check()
            job.set(i / max(1, len(files)), p.name)
            try:
                _, d = ingest(tid, p, copy=copy)
                dup += d
                added += not d
            except Exception as e:  # noqa: BLE001
                job.message = f"{p.name}: {e}"
        return {"added": added, "duplicates": dup, "total": len(files), "skipped": skipped}
    j = jobs.submit("import", f"Import {folder.name}", work, topic_id=tid)
    return jsonify(job=j.out())


@app.patch("/api/docs/<int:did>")
@api
def edit_doc(did):
    b = body()
    doc_row(did)
    if "priority" in b:
        db.x("UPDATE documents SET priority=?, priority_set=1 WHERE id=?", (max(1, min(5, int(b["priority"]))), did))
    for k in ("category", "note", "language", "filename"):
        if k in b:
            db.x(f"UPDATE documents SET {k}=? WHERE id=?", (str(b[k])[:200], did))
    if "tags" in b:
        tags = b["tags"] if isinstance(b["tags"], list) else [t.strip() for t in str(b["tags"]).split(",")]
        db.x("UPDATE documents SET tags=? WHERE id=?",
             (json.dumps([t.strip().lower() for t in tags if t.strip()][:10], ensure_ascii=False), did))
    if "include" in b:
        db.x("UPDATE documents SET include=? WHERE id=?", (1 if b["include"] else 0, did))
    if "linked_to" in b:
        db.x("UPDATE documents SET linked_to=? WHERE id=?", (b["linked_to"] or None, did))
    if b.get("kind") in ("text", "transcript"):
        db.x("UPDATE documents SET kind=? WHERE id=?", (b["kind"], did))
    return jsonify(ok=True)


@app.post("/api/topics/<int:tid>/reorder")
@api
def reorder(tid):
    for i, did in enumerate(body().get("ids", []), 1):
        db.x("UPDATE documents SET seq=? WHERE id=? AND topic_id=?", (i, did, tid))
    return jsonify(ok=True)


@app.delete("/api/docs/<int:did>")
@api
def del_doc(did):
    d = doc_row(did)
    rag.delete_chunks(did)
    db.x("UPDATE items SET doc_id=NULL WHERE doc_id=?", (did,))
    db.x("UPDATE documents SET parent_id=NULL WHERE parent_id=?", (did,))
    db.x("UPDATE documents SET linked_to=NULL WHERE linked_to=?", (did,))
    db.x("DELETE FROM documents WHERE id=?", (did,))
    sp = Path(d["stored_path"] or "")
    if config.HOME in sp.parents:
        sp.unlink(missing_ok=True)
    (config.HOME / "topics" / str(d["topic_id"]) / "text" / f"{did}.md").unlink(missing_ok=True)
    return jsonify(ok=True)


@app.get("/api/docs/<int:did>/text")
@api
def get_doc_text(did):
    d = doc_row(did)
    return jsonify(text=tasks.doc_text(d), filename=d["filename"], kind=d["kind"], status=d["status"])


@app.put("/api/docs/<int:did>/text")
@api
def put_doc_text(did):
    d = doc_row(did)
    set_doc_text(d, body().get("text", ""))
    return jsonify(ok=True)


@app.get("/api/docs/<int:did>/file")
@api
def get_doc_file(did):
    d = doc_row(did)
    return send_file(need_file(d), download_name=d["filename"])


@app.post("/api/topics/<int:tid>/autotag")
@api
def autotag(tid):
    ids = body().get("doc_ids") or [d["id"] for d in db.q("SELECT id FROM documents WHERE topic_id=? AND text_len>0", (tid,))]
    j = jobs.submit("autotag", "Suggest tags & priority", lambda job: tasks.autotag(ids, job), topic_id=tid)
    return jsonify(job=j.out())


@app.post("/api/docs/<int:did>/extract")
@api
def re_extract(did):
    run_extract(doc_row(did))
    return jsonify(ok=True)


# ====================================================================== transcription
@app.get("/api/transcribe/status")
@api
def transcribe_status():
    return jsonify(available=transcribe.available(), ocr=extract.ocr_available(),
                   ffmpeg=bool(shutil.which("ffmpeg")))


def transcribe_doc(did, opts, link_to, job):
    d = doc_row(did)
    job.set(0.0, d["filename"])
    md, info = transcribe.run(need_file(d), opts, job.set, job.check)
    existing = db.one("SELECT * FROM documents WHERE parent_id=? AND kind='transcript'", (did,))
    stem = Path(d["filename"]).stem
    if existing:
        tdoc = existing
    else:
        seq = db.one("SELECT COALESCE(MAX(seq),0)+1 AS n FROM documents WHERE topic_id=?", (d["topic_id"],))["n"]
        tid_ = db.x("""INSERT INTO documents(topic_id,filename,kind,stored_path,status,seq,parent_id,linked_to,
                       priority,category,tags,added) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (d["topic_id"], f"{stem} (transcript).md", "transcript", "", "ready", seq, did, link_to or None,
                     d["priority"], d["category"], d["tags"], db.iso()))
        tdoc = doc_row(tid_)
    if link_to:
        db.x("UPDATE documents SET linked_to=? WHERE id=?", (link_to, tdoc["id"]))
    set_doc_text(tdoc, md, "ready", f"{info.get('language', '?')} · {int(info.get('duration', 0) // 60)} min")
    db.x("UPDATE documents SET status='transcribed', duration=?, language=? WHERE id=?",
         (info.get("duration", 0), info.get("language", ""), did))
    return {"transcript_id": tdoc["id"]}


@app.post("/api/transcribe")
@api
def start_transcribe():
    b = body()
    if not transcribe.available():
        raise ValueError("Transcription needs faster-whisper. Run:  pip install faster-whisper  and restart.")
    s = config.get()["whisper"]
    opts = {**s, **{k: b[k] for k in ("model", "language") if b.get(k)}}
    out = []
    for item in b.get("items", []):
        did = int(item["doc_id"])
        d = doc_row(did)
        j = jobs.submit("transcribe", f"Transcribe {d['filename']}",
                        lambda job, did=did, link=item.get("link_to"): transcribe_doc(did, opts, link, job),
                        topic_id=d["topic_id"], doc_id=did)
        out.append(j.out())
    return jsonify(jobs=out)


# ====================================================================== jobs
@app.get("/api/jobs")
@api
def get_jobs():
    return jsonify(jobs.listing())


@app.post("/api/jobs/<jid>/cancel")
@api
def cancel_job(jid):
    return jsonify(ok=jobs.cancel(jid))


# ====================================================================== summaries
@app.post("/api/summarize")
@api
def summarize():
    b = body()
    ids = [int(i) for i in b.get("doc_ids", [])]
    if not ids:
        raise ValueError("Select at least one document")
    tid = doc_row(ids[0])["topic_id"]
    combine = bool(b.get("combine"))
    j = jobs.submit("summary", "Summarise" + (" (combined)" if combine else f" {len(ids)} document(s)"),
                    lambda job: tasks.summarize(tid, ids, b.get("language"), combine, job, b.get("focus", ""), b.get("level", "standard")),
                    topic_id=tid)
    return jsonify(job=j.out())


@app.get("/api/topics/<int:tid>/summaries")
@api
def list_summaries(tid):
    rows = db.q("SELECT * FROM summaries WHERE topic_id=? ORDER BY id DESC", (tid,))
    for r in rows:
        r["doc_ids"] = json.loads(r["doc_ids"])
    return jsonify(rows)


@app.delete("/api/summaries/<int:sid>")
@api
def del_summary(sid):
    db.x("DELETE FROM summaries WHERE id=?", (sid,))
    return jsonify(ok=True)


@app.put("/api/summaries/<int:sid>")
@api
def put_summary(sid):
    db.x("UPDATE summaries SET content=? WHERE id=?", (body().get("content", ""), sid))
    return jsonify(ok=True)


# ====================================================================== exercises
@app.post("/api/generate")
@api
def generate():
    b = body()
    tid = int(b["topic_id"])
    ids = [int(i) for i in b.get("doc_ids", [])] or tasks.included_docs(tid)
    types = [t for t in b.get("types", ["mcq"]) if t in tasks.TYPES]
    if not ids:
        raise ValueError("No documents with text. Upload or transcribe something first.")
    if not types:
        raise ValueError("Pick at least one exercise type")
    n = max(1, min(30, int(b.get("n", 5))))
    j = jobs.submit("generate", f"Create {n * len(types)} exercises",
                    lambda job: tasks.generate_items(tid, ids, types, n, b.get("difficulty", "mixed"),
                                                     b.get("language"), job), topic_id=tid)
    return jsonify(job=j.out())


@app.get("/api/topics/<int:tid>/items")
@api
def list_items(tid):
    sql, args = "SELECT * FROM items WHERE topic_id=?", [tid]
    if request.args.get("type"):
        sql += " AND type=?"
        args.append(request.args["type"])
    if request.args.get("doc_id"):
        sql += " AND doc_id=?"
        args.append(int(request.args["doc_id"]))
    rows = [srs.hydrate(r) for r in db.q(sql + " ORDER BY id DESC", tuple(args))]
    if request.args.get("weak") == "1":
        rows = [r for r in rows if r["weak"]]
    return jsonify(rows)


@app.delete("/api/items/<int:iid>")
@api
def del_item(iid):
    db.x("DELETE FROM items WHERE id=?", (iid,))
    db.x("DELETE FROM reviews WHERE item_id=?", (iid,))
    return jsonify(ok=True)


@app.post("/api/items/<int:iid>/flag")
@api
def flag_item(iid):
    db.x("UPDATE items SET flag=? WHERE id=?", (1 if body().get("weak") else 0, iid))
    return jsonify(ok=True)


@app.post("/api/items/<int:iid>/reset")
@api
def reset_item(iid):
    db.x("UPDATE items SET state='new',ease=2.5,ivl=0,reps=0,lapses=0,streak=0,last_grade=NULL,flag=0 WHERE id=?", (iid,))
    return jsonify(ok=True)


@app.get("/api/queue")
@api
def queue():
    a = request.args
    tids = [int(t) for t in a.get("topics", "").split(",") if t]
    types = [t for t in a.get("types", "").split(",") if t]
    p = config.get()["practice"]
    items = srs.build_queue(tids, a.get("mode", "due"), int(a.get("limit", p["session_size"])), types or None,
                            int(a.get("new", p["new_per_session"])), a.get("interleave", "1") == "1")
    return jsonify(items)


@app.get("/api/queue/counts")
@api
def queue_counts():
    tids = [int(t) for t in request.args.get("topics", "").split(",") if t]
    if not tids:
        return jsonify(due=0, new=0, weak=0, total=0)
    ph = ",".join("?" * len(tids))
    items = [srs.hydrate(i) for i in db.q(f"SELECT * FROM items WHERE suspended=0 AND topic_id IN ({ph})", tuple(tids))]
    now = db.iso()
    return jsonify(due=sum(1 for i in items if i["state"] != "new" and (i["due"] or "") <= now),
                   new=sum(1 for i in items if i["state"] == "new"),
                   weak=sum(1 for i in items if i["weak"]), total=len(items))


@app.post("/api/items/<int:iid>/review")
@api
def review(iid):
    b = body()
    s = srs.apply(iid, int(b["grade"]), float(b.get("seconds", 0)), b.get("mode", "review"))
    it = srs.hydrate(db.one("SELECT * FROM items WHERE id=?", (iid,)))
    return jsonify(next_due=s["due"], interval=srs.fmt_ivl(s["ivl"]) if s["ivl"] else "10 min", weak=it["weak"])


@app.post("/api/items/<int:iid>/evaluate")
@api
def evaluate_item(iid):
    it = srs.hydrate(db.one("SELECT * FROM items WHERE id=?", (iid,)))
    ans = (body().get("answer") or "").strip()
    if not ans:
        raise ValueError("Write an answer first")
    return jsonify(tasks.evaluate(it, ans, body().get("language")))


# ---------------------------------------------------------------- exams
EXAMS = {}


def strip_item(it):
    d = it["data"]
    out = {"id": it["id"], "type": it["type"], "question": it["question"], "topic_id": it["topic_id"]}
    if it["type"] == "mcq":
        out["options"] = d["options"]
    if it["type"] == "case":
        out["tasks"] = d.get("tasks", [])
    return out


@app.post("/api/exam/start")
@api
def exam_start():
    b = body()
    tids = [int(t) for t in b.get("topic_ids", [])]
    n = max(1, min(60, int(b.get("n", 10))))
    items = srs.build_queue(tids, "all", n, b.get("types") or None, interleave=True)
    if not items:
        raise ValueError("No exercises yet. Create some in 'Question bank' first.")
    minutes = max(1, int(b.get("minutes", len(items) * 2)))
    eid = db.x("INSERT INTO exams(topic_ids,ts,minutes,expected_pct,item_ids) VALUES(?,?,?,?,?)",
               (json.dumps(tids), db.iso(), minutes, b.get("expected_pct"), json.dumps([i["id"] for i in items])))
    EXAMS[eid] = {"items": {i["id"]: i for i in items}}
    return jsonify(exam_id=eid, minutes=minutes, items=[strip_item(i) for i in items])


@app.post("/api/exam/<int:eid>/submit")
@api
def exam_submit(eid):
    ex = db.one("SELECT * FROM exams WHERE id=?", (eid,))
    if not ex:
        raise ValueError("Unknown exam")
    answers = {int(k): v for k, v in (body().get("answers") or {}).items()}
    out = []
    for iid in json.loads(ex["item_ids"]):
        it = srs.hydrate(db.one("SELECT * FROM items WHERE id=?", (iid,)))
        a = answers.get(iid)
        row = {"id": iid, "type": it["type"], "question": it["question"], "data": it["data"], "answer": a,
               "source": it["source"], "correct": None}
        if it["type"] == "mcq":
            row["correct"] = a is not None and int(a) == it["data"]["answer_index"]
        out.append(row)
    return jsonify(items=out)


@app.post("/api/exam/<int:eid>/finish")
@api
def exam_finish(eid):
    ex = db.one("SELECT * FROM exams WHERE id=?", (eid,))
    if not ex:
        raise ValueError("Unknown exam")
    grades = {int(k): float(v) for k, v in (body().get("grades") or {}).items()}   # 0, 0.5 or 1
    if not grades:
        raise ValueError("No grades")
    for iid, g in grades.items():
        srs.apply(iid, 2 if g >= 1 else 1 if g >= 0.5 else 0, 0, "exam")
    score = round(100 * sum(grades.values()) / len(grades), 1)
    db.x("UPDATE exams SET score_pct=?, finished=1 WHERE id=?", (score, eid))
    exp = ex["expected_pct"]
    return jsonify(score_pct=score, expected_pct=exp, calibration=None if exp is None else round(score - exp, 1))


# ---------------------------------------------------------------- jury (oral exam simulation)
JURY = {}


def _jury_transcript(s):
    return "\n".join(f"{'EXAMINER' if m['who'] == 'e' else 'CANDIDATE'}: {m['text']}" for m in s["log"])


@app.post("/api/jury/start")
@api
def jury_start():
    b = body()
    tids = [int(t) for t in b.get("topic_ids", [])]
    n = max(1, min(10, int(b.get("n", 3))))
    items = srs.build_queue(tids, "all", n, ["jury", "open", "case", "audit"], interleave=True)
    if not items:
        raise ValueError("No oral-exam style questions yet. Create 'jury', 'open', 'case' or 'audit' exercises first.")
    sid = uuid.uuid4().hex[:8]
    JURY[sid] = {"items": items, "i": 0, "follow": 0, "log": [], "lang": b.get("language"), "max_follow": 2}
    first = items[0]["question"] + (("\n" + "\n".join("- " + t for t in items[0]["data"].get("tasks", []))) if items[0]["data"].get("tasks") else "")
    JURY[sid]["log"].append({"who": "e", "text": first})
    return jsonify(session=sid, total=len(items), index=1, say=first)


@app.post("/api/jury/turn")
@api
def jury_turn():
    b = body()
    s = JURY.get(b.get("session"))
    if not s:
        raise ValueError("Session expired")
    ans = (b.get("answer") or "").strip()
    if not ans:
        raise ValueError("Answer something")
    lang = tasks.lang_of(s["lang"])
    item = s["items"][s["i"]]
    transcript = _jury_transcript(s)
    s["log"].append({"who": "c", "text": ans})
    force_next = s["follow"] >= s["max_follow"]
    r = llm.complete_json(prompts.p_jury_turn(item, transcript, ans, s["follow"], s["max_follow"], lang),
                          temperature=0.3, max_tokens=500)
    action = "next" if force_next else (r.get("action") if isinstance(r, dict) else "next")
    say = (r.get("say") if isinstance(r, dict) else "") or ""
    if action == "follow_up":
        s["follow"] += 1
        s["log"].append({"who": "e", "text": say})
        return jsonify(done=False, say=say, index=s["i"] + 1, total=len(s["items"]))
    s["i"] += 1
    s["follow"] = 0
    if s["i"] >= len(s["items"]):
        fin = llm.complete_json(prompts.p_jury_final(_jury_transcript(s), lang), temperature=0.2, max_tokens=900)
        return jsonify(done=True, say=say, final=fin)
    nxt = s["items"][s["i"]]
    q = nxt["question"] + (("\n" + "\n".join("- " + t for t in nxt["data"].get("tasks", []))) if nxt["data"].get("tasks") else "")
    s["log"].append({"who": "e", "text": q})
    return jsonify(done=False, say=(say + "\n\n" if say else "") + q, index=s["i"] + 1, total=len(s["items"]),
                   reference=item["data"].get("model_answer", ""))


# ---------------------------------------------------------------- recall / teach / mnemonics
def scope_context(b):
    ids = [int(i) for i in b.get("doc_ids", [])] or tasks.included_docs(int(b["topic_id"]))
    ctx, picked = rag.sample_context(ids, tasks.budget())
    if not picked:
        raise ValueError("No text in the selected documents.")
    return ctx


@app.post("/api/recall/points")
@api
def recall_points():
    b = body()
    r = llm.complete_json(prompts.p_points(scope_context(b), tasks.lang_of(b.get("language")),
                                           tasks.topic_goal(int(b["topic_id"]))), temperature=0.2, max_tokens=900)
    pts = r.get("key_points") if isinstance(r, dict) else r
    return jsonify(points=[str(p) for p in (pts or [])][:14])


@app.post("/api/recall/compare")
@api
def recall_compare():
    b = body()
    if not (b.get("text") or "").strip():
        raise ValueError("Write what you remember first")
    return jsonify(llm.complete_json(prompts.p_recall_compare(b["points"], b["text"], tasks.lang_of(b.get("language"))),
                                     temperature=0.1, max_tokens=900))


@app.post("/api/teach/feedback")
@api
def teach_feedback():
    b = body()
    if not (b.get("text") or "").strip():
        raise ValueError("Explain something first")
    return jsonify(llm.complete_json(
        prompts.p_teach(scope_context(b), b.get("audience", "a colleague"), b["text"], tasks.lang_of(b.get("language"))),
        temperature=0.2, max_tokens=1200))


@app.post("/api/teach/transcribe")
@api
def teach_transcribe():
    if not transcribe.available():
        raise ValueError("Recording needs faster-whisper (pip install faster-whisper). You can type your explanation instead.")
    f = request.files["audio"]
    tmp = config.tmp_dir()
    p = tmp / f"teach_{uuid.uuid4().hex}.webm"
    f.save(p)
    opts = {**config.get()["whisper"], "language": request.form.get("language") or config.get()["whisper"]["language"]}

    def work(job):
        try:
            md, _ = transcribe.run(p, opts, job.set, job.check)
        finally:
            p.unlink(missing_ok=True)
        text = re.sub(r"^#.*\n|^\*Language.*\n|\*\*\[\d\d:\d\d:\d\d\]\*\* ", "", md, flags=re.M).strip()
        return {"text": text}
    j = jobs.submit("teach", "Transcribe recording", work)
    return jsonify(job=j.out())


@app.post("/api/mnemonic")
@api
def mnemonic():
    b = body()
    items = [str(i).strip() for i in b.get("items", []) if str(i).strip()]
    if len(items) < 2:
        raise ValueError("Enter at least two list items")
    letters = "".join(re.sub(r"^\W+", "", i)[:1].upper() for i in items)
    r = llm.complete_json(prompts.p_mnemonic(items, tasks.lang_of(b.get("language")), b.get("style", "vivid and a little funny")),
                          temperature=0.8, max_tokens=700)
    r = r if isinstance(r, dict) else {}
    r["letters"] = letters
    return jsonify(r)


# ====================================================================== chat
@app.post("/api/chat")
@api
def chat():
    b = body()
    tid = int(b["topic_id"])
    msg = (b.get("message") or "").strip()
    if not msg:
        raise ValueError("Empty message")
    gen = tasks.chat_stream(tid, [int(i) for i in b.get("doc_ids", [])], msg, b.get("history", []),
                            bool(b.get("allow_general")))

    def lines():
        try:
            for ev in gen:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except llm.LLMError as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
        except Exception as e:  # noqa: BLE001
            yield json.dumps({"type": "error", "message": f"{e.__class__.__name__}: {e}"}) + "\n"
    return Response(lines(), mimetype="application/x-ndjson")


@app.get("/api/topics/<int:tid>/chat")
@api
def chat_history(tid):
    rows = db.q("SELECT role,content,sources FROM chat WHERE topic_id=? ORDER BY id DESC LIMIT 40", (tid,))
    for r in rows:
        r["sources"] = json.loads(r["sources"] or "[]")
    return jsonify(rows[::-1])


@app.delete("/api/topics/<int:tid>/chat")
@api
def chat_clear(tid):
    db.x("DELETE FROM chat WHERE topic_id=?", (tid,))
    return jsonify(ok=True)


# ====================================================================== progress / focus
@app.post("/api/focus")
@api
def log_focus():
    b = body()
    db.x("INSERT INTO focus_log(ts,topic_id,minutes,kind) VALUES(?,?,?,?)",
         (db.iso(), b.get("topic_id"), float(b.get("minutes", 0)), b.get("kind", "focus")))
    return jsonify(ok=True)


@app.get("/api/stats")
@api
def stats():
    tz = f"{int(request.args.get('tz', 0)):+d} minutes"
    topics = db.q("SELECT * FROM topics ORDER BY ord, id")
    now = db.iso()
    out = []
    for t in topics:
        items = [srs.hydrate(i) for i in db.q("SELECT * FROM items WHERE topic_id=? AND suspended=0", (t["id"],))]
        docs = db.q("SELECT status, text_len FROM documents WHERE topic_id=?", (t["id"],))
        r7 = db.q("""SELECT grade FROM reviews WHERE topic_id=? AND ts >= datetime('now','-7 days')""", (t["id"],))
        sums = db.one("SELECT COUNT(*) n FROM summaries WHERE topic_id=?", (t["id"],))["n"]
        out.append({"id": t["id"], "name": t["name"], "docs": len(docs),
                    "ready": sum(1 for d in docs if d["text_len"] > 0),
                    "audio_pending": sum(1 for d in docs if d["status"] == "pending_transcription"),
                    "summaries": sums, "items": len(items),
                    "due": sum(1 for i in items if i["state"] != "new" and (i["due"] or "") <= now),
                    "new": sum(1 for i in items if i["state"] == "new"),
                    "weak": sum(1 for i in items if i["weak"]),
                    "mature": sum(1 for i in items if i["ivl"] >= 21),
                    "acc7": round(100 * sum(1 for r in r7 if r["grade"] >= 2) / len(r7)) if r7 else None})
    days = db.q("""SELECT date(ts, ?) d, COUNT(*) n, SUM(CASE WHEN grade>=2 THEN 1 ELSE 0 END) ok
                   FROM reviews WHERE ts >= datetime('now','-14 days') GROUP BY d ORDER BY d""", (tz,))
    focus = db.q("""SELECT date(ts, ?) d, SUM(minutes) m FROM focus_log WHERE kind='focus'
                    AND ts >= datetime('now','-14 days') GROUP BY d ORDER BY d""", (tz,))
    active = {r["d"] for r in db.q("SELECT DISTINCT date(ts, ?) d FROM reviews UNION SELECT DISTINCT date(ts, ?) d FROM focus_log", (tz, tz))}
    today = db.one("SELECT date('now', ?) d", (tz,))["d"]
    streak, cur = 0, today
    from datetime import date, timedelta
    day = date.fromisoformat(today)
    if today not in active:
        day -= timedelta(days=1)
    while day.isoformat() in active:
        streak += 1
        day -= timedelta(days=1)
    return jsonify(topics=out, reviews=days, focus=focus, streak=streak, today=today)


# ====================================================================== recommend
@app.post("/api/recommend/queries")
@api
def recommend_queries():
    b = body()
    langs = [l for l in b.get("langs", ["en", "de"]) if l in ("de", "en", "fr")]
    return jsonify(queries=tasks.suggest_queries(int(b["topic_id"]), b.get("scope", "topic"), [int(i) for i in b.get("doc_ids", [])], langs))


@app.post("/api/recommend")
@api
def recommend():
    b = body()
    tid = int(b["topic_id"])
    langs = [l for l in b.get("langs", ["en", "de"]) if l in ("de", "en", "fr")]
    kinds = [k for k in b.get("kinds", ["article", "review", "book"]) if k in ("article", "review", "book")]
    if not langs or not kinds:
        raise ValueError("Choose at least one language and one type")
    ids = [int(i) for i in b.get("doc_ids", [])]
    j = jobs.submit("recommend", "Find reading", lambda job: tasks.recommend(tid, b.get("scope", "topic"), ids, langs, kinds, b.get("queries"), job),
                    topic_id=tid)
    return jsonify(job=j.out())


@app.get("/api/topics/<int:tid>/reading")
@api
def list_reading(tid):
    from core import literature
    rows = db.q("SELECT * FROM reading WHERE topic_id=? AND status!='dismissed' ORDER BY (status='saved') DESC, id DESC", (tid,))
    t = db.one("SELECT name FROM topics WHERE id=?", (tid,))
    return jsonify(items=rows, links=literature.link_outs(t["name"]) if t else [])


@app.patch("/api/reading/<int:rid>")
@api
def edit_reading(rid):
    st = body().get("status")
    if st in ("suggested", "saved", "read", "dismissed"):
        db.x("UPDATE reading SET status=? WHERE id=?", (st, rid))
    return jsonify(ok=True)


# ====================================================================== present
@app.post("/api/decks/generate")
@api
def deck_generate():
    b = body()
    tid = int(b["topic_id"])
    docs = [int(i) for i in b.get("doc_ids", [])]
    if b.get("use_all"):
        docs = sorted(set(docs) | set(tasks.included_docs(tid)))
    sums = [int(i) for i in b.get("summary_ids", [])]
    if not docs and not sums:
        raise ValueError("Choose at least one document or summary")
    j = jobs.submit("deck", "Create presentation",
                    lambda job: tasks.make_deck(tid, docs, sums, b.get("audience", "colleagues"), int(b.get("minutes", 20)),
                                                b.get("language"), b.get("extra", ""), job), topic_id=tid)
    return jsonify(job=j.out())


@app.get("/api/topics/<int:tid>/decks")
@api
def list_decks(tid):
    return jsonify(db.q("SELECT id,title,language,created,updated FROM decks WHERE topic_id=? ORDER BY id DESC", (tid,)))


@app.get("/api/decks/<int:did>")
@api
def get_deck(did):
    d = db.one("SELECT * FROM decks WHERE id=?", (did,))
    if not d:
        raise ValueError("Unknown presentation")
    d["data"] = json.loads(d["data"])
    return jsonify(d)


@app.put("/api/decks/<int:did>")
@api
def put_deck(did):
    data = body().get("data") or {}
    db.x("UPDATE decks SET data=?, title=?, updated=? WHERE id=?", (json.dumps(data, ensure_ascii=False), data.get("title", ""), db.iso(), did))
    return jsonify(ok=True)


@app.delete("/api/decks/<int:did>")
@api
def del_deck(did):
    db.x("DELETE FROM decks WHERE id=?", (did,))
    return jsonify(ok=True)


@app.get("/api/decks/<int:did>/export")
@api
def export_deck(did):
    import io
    d = db.one("SELECT * FROM decks WHERE id=?", (did,))
    if not d:
        raise ValueError("Unknown presentation")
    data = json.loads(d["data"])
    name = re.sub(r"[^\w\- ]+", "", data.get("title") or "presentation").strip().replace(" ", "_")[:60] or "presentation"
    if request.args.get("format") == "md":
        return Response(deckmod.to_markdown(data), mimetype="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    return send_file(io.BytesIO(deckmod.build_pptx(data)), as_attachment=True, download_name=f"{name}.pptx",
                     mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation")


# ====================================================================== main
# ====================================================================== data location (shared drive)
def storage_info():
    other = config.lock_other()
    return {"path": str(config.HOME), "in_cloud": config.in_cloud(), "default_path": str(config.APP_DIR / "data"),
            "is_default": config.HOME == (config.APP_DIR / "data"), "candidates": config.candidates(),
            "machine": config.MACHINE, "os": platform.system(), "has_data_here": config.has_content(config.HOME),
            "other_machine": {"name": other.get("machine"), "os": other.get("os")} if other else None,
            "notice": config.NOTICE["fallback"], "adopted": config.NOTICE["adopted"],
            "backups": len(list((config.HOME / "backups").glob("*.db"))) if (config.HOME / "backups").exists() else 0}


@app.get("/api/storage")
@api
def get_storage():
    return jsonify(storage_info())


@app.post("/api/storage")
@api
def set_storage():
    b = body()
    if any(j["status"] in ("queued", "running") for j in jobs.listing()):
        raise ValueError("Wait until the running tasks in the sidebar have finished, then try again.")
    mode = b.get("mode", "use")
    if mode == "default":
        config.use_default()
    else:
        config.switch(b.get("path", ""), move=(mode == "move"))
    config.backup_db()
    config.NOTICE["adopted"] = ""
    return jsonify(storage_info())


@app.after_request
def _tag(resp):
    resp.headers["X-App"] = "StudyBench"
    return resp


def _free_port(start):
    import socket
    for p in range(start, start + 30):
        with socket.socket() as sk:
            if sk.connect_ex(("127.0.0.1", p)) != 0:           # nothing listens here
                return p
    return start


def _studybench_on(port):
    import requests
    try:
        return requests.get(f"http://127.0.0.1:{port}/api/setup", timeout=1).headers.get("X-App") == "StudyBench"
    except requests.RequestException:
        return False


def migrate_paths():
    """Older versions stored absolute file paths. Make them relative to the data folder so they survive a move."""
    for d in db.q("SELECT id, stored_path FROM documents WHERE stored_path<>''"):
        r = config.resolve(d["stored_path"])
        if r and config.rel_path(r) != d["stored_path"]:
            db.x("UPDATE documents SET stored_path=? WHERE id=?", (config.rel_path(r), d["id"]))


def _heartbeat_loop():
    while True:
        time.sleep(30)
        config.heartbeat()


def main():
    want = int(os.environ.get("STUDYBENCH_PORT", "8731"))        # own port, different from other local tools
    if _studybench_on(want):                                      # already running: just open it
        webbrowser.open(f"http://127.0.0.1:{want}")
        return
    config.auto_adopt()                                           # new computer + data already in a cloud drive: connect to it
    config.backup_db()
    config.move_keys_out_of_shared_settings()
    migrate_paths()
    config.heartbeat()
    atexit.register(config.release_lock)
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    port = _free_port(want)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  StudyBench running at {url}\n  Data folder: {config.HOME}\n  Press Ctrl+C to stop.\n")
    if os.environ.get("STUDYBENCH_NO_SETUP") != "1":
        setup.startup()                                   # start Ollama, download AI + speech models in the background
    if os.environ.get("STUDYBENCH_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
