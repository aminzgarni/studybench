"""Local speech-to-text with faster-whisper (optional dependency)."""
import importlib.util
from pathlib import Path

_models = {}
ENGINE = None   # test hook: callable(path, opts, progress, check) -> (segments[(start,end,text)], info{duration,language})


def available():
    return ENGINE is not None or importlib.util.find_spec("faster_whisper") is not None


def _model(name, device, compute):
    key = (name, device, compute)
    if key not in _models:
        import os
        from faster_whisper import WhisperModel
        kw = {"cpu_threads": min(8, os.cpu_count() or 4)} if device == "cpu" else {}
        _models[key] = WhisperModel(name, device=device, compute_type=compute, **kw)
    return _models[key]


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def to_markdown(segs, info, name, group_seconds=45):
    head = (f"# Transcript: {name}\n\n*Language: {info.get('language', '?')} · "
            f"Duration: {hms(info.get('duration', 0))} · automatic transcription, may contain errors*\n")
    paras, cur, start, last_end = [], [], 0.0, 0.0
    for s, e, t in segs:
        if not t:
            continue
        if cur and (s - start >= group_seconds or s - last_end > 4):
            paras.append((start, " ".join(cur)))
            cur = []
        if not cur:
            start = s
        cur.append(t)
        last_end = e
    if cur:
        paras.append((start, " ".join(cur)))
    return head + "\n" + "\n\n".join(f"**[{hms(s)}]** {t}" for s, t in paras) + "\n"


def decode_audio(path, sr=16000):
    """Decode any audio/video file to 16 kHz mono float32 with PyAV. Own implementation: faster-whisper's built-in
    decoder passes an argument (metadata_errors) that older PyAV versions reject."""
    import gc
    import io

    import av
    import numpy as np
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sr)
    buf = io.BytesIO()

    def put(out):
        if out is None:
            return
        for f in (out if isinstance(out, list) else [out]):
            buf.write(f.to_ndarray().tobytes())

    with av.open(str(path), mode="r") as container:
        if not container.streams.audio:
            raise RuntimeError("This file has no audio track.")
        for packet in container.demux(container.streams.audio[0]):
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:     # damaged packet: skip it
                continue
            for frame in frames:
                frame.pts = None
                put(resampler.resample(frame))
        put(resampler.resample(None))
    del resampler
    gc.collect()
    if not buf.getbuffer().nbytes:
        raise RuntimeError("No audio could be decoded from this file.")
    return np.frombuffer(buf.getbuffer(), dtype=np.int16).astype(np.float32) / 32768.0


def _gpu_error(e):
    m = str(e).lower()
    return any(k in m for k in ("cublas", "cudnn", "cudart", "nvrtc", "cuda", "libcu", ".dll", "no kernel image"))


def _transcribe(audio, opts, device, compute, progress, check):
    progress(0.0, "Loading speech model (first time: downloading)…")
    m = _model(opts.get("model", "small"), device, compute)
    lang = opts.get("language", "auto")
    glossary = (opts.get("glossary") or "").strip()
    seg_iter, inf = m.transcribe(audio, language=None if lang in ("", "auto") else lang, vad_filter=True, beam_size=5,
                                 initial_prompt=glossary[:600] or None)
    info = {"duration": inf.duration, "language": inf.language}
    segs = []
    for s in seg_iter:
        check()
        segs.append((s.start, s.end, s.text.strip()))
        if inf.duration:
            progress(min(0.99, s.end / inf.duration), f"{hms(s.end)} / {hms(inf.duration)}")
    return segs, info


FORCE_CPU = False


def _gpu_error(e):
    m = str(e).lower()
    return any(k in m for k in ("cublas", "cudnn", "cudart", "cuda", "nvrtc", "cannot be loaded", "is not found"))


def _add_cuda_dlls():
    """Use NVIDIA libraries installed with pip (nvidia-cublas-cu12 / nvidia-cudnn-cu12), if present."""
    import os
    import sys
    if sys.platform != "win32":
        return
    for name in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            mod = __import__(name, fromlist=["x"])
        except Exception:  # noqa: BLE001
            continue
        for base in getattr(mod, "__path__", []):
            b = Path(base) / "bin"
            if b.exists():
                try:
                    os.add_dll_directory(str(b))
                except (OSError, AttributeError):
                    pass
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def run(path, opts, progress, check):
    """Returns (markdown, info). Falls back to the CPU when GPU libraries (e.g. cublas64_12.dll) are missing."""
    global FORCE_CPU
    path = Path(path)
    if ENGINE is not None:
        segs, info = ENGINE(path, opts, progress, check)
        return to_markdown(segs, info, path.name), info
    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError("faster-whisper is not installed. Run: pip install faster-whisper")
    _add_cuda_dlls()
    lang = opts.get("language", "auto")
    glossary = (opts.get("glossary") or "").strip()
    dev, comp = opts.get("device", "auto") or "auto", opts.get("compute_type", "auto") or "auto"
    if FORCE_CPU:
        dev = "cpu"
    if dev == "cpu" and comp == "auto":
        comp = "int8"
    progress(0.0, "Reading audio…")
    audio = decode_audio(path)
    for attempt in range(2):
        try:
            progress(0.0, "Loading speech model (first time: downloading)…")
            m = _model(opts.get("model", "small"), dev, comp)
            seg_iter, inf = m.transcribe(audio, language=None if lang in ("", "auto") else lang, vad_filter=True,
                                         beam_size=5, initial_prompt=glossary[:600] or None)
            info = {"duration": inf.duration, "language": inf.language}
            segs = []
            for s in seg_iter:
                check()
                segs.append((s.start, s.end, s.text.strip()))
                if inf.duration:
                    progress(min(0.99, s.end / inf.duration), f"{hms(s.end)} / {hms(inf.duration)}")
            return to_markdown(segs, info, path.name), info
        except Exception as e:  # noqa: BLE001
            if attempt == 0 and dev != "cpu" and _gpu_error(e):
                FORCE_CPU = True
                dev, comp = "cpu", "int8"
                progress(0.0, "GPU libraries not available: continuing on the CPU (slower)…")
                continue
            raise
