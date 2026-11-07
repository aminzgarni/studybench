"""Swappable AI backends: Ollama (default, local), Anthropic, OpenAI-compatible, and an offline demo."""
import json
import random
import re

import requests

from . import config


class LLMError(Exception):
    pass


TIMEOUT = (10, 900)


def _split(messages):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _fail(r, who):
    try:
        detail = r.json()
    except Exception:
        detail = r.text[:300]
    raise LLMError(f"{who} returned HTTP {r.status_code}: {detail}")


# ------------------------------------------------------------------ public API
def complete(messages, json_mode=False, temperature=0.3, max_tokens=2500, model=None):
    s = config.get()
    p = s["provider"]
    try:
        if p == "demo":
            return _demo(messages)
        if p == "ollama":
            return _ollama(s, messages, json_mode, temperature, max_tokens, False, model)
        if p == "anthropic":
            return _anthropic(s, messages, max_tokens, False)
        if p == "openai":
            return _openai(s, messages, json_mode, temperature, max_tokens, False)
    except requests.ConnectionError:
        raise LLMError(_unreachable(s, p))
    except requests.Timeout:
        raise LLMError("The AI took too long to answer (timeout). Try a smaller model or less text.")
    raise LLMError(f"Unknown provider: {p}")


def stream(messages, temperature=0.3, max_tokens=2000):
    s = config.get()
    p = s["provider"]
    try:
        if p == "demo":
            for w in _demo(messages).split(" "):
                yield w + " "
            return
        if p == "ollama":
            yield from _ollama(s, messages, False, temperature, max_tokens, True)
        elif p == "anthropic":
            yield from _anthropic(s, messages, max_tokens, True)
        elif p == "openai":
            yield from _openai(s, messages, False, temperature, max_tokens, True)
        else:
            raise LLMError(f"Unknown provider: {p}")
    except requests.ConnectionError:
        raise LLMError(_unreachable(s, p))


def complete_json(messages, retries=1, **kw):
    raw = complete(messages, json_mode=True, **kw)
    try:
        return extract_json(raw)
    except ValueError:
        if retries <= 0:
            raise LLMError("The AI did not return valid JSON. Try again, or use a stronger model in Settings.")
        fix = messages + [{"role": "assistant", "content": raw[:4000]},
                          {"role": "user", "content": "That was not valid JSON. Reply again with ONLY the valid JSON object, no commentary."}]
        return complete_json(fix, retries - 1, **kw)


