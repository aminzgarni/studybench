"""Chunking + a small BM25 index (no heavy dependencies; works offline)."""
import math
import random
import re
import unicodedata
from collections import Counter

from . import db

_STOP_RAW = ("der die das und oder ein eine einer eines einem einen ist sind wird werden von mit für auf bei den dem des "
             "im in zu zum zur als auch nicht sich es an aus nach über um wie wenn dass daß the of and to a in is are for "
             "on with as by that this be or an it at from was were has have").split()


def norm(s):
    s = s.lower().replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


STOP = {norm(w) for w in _STOP_RAW}


def tokens(s):
    # crude stemming: first 7 chars, so "Behandlung/Behandlungen/behandelt" share a stem
    return [w[:7] for w in re.findall(r"[^\W_]{2,}", norm(s)) if w not in STOP]


_TS = re.compile(r"\[(\d{1,2}:\d{2}:\d{2})\]")


def chunk_markdown(text, max_chars=1400):
    """Split markdown into (locator, text) chunks. Locator = last '## ' heading or first transcript timestamp."""
    chunks, loc, buf, buf_loc = [], "", [], ""

    def flush():
        nonlocal buf, buf_loc
        t = "\n".join(buf).strip()
        if t:
            chunks.append((buf_loc or loc, t))
        buf, buf_loc = [], ""

    for line in text.splitlines():
        h = re.match(r"^##\s+(.*)", line)
        if h and not line.startswith("###"):
            flush()
            loc = h.group(1).strip()
            continue
        if not buf:
            buf_loc = loc
        ts = _TS.search(line)
        if ts and not buf_loc.startswith("["):
            buf_loc = f"[{ts.group(1)}]"
        buf.append(line)
        if sum(len(b) + 1 for b in buf) >= max_chars and line.strip() == "" or sum(len(b) + 1 for b in buf) >= max_chars * 1.4:
            flush()
    flush()
    return chunks


_version = 0
_cache = {}


def bump():
    global _version
    _version += 1
    _cache.clear()


def store_chunks(doc_id, topic_id, text):
    db.x("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    rows = [(doc_id, topic_id, i, loc, t) for i, (loc, t) in enumerate(chunk_markdown(text))]
    db.xmany("INSERT INTO chunks(doc_id,topic_id,idx,locator,text) VALUES(?,?,?,?,?)", rows)
    bump()


def delete_chunks(doc_id):
    db.x("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    bump()


def load_chunks(doc_ids):
    if not doc_ids:
        return []
    ph = ",".join("?" * len(doc_ids))
    return db.q(f"""SELECT c.id, c.doc_id, c.idx, c.locator, c.text, d.filename
                    FROM chunks c JOIN documents d ON d.id=c.doc_id
                    WHERE c.doc_id IN ({ph}) ORDER BY d.seq, c.doc_id, c.idx""", tuple(doc_ids))


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        self.toks = [Counter(tokens(c["text"] + " " + (c["locator"] or ""))) for c in chunks]
        self.len = [sum(t.values()) or 1 for t in self.toks]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        df = Counter()
        for t in self.toks:
            df.update(t.keys())
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query, k=6):
        q = set(tokens(query))
        scored = []
        for i, t in enumerate(self.toks):
            s = 0.0
            for w in q:
                f = t.get(w)
                if f:
                    s += self.idf[w] * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [self.chunks[i] for _, i in scored[:k]]


def search(doc_ids, query, k=6):
    key = (tuple(sorted(doc_ids)), _version)
    if key not in _cache:
        _cache[key] = Index(load_chunks(doc_ids))
    return _cache[key].search(query, k)


def fmt(c):
    return f"### SOURCE: {c['filename']} · {c['locator'] or '-'}\n{c['text']}"


def sample_context(doc_ids, budget=12000, seed=None):
    """Whole text if it fits; otherwise a random, document-ordered sample that spreads over everything."""
    chunks = load_chunks(doc_ids)
    if sum(len(c["text"]) for c in chunks) <= budget:
        picked = chunks
    else:
        rnd = random.Random(seed)
        order = list(range(len(chunks)))
        rnd.shuffle(order)
        used, keep = 0, []
        for i in order:
            L = len(chunks[i]["text"])
            if used + L > budget:
                continue
            keep.append(i)
            used += L
        picked = [chunks[i] for i in sorted(keep)]
    return "\n\n".join(fmt(c) for c in picked), picked


def pack(chunks, budget):
    """Greedy-group chunks into parts of <= budget chars (for map-reduce summaries)."""
    parts, cur, n = [], [], 0
    for c in chunks:
        L = len(c["text"]) + 60
        if cur and n + L > budget:
            parts.append(cur)
            cur, n = [], 0
        cur.append(c)
        n += L
    if cur:
        parts.append(cur)
    return parts
