"""Paths and user settings (stored as JSON next to the database)."""
import copy
import json
import os
import platform
import shutil
import socket
import sqlite3
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = Path(os.environ.get("STUDYBENCH_LOCAL") or Path.home() / ".studybench")   # stays on THIS computer
LOCATION_FILE = LOCAL_DIR / "location.json"        # remembers where the shared data folder is
SECRETS_FILE = LOCAL_DIR / "secrets.json"          # API keys: never stored in a synced folder
MARKER = "studybench-data.json"                    # identifies a StudyBench data folder (also in cloud drives)
MACHINE = socket.gethostname() or "this computer"
NOTICE = {"fallback": "", "adopted": ""}


def _saved_location():
    try:
        return json.loads(LOCATION_FILE.read_text(encoding="utf-8")).get("data_dir") or ""
    except Exception:  # noqa: BLE001
        return ""


def _prepare(home):
    """Create the folder structure. Raises if the (drive) location is not reachable."""
    home = Path(home)
    if not home.exists() and not home.parent.exists():
        raise OSError(f"{home.parent} is not available (drive offline or not mounted)")
    home.mkdir(parents=True, exist_ok=True)
    (home / "topics").mkdir(exist_ok=True)
    m = home / MARKER
    if not m.exists():
        m.write_text(json.dumps({"app": "StudyBench", "format": 1, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "created_on": MACHINE, "os": platform.system()}), encoding="utf-8")
    return home


def _pick_home():
    env = os.environ.get("STUDYBENCH_HOME")
    if env:
        return _prepare(env)
    saved = _saved_location()
    if saved:
        try:
            return _prepare(saved)
        except OSError as e:
            NOTICE["fallback"] = (f"Your data folder {saved} is not reachable ({e}). StudyBench uses a temporary local "
                                  "folder until it is back. Start your cloud drive and restart StudyBench.")
    return _prepare(APP_DIR / "data")


HOME = _pick_home()
SETTINGS_FILE = HOME / "settings.json"