def extract_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\{\[]", t):
        try:
            obj, _ = dec.raw_decode(t[m.start():])
            return obj
        except ValueError:
            fixed = re.sub(r",\s*([}\]])", r"\1", t[m.start():])
            try:
                obj, _ = dec.raw_decode(fixed)
                return obj
            except ValueError:
                continue
    raise ValueError("no JSON found")


def list_models():
    s = config.get()
    if s["provider"] == "ollama":
        r = requests.get(s["ollama"]["host"].rstrip("/") + "/api/tags", timeout=8)
        return sorted(m["name"] for m in r.json().get("models", []))
    return []


def ping():
    """Returns (ok, message)."""
    s = config.get()
    try:
        if s["provider"] == "ollama":
            names = list_models()
            want = s["ollama"]["model"]
            if not names:
                return False, f"Ollama is running; model '{want}' is not downloaded yet. Press 'Download model' (done automatically on first use)."
            if want not in names and want + ":latest" not in names:
                return False, f"Model '{want}' is not downloaded yet (installed: {', '.join(names)}). Press 'Download model'."
            return True, f"Ollama OK · {len(names)} model(s) installed"
        out = complete([{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=10)
        return True, "Connected · reply: " + out.strip()[:40]
    except LLMError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{e.__class__.__name__}: {e}"


def _unreachable(s, p):
    if p == "ollama":
        return (f"Cannot reach Ollama at {s['ollama']['host']}. Install it from https://ollama.com/download "
                f"(run.bat tries this for you), then restart StudyBench.")
    return f"Cannot reach the {p} API. Check your internet connection and base URL."


# ------------------------------------------------------------------ Ollama
def _ollama_post(url, body, stream_):
    """POST to Ollama. Starts the server if needed and downloads a missing model automatically."""
    from . import setup
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, stream=stream_, timeout=TIMEOUT)
        except requests.ConnectionError:
            if attempt == 0 and setup.ensure_ollama():
                continue
            raise
        if r.status_code == 404 and attempt < 2:
            txt = r.text
            r.close()
            if "not found" in txt.lower():
                try:
                    setup.pull_model(body["model"])
                except RuntimeError as e:
                    raise LLMError(str(e))
                continue
            raise LLMError(f"Ollama returned HTTP 404: {txt[:200]}")
        return r
    raise LLMError("Ollama is not reachable.")


def _ollama(s, messages, json_mode, temperature, max_tokens, stream_, model=None):
    c = s["ollama"]
    body = {"model": model or c["model"], "messages": messages, "stream": stream_,
            "options": {"temperature": temperature, "num_ctx": int(c.get("num_ctx", 8192)),
                        "num_predict": max_tokens}}
    if json_mode:
        body["format"] = "json"
    url = c["host"].rstrip("/") + "/api/chat"
    if not stream_:
        r = _ollama_post(url, body, False)
        if r.status_code != 200:
            _fail(r, "Ollama")
        return r.json()["message"]["content"]
    return _ollama_stream(url, body)


def _ollama_stream(url, body):
    r = _ollama_post(url, body, True)
    with r:
        if r.status_code != 200:
            _fail(r, "Ollama")
        for line in r.iter_lines():
            if not line:
                continue
            j = json.loads(line)
            if j.get("message", {}).get("content"):
                yield j["message"]["content"]
            if j.get("done"):
                break


# ------------------------------------------------------------------ Anthropic
def _anthropic(s, messages, max_tokens, stream_):
    c = s["anthropic"]
    if not c["api_key"]:
        raise LLMError("No Anthropic API key. Add it in Settings.")
    system, rest = _split(messages)
    body = {"model": c["model"], "max_tokens": max_tokens, "messages": rest, "stream": stream_}
    if system:
        body["system"] = system
    headers = {"x-api-key": c["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
    url = "https://api.anthropic.com/v1/messages"
    if not stream_:
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            _fail(r, "Anthropic")
        return "".join(b.get("text", "") for b in r.json()["content"])
    return _sse(url, body, headers, "Anthropic",
                lambda j: j.get("delta", {}).get("text", "") if j.get("type") == "content_block_delta" else "")


# ------------------------------------------------------------------ OpenAI-compatible
def _openai(s, messages, json_mode, temperature, max_tokens, stream_):
    c = s["openai"]
    if not c["api_key"] and "openai.com" in c["base_url"]:
        raise LLMError("No OpenAI API key. Add it in Settings.")
    official = "api.openai.com" in c["base_url"]
    body = {"model": c["model"], "messages": messages, "stream": stream_}
    body["max_completion_tokens" if official else "max_tokens"] = max_tokens
    if not re.match(r"^(o\d|gpt-5)", c["model"]):
        body["temperature"] = temperature
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {c['api_key']}", "content-type": "application/json"}
    url = c["base_url"].rstrip("/") + "/chat/completions"
    if not stream_:
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            _fail(r, "OpenAI")
        return r.json()["choices"][0]["message"]["content"]
    return _sse(url, body, headers, "OpenAI",
                lambda j: (j.get("choices") or [{}])[0].get("delta", {}).get("content") or "")


def _sse(url, body, headers, who, pick):
    with requests.post(url, json=body, headers=headers, stream=True, timeout=TIMEOUT) as r:
        if r.status_code != 200:
            _fail(r, who)
        for line in r.iter_lines():
            if not line or not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                t = pick(json.loads(data))
            except ValueError:
                continue
            if t:
                yield t


# ------------------------------------------------------------------ Demo (no AI, canned answers)
def _demo(messages):
    blob = "\n".join(m["content"] for m in messages)
    task = (re.search(r"\[\[task:([a-z_:]+)\]\]", blob) or [None, "chat"])[1]
    n = int((re.search(r"\[\[n:(\d+)\]\]", blob) or [None, 2])[1])
    m = re.search(r"### SOURCE: ([^\n]+)\n(.+?)(?:\n###|\Z)", blob, re.S)
    snip = re.sub(r"\s+", " ", m.group(2)).strip()[:110] if m else "the course material"
    src = m.group(1) if m else "demo"
    note = "DEMO output: connect a real model in Settings."
    if task == "tags":
        return json.dumps({"category": "Demo", "tags": ["demo", "sample"], "priority": 2, "one_line": snip})
    if task in ("summary", "notes"):
        return (f"## Essence\nDemo summary. {note}\n\n## Key points\n- {snip}\n- Second demo point\n\n"
                f"## Practical takeaways\n- Apply this in your daily routine\n\n## To memorise\n- Item A\n- Item B\n\n"
                f"## Pitfalls\n- Do not rely on demo output\n\n## Think about\n- Why does this matter?")
    if task.startswith("items"):
        kind = task.split(":")[1]
        items = []
        for i in range(n):
            base = {"source": src}
            if kind == "mcq":
                base.update(question=f"(demo {i + 1}) Which statement fits «{snip}»?",
                            options=["Matches the source", "Contradicts the source", "Unrelated", "Unknown"],
                            answer_index=0, explanation=note)
            elif kind == "case":
                base.update(vignette=f"(demo {i + 1}) A fictional case related to: {snip}",
                            tasks=["What is your assessment?", "What are the next steps?"],
                            model_answer=note, key_points=["Point 1", "Point 2"])
            elif kind == "audit":
                base.update(question=f"(demo {i + 1}) An auditor asks: how do you ensure compliance regarding «{snip}»?",
                            expected_evidence=["Procedure document", "Training record"], model_answer=note,
                            key_points=["Process", "Evidence"])
            elif kind == "jury":
                base.update(question=f"(demo {i + 1}) Please explain «{snip}» to the committee.",
                            follow_ups=["Why?", "What if the other side refuses?"], key_points=["Core idea", "Limits"],
                            model_answer=note)
            elif kind == "elab":
                base.update(question=f"(demo {i + 1}) Why does «{snip}» matter in practice?", kind="why",
                            model_answer=note, key_points=["Reason 1"])
            else:
                base.update(question=f"(demo {i + 1}) Explain «{snip}».", key_points=["Key idea"], model_answer=note)
            items.append(base)
        return json.dumps({"items": items})
    if task == "evaluate":
        return json.dumps({"score": 70, "grade": 2, "covered": ["Main idea"], "missing": ["Detail"], "wrong": [],
                           "feedback": "Demo feedback. " + note})
    if task == "points":
        return json.dumps({"key_points": [snip, "Second demo key point", "Third demo key point"]})
    if task == "recall_compare":
        return json.dumps({"recalled": ["Demo point"], "missing": ["Another demo point"], "incorrect": [],
                           "coverage_pct": 50, "next_steps": "Re-read the summary. " + note})
    if task == "teach":
        return json.dumps({"score": 65, "strengths": ["Clear structure"], "errors": [], "missing": ["Example"],
                           "clarity_tips": ["Use a concrete example"], "followup_questions": ["Why?", "And then?"],
                           "improved_outline": ["Intro", "Core", "Example"]})
    if task == "jury_turn":
        return json.dumps({"action": "next", "say": "Thank you. Let us move on.", "note": "Demo"})
    if task == "jury_final":
        return json.dumps({"score": 60, "strengths": ["Calm delivery"], "weaknesses": ["Missing detail"],
                           "advice": "Practise structured answers. " + note})
    if task == "lit_queries":
        return json.dumps({"en": ["alcohol withdrawal management", "relapse prevention"], "de": ["Alkoholentzug Behandlung"],
                           "fr": ["sevrage alcoolique"]})
    if task == "outline":
        return json.dumps({"title": "Demo presentation", "subtitle": "Generated in demo mode",
                           "slides": [{"title": "Learning objectives", "focus": "what we will learn", "query": snip[:30]},
                                      {"title": "Key points", "focus": "the core", "query": "key"},
                                      {"title": "Questions for you", "focus": "discussion", "query": "why"}]})
    if task == "slide":
        return json.dumps({"bullets": [snip[:80], "Second point", "Third point"], "notes": note + " " + snip})
    if task == "deck":
        return json.dumps({"title": "Demo presentation", "subtitle": "Generated in demo mode",
                           "slides": [{"title": "Learning objectives", "bullets": [snip, "Second objective"], "notes": note},
                                      {"title": "Key points", "bullets": ["Point one", "Point two", "Point three"], "notes": note},
                                      {"title": "Questions for you", "bullets": ["Why does this matter?", "What would you do?"], "notes": note}]})
    if task == "mnemonic":
        return json.dumps({"acronym": "DEMO", "phrases": ["Dogs Eat Meat Often"], "tip": note})
    return f"{note} Closest passage in your sources: «{snip}» [1]"
