"""Long documents: summary grows with the document, is cached/resumable, makes few AI calls; slides are filled; audio decoding."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["STUDYBENCH_HOME"] = tempfile.mkdtemp()
os.environ["STUDYBENCH_LOCAL"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db, jobs, llm, tasks, transcribe  # noqa: E402
import app as appmod  # noqa: E402

c = appmod.app.test_client()
J = lambda r: (r.status_code == 200 or print(r.data[:300])) and r.get_json()  # noqa: E731
config.update({"provider": "demo"})
calls = []
orig = llm.complete


def counting(messages, **kw):
    calls.append(kw.get("max_tokens"))
    return orig(messages, **kw)


llm.complete = counting
tasks.llm.complete = counting


def main():
    tid = J(c.post("/api/topics", json={"name": "Big", "keywords": "ADHS Erwachsene"}))["id"]
    assert J(c.get("/api/topics"))[0]["keywords"] == "ADHS Erwachsene"
    pages = [f"## Page {i}\n" + (f"Kapitel {i // 10}: Thema {i}. " + "Die Diagnostik umfasst Anamnese, Fremdanamnese und Testung. " * 28) for i in range(1, 217)]
    f = Path(tempfile.mkdtemp()) / "buch.txt"
    f.write_text("\n\n".join(pages), encoding="utf-8")
    J(c.post(f"/api/topics/{tid}/upload", data={"files": [(open(f, "rb"), "buch.txt")]}, content_type="multipart/form-data"))
    doc = J(c.get(f"/api/topics/{tid}/docs"))[0]
    n_parts = len(tasks.rag.pack(tasks.rag.load_chunks([doc["id"]]), tasks.part_budget()))
    print("216 pages =", doc["text_len"], "chars ->", n_parts, "sections")
    assert 20 <= n_parts <= 40

    job = jobs.wait(jobs.JOBS[J(c.post("/api/summarize", json={"doc_ids": [doc["id"]], "language": "de", "level": "quick"}))["job"]["id"]], 120)
    assert job.status == "done", job.error
    assert len(calls) == n_parts + 1, (len(calls), n_parts)               # one call per section + one overview, nothing more
    assert max(calls) <= 2200
    summ = J(c.get(f"/api/topics/{tid}/summaries"))[0]["content"]
    assert "## Abschnitt für Abschnitt" in summ and summ.count("###") == n_parts, summ.count("###")
    assert "(pp. 1-" in summ
    print("summary length:", len(summ), "chars,", len(calls), "AI calls")

    calls.clear()                                                           # cache: running again costs only the overview
    job = jobs.wait(jobs.JOBS[J(c.post("/api/summarize", json={"doc_ids": [doc["id"]], "language": "de", "level": "quick"}))["job"]["id"]], 120)
    assert job.status == "done" and len(calls) == 1, len(calls)

    # presentation from the long document: outline + one call per slide, filled slides
    calls.clear()
    sid = J(c.get(f"/api/topics/{tid}/summaries"))[0]["id"]
    jd = jobs.wait(jobs.JOBS[J(c.post("/api/decks/generate", json={"topic_id": tid, "summary_ids": [sid], "doc_ids": [doc["id"]], "minutes": 20}))["job"]["id"]], 120)
    assert jd.status == "done", jd.error
    deck = J(c.get(f"/api/decks/{jd.result['deck_id']}"))["data"]
    assert len(calls) == 1 + len(deck["slides"]) and all(s["bullets"] and len(s["notes"]) > 40 for s in deck["slides"])

    # GPU libraries missing (cublas64_12.dll): falls back to CPU instead of failing
    import types
    seen = []

    class Seg:
        def __init__(self, a, b, t):
            self.start, self.end, self.text = a, b, t

    class Model:
        def __init__(self, dev):
            self.dev = dev

        def transcribe(self, audio, **kw):
            def gen():
                if self.dev != "cpu":
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                yield Seg(0, 1, "ok")
            return gen(), types.SimpleNamespace(duration=1.0, language="de")
    real_model, real_dec = transcribe._model, transcribe.decode_audio
    transcribe._model = lambda name, dev, comp: (seen.append((dev, comp)), Model(dev))[1]
    transcribe.decode_audio = lambda p: [0.0]
    md, info = transcribe.run("x.m4a", {"model": "small", "device": "auto"}, lambda *a: None, lambda: None)
    assert "ok" in md and seen == [("auto", "auto"), ("cpu", "int8")], seen
    seen.clear()
    transcribe.run("x.m4a", {"model": "small", "device": "auto"}, lambda *a: None, lambda: None)
    assert seen == [("cpu", "int8")], seen                                  # remembers: no second failed attempt
    transcribe._model, transcribe.decode_audio = real_model, real_dec

    # audio decoding (own decoder, independent of the PyAV version)
    if shutil.which("ffmpeg"):
        m4a = Path(tempfile.mkdtemp()) / "x.m4a"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac", str(m4a)], check=True)
        a = transcribe.decode_audio(m4a)
        assert a.dtype.name == "float32" and 2.8 < len(a) / 16000 < 3.3
    print("ALL BIG TESTS PASSED")


if __name__ == "__main__":
    main()
