"""End-to-end API test using the offline demo provider and a fake transcription engine."""
import io
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

HOME = tempfile.mkdtemp()
os.environ["STUDYBENCH_HOME"] = HOME
os.environ["STUDYBENCH_LOCAL"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, jobs, transcribe  # noqa: E402
import app as appmod  # noqa: E402

c = appmod.app.test_client()
config.update({"provider": "demo"})


def J(r):
    assert r.status_code == 200, (r.status_code, r.data[:300])
    return r.get_json()


def wait_job(j, t=30):
    jid = j["id"]
    t0 = time.time()
    while time.time() - t0 < t:
        for x in J(c.get("/api/jobs")):
            if x["id"] == jid and x["status"] in ("done", "error", "cancelled"):
                return x
        time.sleep(0.1)
    raise TimeoutError


def make_files(d):
    from pptx import Presentation
    import docx
    p = Presentation()
    for i, (t, b) in enumerate([("Alcohol withdrawal", "CIWA-Ar scores guide benzodiazepine dosing.\nThiamine before glucose."),
                                ("Relapse prevention", "Naltrexone and acamprosate support abstinence.")], 1):
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = t
        s.placeholders[1].text = b
        s.notes_slide.notes_text_frame.text = "Mention the Wernicke risk."
    p.save(d / "sucht.pptx")
    dd = docx.Document()
    dd.add_heading("PIA Regelwerk", 1)
    dd.add_paragraph("Die Dokumentation muss innerhalb von 24 Stunden erfolgen.")
    t = dd.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Kriterium", "Frist"
    t.cell(1, 0).text, t.cell(1, 1).text = "Erstkontakt", "7 Tage"
    dd.save(d / "pia.docx")
    (d / "notes.txt").write_text("ADHS Diagnostik: Eigenanamnese, Fremdanamnese, Selbstbeurteilung.\n", encoding="utf-8")
    with wave.open(str(d / "rec.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    (d / "old.ppt").write_bytes(b"x")


def fake_engine(path, opts, progress, check):
    progress(0.5, "half")
    return [(0, 5, "Heute sprechen wir über Entzugssyndrome."), (6, 20, "Thiamin zuerst geben."),
            (80, 90, "Rückfallprophylaxe mit Naltrexon.")], {"duration": 95, "language": "de"}


def main():
    d = Path(tempfile.mkdtemp())
    make_files(d)
    topic = J(c.post("/api/topics", json={"name": "Sucht", "goal": "Zusatzbezeichnung"}))
    tid = topic["id"]
    assert c.post("/api/topics", json={"name": "Sucht"}).status_code == 400

    # inventory: upload + folder import + dedupe
    up = J(c.post(f"/api/topics/{tid}/upload", data={"files": [(open(d / "sucht.pptx", "rb"), "sucht.pptx")]},
                  content_type="multipart/form-data"))
    assert up[0]["duplicate"] is False
    up2 = J(c.post(f"/api/topics/{tid}/upload", data={"files": [(open(d / "sucht.pptx", "rb"), "again.pptx")]},
                   content_type="multipart/form-data"))
    assert up2[0]["duplicate"] is True
    j = J(c.post(f"/api/topics/{tid}/import-folder", json={"path": str(d), "copy": True}))["job"]
    res = wait_job(j)
    assert res["status"] == "done", res
    assert res["result"]["added"] == 3 and res["result"]["duplicates"] == 1 and res["result"]["skipped"] == 1, res["result"]
    docs = {x["filename"]: x for x in J(c.get(f"/api/topics/{tid}/docs"))}
    assert docs["sucht.pptx"]["status"] == "ready" and docs["sucht.pptx"]["text_len"] > 50
    assert docs["pia.docx"]["status"] == "ready"
    assert docs["rec.wav"]["status"] == "pending_transcription" and docs["rec.wav"]["kind"] == "audio"
    assert "old.ppt" not in docs          # extension not allowed in settings -> ignored
    txt = J(c.get(f"/api/docs/{docs['sucht.pptx']['id']}/text"))["text"]
    assert "## Slide 1: Alcohol withdrawal" in txt and "Wernicke" in txt, txt
    dtxt = J(c.get(f"/api/docs/{docs['pia.docx']['id']}/text"))["text"]
    assert "Erstkontakt | 7 Tage" in dtxt, dtxt

    # edit metadata, reorder
    pid = docs["pia.docx"]["id"]
    J(c.patch(f"/api/docs/{pid}", json={"priority": 1, "tags": "pia, regeln", "category": "Regelwerk"}))
    J(c.post(f"/api/topics/{tid}/reorder", json={"ids": [pid, docs["sucht.pptx"]["id"]]}))
    after = J(c.get(f"/api/topics/{tid}/docs"))
    assert after[0]["id"] == pid and after[0]["tags"] == ["pia", "regeln"] and after[0]["priority"] == 1

    # autotag does not overwrite manual priority/tags
    wait_job(J(c.post(f"/api/topics/{tid}/autotag", json={}))["job"])
    again = {x["id"]: x for x in J(c.get(f"/api/topics/{tid}/docs"))}
    assert again[pid]["priority"] == 1 and again[pid]["tags"] == ["pia", "regeln"]
    assert again[docs["sucht.pptx"]["id"]]["category"] == "Demo"

    # transcription (fake engine) -> transcript doc linked to slides
    transcribe.ENGINE = fake_engine
    rec = docs["rec.wav"]["id"]
    tj = J(c.post("/api/transcribe", json={"items": [{"doc_id": rec, "link_to": docs["sucht.pptx"]["id"]}]}))["jobs"][0]
    r = wait_job(tj)
    assert r["status"] == "done", r
    all_docs = J(c.get(f"/api/topics/{tid}/docs"))
    tr = [x for x in all_docs if x["kind"] == "transcript"][0]
    assert tr["parent_id"] == rec and tr["linked_to"] == docs["sucht.pptx"]["id"] and tr["status"] == "ready"
    ttext = J(c.get(f"/api/docs/{tr['id']}/text"))["text"]
    assert "**[00:01:20]**" in ttext, ttext
    J(c.put(f"/api/docs/{tr['id']}/text", json={"text": ttext.replace("Naltrexon", "Naltrexon (Nemexin)")}))
    assert [x for x in all_docs if x["id"] == rec][0]["status"] in ("transcribed", "pending_transcription")

    # summaries
    sd = [docs["sucht.pptx"]["id"], pid]
    s1 = wait_job(J(c.post("/api/summarize", json={"doc_ids": sd, "language": "en"}))["job"])
    assert s1["status"] == "done", s1
    s2 = wait_job(J(c.post("/api/summarize", json={"doc_ids": sd, "combine": True}))["job"])
    assert s2["status"] == "done", s2
    sums = J(c.get(f"/api/topics/{tid}/summaries"))
    assert len(sums) >= 3 and "## Key points" in sums[0]["content"]

    # exercises
    g = wait_job(J(c.post("/api/generate", json={"topic_id": tid, "doc_ids": sd, "types": ["mcq", "case", "open", "audit", "jury", "elab"], "n": 3}))["job"])
    assert g["status"] == "done" and g["result"]["created"] == 18, g
    items = J(c.get(f"/api/topics/{tid}/items"))
    assert len(items) == 18
    mcq = [i for i in items if i["type"] == "mcq"][0]
    assert len(mcq["data"]["options"]) == 4 and 0 <= mcq["data"]["answer_index"] < 4

    # spaced repetition behaviour
    q = J(c.get(f"/api/queue?topics={tid}&mode=due&limit=10"))
    assert 0 < len(q) <= 10 and q[0]["preview"]["0"] == "10 min"
    a = q[0]["id"]
    J(c.post(f"/api/items/{a}/review", json={"grade": 2}))
    J(c.post(f"/api/items/{a}/review", json={"grade": 2}))
    ivl2 = db_item(a)["ivl"]
    J(c.post(f"/api/items/{a}/review", json={"grade": 0}))
    it = db_item(a)
    assert ivl2 >= 2 and it["ivl"] == 0 and it["lapses"] == 1 and it["state"] == "learning"
    J(c.post(f"/api/items/{a}/review", json={"grade": 0}))
    assert any(i["id"] == a for i in J(c.get(f"/api/queue?topics={tid}&mode=weak")))
    cnt = J(c.get(f"/api/queue/counts?topics={tid}"))
    assert cnt["total"] == 18 and cnt["weak"] >= 1
    ev = J(c.post(f"/api/items/{q[1]['id']}/evaluate", json={"answer": "some answer"}))
    assert "score" in ev

    # exam
    ex = J(c.post("/api/exam/start", json={"topic_ids": [tid], "n": 5, "minutes": 10, "expected_pct": 80}))
    assert len(ex["items"]) == 5 and all("answer_index" not in json.dumps(i) for i in ex["items"])
    sub = J(c.post(f"/api/exam/{ex['exam_id']}/submit", json={"answers": {}}))
    fin = J(c.post(f"/api/exam/{ex['exam_id']}/finish", json={"grades": {str(i["id"]): 1 for i in sub["items"]}}))
    assert fin["score_pct"] == 100 and fin["calibration"] == 20

    # jury
    js = J(c.post("/api/jury/start", json={"topic_ids": [tid], "n": 2}))
    t1 = J(c.post("/api/jury/turn", json={"session": js["session"], "answer": "my answer"}))
    t2 = J(c.post("/api/jury/turn", json={"session": js["session"], "answer": "again"}))
    assert t2["done"] is True and "score" in t2["final"]

    # recall / teach / mnemonic
    pts = J(c.post("/api/recall/points", json={"topic_id": tid}))["points"]
    assert pts
    J(c.post("/api/recall/compare", json={"points": pts, "text": "I remember x"}))
    J(c.post("/api/teach/feedback", json={"topic_id": tid, "audience": "a trainee", "text": "blah"}))
    m = J(c.post("/api/mnemonic", json={"items": ["Alkohol", "Benzodiazepine", "Cannabis"]}))
    assert m["letters"] == "ABC"

    # chat (streaming) with retrieval
    r = c.post("/api/chat", json={"topic_id": tid, "message": "Welche Frist gilt für den Erstkontakt?"})
    evs = [json.loads(l) for l in r.data.decode().splitlines() if l]
    assert evs[0]["type"] == "sources" and evs[0]["sources"][0]["filename"] == "pia.docx", evs[0]
    assert evs[-1]["type"] == "done" and any(e["type"] == "delta" for e in evs)
    hist = J(c.get(f"/api/topics/{tid}/chat"))
    assert len(hist) == 2

    # stats + focus
    J(c.post("/api/focus", json={"topic_id": tid, "minutes": 25}))
    st = J(c.get("/api/stats?tz=60"))
    assert st["streak"] >= 1 and st["topics"][0]["items"] == 18

    # settings never leak keys
    J(c.post("/api/settings", json={"anthropic": {"api_key": "sk-secret"}}))
    pub = J(c.get("/api/settings"))
    assert "sk-secret" not in json.dumps(pub) and pub["anthropic"]["has_key"] is True
    J(c.post("/api/settings", json={"anthropic": {"api_key": pub["anthropic"]["api_key"]}}))
    assert config.get()["anthropic"]["api_key"] == "sk-secret"

    # delete doc keeps items, removes chunks
    J(c.delete(f"/api/docs/{pid}"))
    assert len(J(c.get(f"/api/topics/{tid}/items"))) == 18
    print("ALL API TESTS PASSED")


def db_item(i):
    from core import db
    return db.one("SELECT * FROM items WHERE id=?", (i,))


if __name__ == "__main__":
    main()