def tmp_dir():
    d = LOCAL_DIR / "tmp"                           # temporary uploads stay local, they are not synced
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS = {
    "appearance": "system",            # system | light | dark
    "allowed_extensions": ("pptx docx pdf txt md markdown csv vtt srt png jpg jpeg webp bmp tif tiff gif "
                           "mp3 m4a wav ogg oga flac aac opus wma amr 3gp mp4 m4v mkv webm mov avi"),
    "content_language": "de",          # language of summaries / exercises: de | en | auto
    "provider": "ollama",              # ollama | anthropic | openai | demo
    "ollama": {"host": "http://localhost:11434", "model": "llama3.1:8b", "fast_model": "", "num_ctx": 8192},
    "anthropic": {"api_key": "", "model": "claude-sonnet-5-5"},
    "openai": {"api_key": "", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "context_chars": 12000,            # how much source text goes into one prompt
    "whisper": {"model": "small", "device": "auto", "compute_type": "auto",
                "language": "auto", "glossary": ""},
    "focus": {"focus_min": 25, "break_min": 5, "long_break_min": 15, "cycles": 4,
              "recall_pause": True, "sound": True},
    "practice": {"new_per_session": 10, "session_size": 20, "think_delay": 3},
}

_lock = threading.Lock()
MASK = "••••••••"


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_secret(prov, key):
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    sec = _read_json(SECRETS_FILE)
    sec.setdefault(prov, {})["api_key"] = key
    SECRETS_FILE.write_text(json.dumps(sec), encoding="utf-8")
    try:
        os.chmod(SECRETS_FILE, 0o600)
    except OSError:
        pass


def move_keys_out_of_shared_settings():
    """Older versions kept API keys in settings.json (which may sit in a cloud drive): move them to this computer."""
    cur = _read_json(SETTINGS_FILE)
    changed = False
    for prov in ("anthropic", "openai"):
        k = (cur.get(prov) or {}).get("api_key")
        if k:
            if not (_read_json(SECRETS_FILE).get(prov) or {}).get("api_key"):
                _write_secret(prov, k)
            cur[prov].pop("api_key", None)
            changed = True
    if changed:
        SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")


def get():
    s = copy.deepcopy(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            _merge(s, json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    sec = _read_json(SECRETS_FILE)
    for prov in ("anthropic", "openai"):                 # keys live on this computer only
        k = (sec.get(prov) or {}).get("api_key")
        if k:
            s[prov]["api_key"] = k
    if not s["anthropic"]["api_key"]:
        s["anthropic"]["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    if not s["openai"]["api_key"]:
        s["openai"]["api_key"] = os.environ.get("OPENAI_API_KEY", "")
    return s


def update(patch):
    with _lock:
        cur = {}
        if SETTINGS_FILE.exists():
            try:
                cur = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                cur = {}
        for prov in ("anthropic", "openai"):          # never overwrite a key with the mask
            k = patch.get(prov, {}).get("api_key")
            if k is not None and (k == "" or k.startswith("•")):
                patch[prov].pop("api_key")
            elif k is not None:
                _write_secret(prov, k)                  # not into the (possibly synced) settings file
                patch[prov].pop("api_key")
        _merge(cur, patch)
        SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
    return get()


def public():
    s = get()
    for prov in ("anthropic", "openai"):
        s[prov]["has_key"] = bool(s[prov]["api_key"])
        s[prov]["api_key"] = MASK if s[prov]["api_key"] else ""
    return s


def allowed_exts():
    raw = get().get("allowed_extensions") or ""
    return {e.strip().lower().lstrip(".*") for e in raw.replace(",", " ").split() if e.strip()}


# ------------------------------------------------------------------ files: relative paths work on every computer
def rel_path(p):
    """Store paths inside the data folder relative to it, with forward slashes (same on Windows and Mac)."""
    p = Path(p)
    try:
        return p.relative_to(HOME).as_posix()
    except ValueError:
        return str(p)


def resolve(stored):
    """Turn a stored path into a real file path on this computer (None if it is empty or missing)."""
    if not stored:
        return None
    p = Path(stored)
    if not p.is_absolute() and not (len(stored) > 1 and stored[1] == ":"):
        q = HOME / Path(*stored.replace("\\", "/").split("/"))
        return q if q.exists() else None
    if p.exists():
        return p
    parts = stored.replace("\\", "/").split("/")
    if "topics" in parts:                               # absolute path written on another computer / old data folder
        q = HOME / Path(*parts[parts.index("topics"):])
        if q.exists():
            return q
    return None


# ------------------------------------------------------------------ shared data folder (OneDrive, Google Drive, ...)
def _cloud_roots():
    home, out = Path.home(), []
    sysname = platform.system()
    def add(label, path):
        path = Path(path)
        try:
            if path.is_dir() and all(path != o[1] for o in out):
                out.append((label, path))
        except OSError:
            pass
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            add("OneDrive", os.environ[var])
    for d in sorted(home.glob("OneDrive*")):
        add("OneDrive", d)
    add("Dropbox", home / "Dropbox")
    add("Google Drive", home / "Google Drive" / "My Drive")
    add("Google Drive", home / "Google Drive")
    add("iCloud Drive", home / "iCloudDrive")
    add("iCloud Drive", home / "iCloud Drive")
    if sysname == "Windows":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            add("Google Drive", f"{letter}:/My Drive")
    if sysname == "Darwin":
        add("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
        for d in sorted((home / "Library" / "CloudStorage").glob("*")):
            n = d.name.lower()
            label = ("Google Drive" if n.startswith("googledrive") else "OneDrive" if n.startswith("onedrive")
                     else "Dropbox" if n.startswith("dropbox") else d.name)
            add(label, d / "My Drive" if (d / "My Drive").is_dir() else d)
    return out


def candidates():
    """Places that could hold the data: each cloud drive found, and whether StudyBench data is already there."""
    res = []
    for label, root in _cloud_roots():
        found = None
        try:
            for c in [root / "StudyBench-Data"] + [d for d in root.iterdir() if d.is_dir()]:
                if (c / MARKER).exists():
                    found = c
                    break
        except OSError:
            pass
        res.append({"label": label, "root": str(root), "path": str(found or root / "StudyBench-Data"), "has_data": bool(found)})
    return res


def in_cloud(path=None):
    p = Path(path or HOME)
    return any(r in p.parents or r == p for _, r in _cloud_roots())


def has_content(home):
    home = Path(home)
    return (home / "studybench.db").exists()


def backup_db(keep=7):
    """One safe copy of the database per day (protects against sync conflicts)."""
    db = HOME / "studybench.db"
    if not db.exists():
        return
    d = HOME / "backups"
    d.mkdir(exist_ok=True)
    dst = d / f"studybench-{time.strftime('%Y%m%d')}.db"
    if not dst.exists():
        try:
            src, out = sqlite3.connect(str(db)), sqlite3.connect(str(dst))
            with out:
                src.backup(out)
            src.close()
            out.close()
        except Exception:  # noqa: BLE001
            return
    for old in sorted(d.glob("studybench-*.db"))[:-keep]:
        old.unlink(missing_ok=True)


def auto_adopt():
    """Fresh install on a new computer: if exactly one cloud drive already holds StudyBench data, use it."""
    if os.environ.get("STUDYBENCH_HOME") or _saved_location() or has_content(HOME):
        return ""
    found = [c for c in candidates() if c["has_data"]]
    if len(found) == 1:
        switch(found[0]["path"])
        NOTICE["adopted"] = f"Found your StudyBench data in {found[0]['label']} and connected to it."
    return NOTICE["adopted"]


def lock_other():
    """Is another computer using the same shared folder right now? (advisory, based on a heartbeat file)"""
    d = _read_json(HOME / ".in-use.json")
    if d and d.get("machine") != MACHINE and time.time() - d.get("time", 0) < 180:
        return d
    return None


def heartbeat():
    try:
        (HOME / ".in-use.json").write_text(json.dumps({"machine": MACHINE, "os": platform.system(), "time": time.time()}), encoding="utf-8")
    except OSError:
        pass


def release_lock():
    f = HOME / ".in-use.json"
    if _read_json(f).get("machine") == MACHINE:
        f.unlink(missing_ok=True)


def switch(new_dir, move=False):
    """Use another data folder. move=True copies the current data into it first. Raises ValueError on problems."""
    global HOME, SETTINGS_FILE
    new = Path(os.path.expanduser(str(new_dir).strip().strip('"')))
    if not str(new).strip():
        raise ValueError("Choose a folder.")
    old = HOME
    if new.resolve() == old.resolve():
        raise ValueError("This is already the folder in use.")
    has_data = (new / MARKER).exists() or has_content(new)
    if move and has_data:
        raise ValueError("That folder already contains StudyBench data. Use it instead of moving into it (or choose an empty folder).")
    if not move and new.exists() and not has_data and any(new.iterdir()):
        raise ValueError("That folder is not empty and holds no StudyBench data. Choose an empty folder or a new name.")
    try:
        new = _prepare(new)
    except OSError as e:
        raise ValueError(f"Cannot use that folder: {e}") from e
    if move:
        for name in ("studybench.db", "settings.json"):
            if (old / name).exists():
                shutil.copy2(old / name, new / name)
        if (old / "topics").exists():
            shutil.copytree(old / "topics", new / "topics", dirs_exist_ok=True)
    release_lock()
    HOME, SETTINGS_FILE = new, new / "settings.json"
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text(json.dumps({"data_dir": str(new)}), encoding="utf-8")
    move_keys_out_of_shared_settings()
    heartbeat()
    from . import db, rag
    db.reset()
    rag.bump()
    return new


def use_default():
    global HOME, SETTINGS_FILE
    release_lock()
    LOCATION_FILE.unlink(missing_ok=True)
    HOME = _prepare(APP_DIR / "data")
    SETTINGS_FILE = HOME / "settings.json"
    heartbeat()
    from . import db, rag
    db.reset()
    rag.bump()
    return HOME
