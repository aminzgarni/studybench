"""Spaced repetition: intervals grow after correct answers, shrink after wrong ones; weak items return sooner."""
import json
import random
from collections import defaultdict
from datetime import timedelta

from . import db

MAX_IVL = 180.0           # days
LABELS = {0: "Again", 1: "Hard", 2: "Good", 3: "Easy"}


def is_weak(it):
    return bool(it["flag"]) or (it["lapses"] >= 2 and it["streak"] < 3) or it.get("last_grade") == 0


def schedule(it, grade, now=None):
    """Return the new scheduling fields for `it` after answering with `grade` (0 again, 1 hard, 2 good, 3 easy)."""
    now = now or db.now_dt()
    ease, ivl, lapses, streak = it["ease"], it["ivl"], it["lapses"], it["streak"]
    if grade == 0:
        lapses += 1
        streak = 0
        ease = max(1.3, ease - 0.2)
        ivl = 0.0
        due = now + timedelta(minutes=10)
        state = "learning"
    else:
        streak += 1
        if ivl < 1:                                      # new, or relearning after a lapse
            ivl = {1: 0.5, 2: 1.0, 3: 3.0}[grade]
        elif grade == 1:
            ivl = max(1.0, ivl * 1.2)
            ease = max(1.3, ease - 0.15)
        elif grade == 2:
            ivl = ivl * ease
        else:
            ivl = ivl * ease * 1.3
            ease += 0.15
        ivl = min(ivl, MAX_IVL)
        if is_weak({"flag": it["flag"], "lapses": lapses, "streak": streak, "last_grade": grade}):
            ivl = min(ivl, 3.0)                          # weak items come back often until they stick
        due = now + timedelta(days=ivl)
        state = "review"
    return {"ease": round(ease, 3), "ivl": round(ivl, 3), "due": db.iso(due), "state": state,
            "reps": it["reps"] + 1, "lapses": lapses, "streak": streak, "last_grade": grade,
            "last_ts": db.iso(now)}


def fmt_ivl(days):
    if days < 1 / 24:
        return f"{max(1, round(days * 1440))} min"
    if days < 1:
        return f"{round(days * 24)} h"
    if days < 30:
        return f"{round(days)} d"
    return f"{round(days / 30, 1)} mo"


def preview(it):
    out = {}
    for g in range(4):
        s = schedule(it, g)
        out[g] = "10 min" if g == 0 else fmt_ivl(s["ivl"])
    return out


def apply(item_id, grade, seconds=0, mode="review"):
    it = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    if not it:
        raise ValueError("Unknown item")
    s = schedule(it, grade)
    cols = ",".join(f"{k}=?" for k in s)
    db.x(f"UPDATE items SET {cols} WHERE id=?", (*s.values(), item_id))
    db.x("INSERT INTO reviews(item_id,topic_id,ts,grade,seconds,mode) VALUES(?,?,?,?,?,?)",
         (item_id, it["topic_id"], db.iso(), grade, seconds, mode))
    return s


def hydrate(it):
    it = dict(it)
    it["data"] = json.loads(it["data"] or "{}")
    it["weak"] = is_weak(it)
    return it


def build_queue(topic_ids, mode="due", limit=20, types=None, new_limit=10, interleave=True, doc_ids=None):
    if not topic_ids:
        return []
    ph = ",".join("?" * len(topic_ids))
    sql, args = f"SELECT * FROM items WHERE suspended=0 AND topic_id IN ({ph})", list(topic_ids)
    if types:
        sql += f" AND type IN ({','.join('?' * len(types))})"
        args += list(types)
    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        args += list(doc_ids)
    items = [hydrate(i) for i in db.q(sql, tuple(args))]
    now_s = db.iso()
    due = sorted([i for i in items if i["state"] != "new" and (i["due"] or "") <= now_s], key=lambda i: i["due"])
    new = [i for i in items if i["state"] == "new"]
    weak = sorted([i for i in items if i["weak"]], key=lambda i: (-i["lapses"], i["due"] or ""))
    random.shuffle(new)
    if mode == "weak":
        chosen = weak
    elif mode == "new":
        chosen = new
    elif mode == "all":
        chosen = items[:]
        random.shuffle(chosen)
    else:                                                  # due + new, plus a boost of weak items
        base = due + new[:new_limit]
        reserve = max(1, limit // 4) if weak else 0
        base = base[:max(1, limit - reserve)]
        ids = {i["id"] for i in base}
        base += [w for w in weak if w["id"] not in ids][:reserve]
        chosen = base
    chosen = chosen[:limit]
    if interleave and len(chosen) > 2:
        groups = defaultdict(list)
        for i in chosen:
            groups[(i["topic_id"], i["doc_id"] or i["type"])].append(i)
        order = list(groups.values())
        random.shuffle(order)
        mixed = []
        while any(order):
            for g in order:
                if g:
                    mixed.append(g.pop(0))
        chosen = mixed
    for i in chosen:
        i["preview"] = preview(i)
    return chosen
