"""Shared data folder: two 'computers' (Windows and Mac) use the same cloud-drive folder."""
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_A = tempfile.mkdtemp()
os.environ["STUDYBENCH_HOME"] = tempfile.mkdtemp()
os.environ["STUDYBENCH_LOCAL"] = LOCAL_A
sys.path.insert(0, str(ROOT))
from core import config  # noqa: E402
import app as appmod  # noqa: E402

c = appmod.app.test_client()
config.update({"provider": "demo"})


def J(r):
    assert r.status_code == 200, (r.status_code, r.data[:300])
    return r.get_json()


# --- computer A: create a topic and a document in the default folder
tid = J(c.post("/api/topics", json={"name": "Shared topic"}))["id"]
r = J(c.post(f"/api/topics/{tid}/upload", data={"files": (io.BytesIO(b"Alcohol withdrawal is treated with benzodiazepines."), "notes.txt")},
             content_type="multipart/form-data"))
did = r[0]["id"]
from core import db  # noqa: E402
sp = db.one("SELECT stored_path FROM documents WHERE id=?", (did,))["stored_path"]
assert not os.path.isabs(sp) and "\\" not in sp, sp                    # relative, forward slashes
assert c.get(f"/api/docs/{did}/file").status_code == 200

# --- API keys never go into the (synced) settings file
config.update({"provider": "anthropic", "anthropic": {"api_key": "sk-secret-123"}})
assert config.get()["anthropic"]["api_key"] == "sk-secret-123"
assert "sk-secret" not in config.SETTINGS_FILE.read_text()
assert "sk-secret" in (Path(LOCAL_A) / "secrets.json").read_text()
config.update({"provider": "demo"})

# --- move into a "cloud drive" folder
drive = Path(tempfile.mkdtemp()) / "OneDrive"
drive.mkdir()
shared = drive / "StudyBench-Data"
info = J(c.post("/api/storage", json={"path": str(shared), "mode": "move"}))
assert info["path"] == str(shared) and (shared / config.MARKER).exists() and (shared / "studybench.db").exists()
assert J(c.get("/api/topics"))[0]["name"] == "Shared topic"
assert c.get(f"/api/docs/{did}/file").status_code == 200
assert json.loads((Path(LOCAL_A) / "location.json").read_text())["data_dir"] == str(shared)

# moving into a folder that already holds data, or into a non-empty unrelated folder, is refused
assert c.post("/api/storage", json={"path": str(shared), "mode": "move"}).status_code in (400, 500)
other = Path(tempfile.mkdtemp())
(other / "x.txt").write_text("x")
assert c.post("/api/storage", json={"path": str(other), "mode": "use"}).status_code in (400, 500)

# --- computer B (Mac) has a different absolute path for the same files: still found
db.x("UPDATE documents SET stored_path=? WHERE id=?", (r"C:\Users\someone\StudyBench\data\topics\%d\files\%s" % (tid, Path(sp).name), did))
assert c.get(f"/api/docs/{did}/file").status_code == 200
db.x("UPDATE documents SET stored_path=? WHERE id=?", (sp, did))

# --- the other computer using the folder is reported
(shared / ".in-use.json").write_text(json.dumps({"machine": "OTHER-PC", "os": "Darwin", "time": __import__("time").time()}))
assert J(c.get("/api/storage"))["other_machine"]["name"] == "OTHER-PC"
(shared / ".in-use.json").unlink()

# --- daily backup
config.backup_db()
assert list((shared / "backups").glob("studybench-*.db"))

# --- fresh computer: a different user home with the same OneDrive folder -> data is found automatically
fake_home = Path(tempfile.mkdtemp())
(fake_home / "OneDrive").mkdir()
import shutil  # noqa: E402
shutil.copytree(shared, fake_home / "OneDrive" / "StudyBench-Data", ignore=shutil.ignore_patterns("backups"))
appcopy = Path(tempfile.mkdtemp()) / "app"                      # a clean program folder with an empty default data folder
shutil.copytree(ROOT / "core", appcopy / "core", ignore=shutil.ignore_patterns("__pycache__"))
code = f"""
import os, sys
sys.path.insert(0, r'{appcopy}')
from core import config
msg = config.auto_adopt()
print('MSG', msg)
from core import db
print('TOPIC', db.q('select name from topics')[0]['name'])
print('HOME', config.HOME)
"""
env = {k: v for k, v in os.environ.items() if k not in ("STUDYBENCH_HOME", "STUDYBENCH_LOCAL", "OneDrive")}
env.update(HOME=str(fake_home), USERPROFILE=str(fake_home), STUDYBENCH_LOCAL=str(fake_home / ".studybench"))
out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=str(fake_home))
assert "TOPIC Shared topic" in out.stdout and "OneDrive" in out.stdout, (out.stdout, out.stderr[-500:])
print("ALL STORAGE TESTS PASSED")
