"""First-run setup: start Ollama, download the AI model, the speech model and OCR language data automatically."""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from . import config

STATE = {}                      # key -> {label, status: running|done|error, pct, msg}
_lock = threading.Lock()
_pull_lock = threading.Lock()
SUGGESTED = [("llama3.1:8b", "4.7 GB · good all-rounder"), ("qwen2.5:7b", "4.7 GB · strong multilingual"),
             ("qwen2.5:14b", "9 GB · better German, needs 16 GB RAM"), ("gemma3:12b", "8 GB · good German")]


def _set(key, label, status, pct=None, msg=""):
    with _lock:
        STATE[key] = {"key": key, "label": label, "status": status, "pct": pct, "msg": msg, "ts": time.time()}


def listing():
    with _lock:
        return list(STATE.values())


# ------------------------------------------------------------------ Ollama
def _host():
    return config.get()["ollama"]["host"].rstrip("/")


def ollama_up(host=None):
    try:
        return requests.get((host or _host()) + "/api/tags", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def ollama_binary():
    b = shutil.which("ollama")
    if b:
        return b
    for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"), r"C:\Program Files\Ollama\ollama.exe",
              "/usr/local/bin/ollama", "/opt/homebrew/bin/ollama", "/Applications/Ollama.app/Contents/Resources/ollama"):
        if os.path.exists(p):
            return p
    return None


def ensure_ollama():
    """Make sure the Ollama server is running; start it if installed. Returns True when reachable."""
    if ollama_up():
        return True
    exe = ollama_binary()
    if not exe:
        _set("ollama", "Local AI (Ollama)", "error", msg="Ollama is not installed. Download it from https://ollama.com/download, install it, then restart StudyBench.")
        return False
    _set("ollama", "Local AI (Ollama)", "running", msg="starting…")
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000                         # CREATE_NO_WINDOW
    try:
        subprocess.Popen([exe, "serve"], **kw)
    except OSError as e:
        _set("ollama", "Local AI (Ollama)", "error", msg=f"Could not start Ollama: {e}")
        return False
    for _ in range(40):
        if ollama_up():
            _set("ollama", "Local AI (Ollama)", "done", msg="running")
            return True
        time.sleep(0.5)
    _set("ollama", "Local AI (Ollama)", "error", msg="Ollama did not start. Open the Ollama app once, then try again.")
    return False


def installed_models():
    try:
        return sorted(m["name"] for m in requests.get(_host() + "/api/tags", timeout=5).json().get("models", []))
    except Exception:  # noqa: BLE001
        return []


def has_model(name):
    names = installed_models()
    return name in names or (":" not in name and name + ":latest" in names)


def pull_model(name):
    """Download an Ollama model (blocking). Progress is published in STATE."""
    key = "model:" + name
    with _pull_lock:
        if has_model(name):
            _set(key, f"AI model {name}", "done", 100, "installed")
            return True
        if not ensure_ollama():
            raise RuntimeError(STATE.get("ollama", {}).get("msg") or "Ollama is not available")
        _set(key, f"Downloading AI model {name}", "running", 0, "starting…")
        try:
            with requests.post(_host() + "/api/pull", json={"name": name, "stream": True}, stream=True, timeout=(10, 300)) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"Ollama could not download '{name}' (HTTP {r.status_code}): {r.text[:200]}")
                import json
                for line in r.iter_lines():
                    if not line:
                        continue
                    j = json.loads(line)
                    if j.get("error"):
                        raise RuntimeError(f"Ollama could not download '{name}': {j['error']}")
                    if j.get("total"):
                        _set(key, f"Downloading AI model {name}", "running", round(100 * j.get("completed", 0) / j["total"]), j.get("status", ""))
                    else:
                        _set(key, f"Downloading AI model {name}", "running", None, j.get("status", ""))
        except requests.RequestException as e:
            _set(key, f"AI model {name}", "error", msg=f"Download failed (internet?): {e}")
            raise RuntimeError(f"Could not download model '{name}'. Check your internet connection.") from e
        except RuntimeError as e:
            _set(key, f"AI model {name}", "error", msg=str(e))
            raise
        _set(key, f"AI model {name}", "done", 100, "installed")
        return True


def ensure_model():
    s = config.get()
    if s["provider"] != "ollama":
        return
    try:
        if ensure_ollama():
            pull_model(s["ollama"]["model"])
    except Exception:  # noqa: BLE001 - state already records the problem
        pass


# ------------------------------------------------------------------ speech model
def prefetch_whisper():
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        _set("whisper", "Speech-to-text", "error", msg="faster-whisper is not installed. Re-run run.bat.")
        return
    name = config.get()["whisper"]["model"]
    _set("whisper", f"Downloading speech model '{name}'", "running", None, "first time only")
    try:
        from faster_whisper.utils import download_model
        download_model(name)
        _set("whisper", f"Speech model '{name}'", "done", 100, "ready")
    except Exception as e:  # noqa: BLE001
        _set("whisper", "Speech model", "error", msg=f"Download failed (internet?): {e}")


# ------------------------------------------------------------------ OCR language data
TESSDATA = config.LOCAL_DIR / "tessdata"      # language data stays on this computer


def tesseract_cmd():
    b = shutil.which("tesseract")
    if b:
        return b
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if os.path.exists(p):
            return p
    return None


def ensure_tessdata():
    if not tesseract_cmd():
        return
    TESSDATA.mkdir(parents=True, exist_ok=True)
    for lang in ("deu", "eng"):
        f = TESSDATA / f"{lang}.traineddata"
        if f.exists() and f.stat().st_size > 1000:
            continue
        _set("ocr", "Downloading OCR language data", "running", None, lang)
        try:
            r = requests.get(f"https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata", timeout=120)
            r.raise_for_status()
            f.write_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            _set("ocr", "OCR language data", "error", msg=f"Download failed: {e}")
            return
    _set("ocr", "OCR language data", "done", 100, "ready")


def startup():
    """Run everything in the background so the app opens immediately."""
    def work():
        for fn in (ensure_model, prefetch_whisper, ensure_tessdata):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=work, daemon=True).start()
