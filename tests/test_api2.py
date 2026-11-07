"""Tests for: auto-download of a missing Ollama model, file-type filter, encrypted PDFs, recommend, presentations."""
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["STUDYBENCH_HOME"] = tempfile.mkdtemp()
os.environ["STUDYBENCH_LOCAL"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, extract, literature, llm, setup  # noqa: E402
import app as appmod  # noqa: E402

c = appmod.app.test_client()
J = lambda r: (r.status_code == 200 or print(r.data[:300])) and r.get_json()  # noqa: E731


class FakeOllama(BaseHTTPRequestHandler):
    installed = set()
    pulls = 0

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ndjson=False):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        lines = obj if ndjson else [obj]
        for o in lines:
            self.wfile.write((json.dumps(o) + "\n").encode())

    def do_GET(self):
        self._send(200, {"models": [{"name": m} for m in self.installed]})

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/pull":
            FakeOllama.pulls += 1
            time.sleep(0.2)
            self.installed.add(b["name"])
            return self._send(200, [{"status": "pulling", "total": 100, "completed": 40}, {"status": "success"}], True)
        if b["model"] not in self.installed:
            return self._send(404, {"error": f"model '{b['model']}' not found"})
        if b.get("stream"):
            return self._send(200, [{"message": {"content": "hel"}}, {"message": {"content": "lo"}, "done": True}], True)
        self._send(200, {"message": {"content": "hello"}})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    config.update({"provider": "ollama", "ollama": {"host": f"http://127.0.0.1:{srv.server_port}", "model": "fake:1b"}})

    # 1. missing model is downloaded automatically, then the request succeeds (the reported 404 bug)
    assert llm.complete([{"role": "user", "content": "hi"}]) == "hello"
    assert FakeOllama.pulls == 1 and setup.STATE["model:fake:1b"]["status"] == "done"
    assert "".join(llm.stream([{"role": "user", "content": "hi"}])) == "hello"
    config.update({"ollama": {"model": "other:2b"}})
    assert "".join(llm.stream([{"role": "user", "content": "hi"}])) == "hello" and FakeOllama.pulls == 2   # streaming path pulls too
    ok, msg = llm.ping()
    assert ok, msg
    srv.shutdown()
    config.update({"provider": "demo"})

    # 2. file-type filter (upload + folder import)
    tid = J(c.post("/api/topics", json={"name": "T"}))["id"]
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("Hallo Welt")
    (d / "b.exe").write_bytes(b"MZ")
    (d / "c.ppt").write_bytes(b"x")
    r = J(c.post(f"/api/topics/{tid}/upload", data={"files": [(io.BytesIO(b"MZ"), "x.exe"), (io.BytesIO(b"hello text"), "y.txt")]},
                 content_type="multipart/form-data"))
    assert r[0].get("skipped") and not r[1].get("skipped")
    job = J(c.post(f"/api/topics/{tid}/import-folder", json={"path": str(d)}))["job"]
    from core import jobs
    jb = jobs.wait(jobs.JOBS[job["id"]])
    assert jb.result["added"] == 1 and jb.result["skipped"] == 2, jb.result
    config.update({"allowed_extensions": "txt, ppt"})
    assert config.allowed_exts() == {"txt", "ppt"}
    config.update({"allowed_extensions": "pptx docx pdf txt md"})

    # 3. encrypted PDF no longer fails with the 'cryptography' error
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.encrypt("", algorithm="AES-256")
    pdf = d / "enc.pdf"
    with open(pdf, "wb") as f:
        w.write(f)
    res = extract.extract(pdf, "pdf")
    assert "cryptography" not in res.msg, res.msg

    # 4. recommend (database calls mocked with real response shapes)
    last = {"q": ""}

    def fake_get(url, params):
        last["q"] = params.get("term") or params.get("search") or params.get("q") or last["q"]
        if "esearch" in url:
            return {"esearchresult": {"idlist": ["111"]}}
        if "esummary" in url:
            return {"result": {"uids": ["111"], "111": {"title": last["q"].split(" AND ")[0] + " in adults.", "authors": [{"name": "Smith J"}, {"name": "Lee K"}],
                                                           "source": "JAMA", "pubdate": "2023 Jan", "pubtype": ["Review"]}}}
        if "openalex" in url:
            lang = params["filter"].split(",")[0].split(":")[1]
            return {"results": [{"title": "Zufaellige Gartenpflege Tipps", "publication_year": 2020, "doi": "https://doi.org/10.9/off", "authorships": [],
                                 "primary_location": {}, "cited_by_count": 9999, "language": lang, "type": "article"},
                                {"title": f"{params['search']} Arbeit {lang}", "publication_year": 2021, "doi": f"https://doi.org/10.1/{lang}/{abs(hash(params['search']))}",
                                 "authorships": [{"author": {"display_name": "A. Autor"}}], "primary_location": {"source": {"display_name": "Nervenarzt"}},
                                 "cited_by_count": 12, "language": lang, "type": "article"}]}
        return {"docs": [{"key": "/works/OL1W", "title": last["q"] + " Handbuch", "author_name": ["X"], "first_publish_year": 2019}]}
    literature._get = fake_get
    J(c.post("/api/settings", json={"provider": "demo"}))
    ji = J(c.post("/api/recommend", json={"topic_id": tid, "scope": "topic", "langs": ["en", "de", "fr"], "kinds": ["article", "review", "book"]}))["job"]
    jb = jobs.wait(jobs.JOBS[ji["id"]])
    assert jb.status == "done" and jb.result["new"] >= 5, (jb.status, jb.error, jb.result)
    rd = J(c.get(f"/api/topics/{tid}/reading"))
    assert not any("Gartenpflege" in i["title"] for i in rd["items"]), "off-topic results must be filtered"
    assert any(i["source"] == "PubMed" and i["kind"] == "review" for i in rd["items"]) and any(i["lang"] == "fr" for i in rd["items"])
    assert any("awmf" in u for _, u in rd["links"])
    J(c.patch(f"/api/reading/{rd['items'][0]['id']}", json={"status": "saved"}))
    J(c.patch(f"/api/reading/{rd['items'][1]['id']}", json={"status": "dismissed"}))
    assert len(J(c.get(f"/api/topics/{tid}/reading"))["items"]) == len(rd["items"]) - 1
    jb2 = jobs.wait(jobs.JOBS[J(c.post("/api/recommend", json={"topic_id": tid, "scope": "topic", "langs": ["en"], "kinds": ["article"]}))["job"]["id"]])
    assert jb2.result["new"] == 0                                  # no duplicates on repeat

    def boom(url, params):
        raise literature.SourceError("api.openalex.org: ConnectionError")
    literature._get = boom
    jb3 = jobs.wait(jobs.JOBS[J(c.post("/api/recommend", json={"topic_id": tid, "scope": "topic", "langs": ["de"], "kinds": ["article"]}))["job"]["id"]])
    assert jb3.status == "done" and jb3.result["errors"], jb3.result     # offline: reported, not crashed

    qs = J(c.post("/api/recommend/queries", json={"topic_id": tid, "scope": "topic", "langs": ["en", "de"]}))["queries"]
    assert qs["en"] and qs["de"]
    literature._get = fake_get
    jq = jobs.wait(jobs.JOBS[J(c.post("/api/recommend", json={"topic_id": tid, "scope": "topic", "langs": ["de"], "kinds": ["article"],
                                                              "queries": {"de": ["ADHS Erwachsene Diagnostik"]}}))["job"]["id"]])
    assert jq.result["queries"] == {"de": ["ADHS Erwachsene Diagnostik"]} and jq.result["new"] >= 1

    # 5. presentations
    docs = J(c.get(f"/api/topics/{tid}/docs"))
    ids = [x["id"] for x in docs if x["text_len"] > 0]
    jd = jobs.wait(jobs.JOBS[J(c.post("/api/decks/generate", json={"topic_id": tid, "doc_ids": ids, "audience": "trainee doctors", "minutes": 15}))["job"]["id"]])
    assert jd.status == "done", jd.error
    did = jd.result["deck_id"]
    deck = J(c.get(f"/api/decks/{did}"))["data"]
    assert len(deck["slides"]) == 3 and deck["sources"]
    deck["slides"][0]["bullets"].append("edited bullet")
    J(c.put(f"/api/decks/{did}", json={"data": deck}))
    r = c.get(f"/api/decks/{did}/export")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    from pptx import Presentation
    prs = Presentation(io.BytesIO(r.data))
    assert len(prs.slides) == 1 + 3 + 1                            # title + slides + sources
    assert "edited bullet" in prs.slides[1].placeholders[1].text_frame.text and prs.slides[1].notes_slide.notes_text_frame.text
    assert c.get(f"/api/decks/{did}/export?format=md").data.startswith(b"# ")
    print("ALL API2 TESTS PASSED")


if __name__ == "__main__":
    main()
