"""Find real literature through open databases (never from the AI's memory):
PubMed (articles, mostly English), OpenAlex (articles/books in de/en/fr/ar and more), Open Library (books)."""
import re
from urllib.parse import quote_plus

import requests

from . import rag

UA = {"User-Agent": "StudyBench/1.0 (local study tool)"}
LANGS = {"de": ("German", "ger"), "en": ("English", "eng"), "fr": ("French", "fre")}
TIMEOUT = 15


class SourceError(Exception):
    pass


def _get(url, params):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise SourceError(f"{url.split('/')[2]}: {e.__class__.__name__}") from e


def _authors(names, limit=3):
    names = [n for n in names if n]
    return ", ".join(names[:limit]) + (" et al." if len(names) > limit else "")


def pubmed(query, n=6, evidence=False):
    term = query + (" AND (review[pt] OR guideline[pt] OR meta-analysis[pt] OR systematic[sb])" if evidence else "")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    ids = _get(base + "esearch.fcgi", {"db": "pubmed", "term": term, "retmax": n, "retmode": "json", "sort": "relevance"}) \
        .get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    res = _get(base + "esummary.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}).get("result", {})
    out = []
    for uid in res.get("uids", []):
        r = res[uid]
        types = [t.lower() for t in r.get("pubtype", [])]
        kind = "guideline" if any("guideline" in t for t in types) else "review" if any("review" in t or "meta" in t for t in types) else "article"
        year = (re.search(r"\d{4}", r.get("pubdate", "")) or [""])[0]
        out.append({"title": (r.get("title") or "").rstrip("."), "authors": _authors([a.get("name") for a in r.get("authors", [])]),
                    "year": year, "venue": r.get("source", ""), "lang": "en", "kind": kind,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/", "source": "PubMed", "cited": None})
    return out


def openalex(query, lang, n=6, kinds=("article", "review", "book")):
    types = []
    if "article" in kinds:
        types.append("article")
    if "review" in kinds:
        types.append("review")
    if "book" in kinds:
        types += ["book", "book-chapter"]
    flt = f"language:{lang}" + (f",type:{'|'.join(types)}" if types else "")
    data = _get("https://api.openalex.org/works", {"search": query, "filter": flt, "per-page": n,
                                                   "select": "id,title,publication_year,doi,authorships,primary_location,cited_by_count,language,type,open_access"})
    out = []
    for w in data.get("results", []):
        if not w.get("title"):
            continue
        loc = w.get("primary_location") or {}
        url = w.get("doi") or (w.get("open_access") or {}).get("oa_url") or loc.get("landing_page_url") or w.get("id")
        t = w.get("type") or "article"
        out.append({"title": w["title"], "authors": _authors([(a.get("author") or {}).get("display_name") for a in w.get("authorships", [])]),
                    "year": str(w.get("publication_year") or ""), "venue": ((loc.get("source") or {}).get("display_name") or ""),
                    "lang": w.get("language") or lang, "kind": "book" if t.startswith("book") else "review" if t == "review" else "article",
                    "url": url, "source": "OpenAlex", "cited": w.get("cited_by_count")})
    return out


def openlibrary(query, lang, n=5):
    code = LANGS.get(lang, ("", "eng"))[1]
    data = _get("https://openlibrary.org/search.json", {"q": query, "language": code, "limit": n,
                                                        "fields": "key,title,author_name,first_publish_year,publisher"})
    return [{"title": d.get("title", ""), "authors": _authors(d.get("author_name", [])), "year": str(d.get("first_publish_year") or ""),
             "venue": ", ".join((d.get("publisher") or [])[:1]), "lang": lang, "kind": "book",
             "url": "https://openlibrary.org" + d["key"], "source": "Open Library", "cited": None}
            for d in data.get("docs", []) if d.get("title") and d.get("key")]


def link_outs(query):
    """Search pages for guidelines and other sources that have no open API (links only, nothing is fetched)."""
    q = quote_plus(query)
    return [("Google Scholar", f"https://scholar.google.com/scholar?q={q}"),
            ("PubMed", f"https://pubmed.ncbi.nlm.nih.gov/?term={q}"),
            ("AWMF guidelines (DE)", f"https://duckduckgo.com/?q=site%3Aregister.awmf.org+{q}"),
            ("NICE guidance (UK)", f"https://duckduckgo.com/?q=site%3Anice.org.uk+{q}"),
            ("Cochrane Library", f"https://duckduckgo.com/?q=site%3Acochranelibrary.com+{q}")]


def relevance(query, r):
    """Share of the query's key terms that appear in the result's title/venue (0..1). Keeps results on topic."""
    q = set(rag.tokens(query))
    if not q:
        return 1.0
    have = set(rag.tokens(f"{r['title']} {r.get('venue', '')}"))
    return len(q & have) / len(q)


def search(queries, langs, kinds, job=None, per_query=8, min_rel=0.5):
    """queries: {lang: [phrases]}. Returns (items, errors)."""
    items, errors, seen = [], [], set()
    plan = []
    for lang in langs:
        for q in (queries.get(lang) or [])[:3]:
            if lang == "en" and ("article" in kinds or "review" in kinds):
                plan.append(("pubmed", q, lang))
            if "article" in kinds or "review" in kinds or "book" in kinds:
                plan.append(("openalex", q, lang))
            if "book" in kinds:
                plan.append(("olib", q, lang))
    for i, (src, q, lang) in enumerate(plan):
        if job:
            job.check()
            job.set(i / max(1, len(plan)), f"{src}: {q[:40]}")
        try:
            if src == "pubmed":
                res = pubmed(q, per_query, evidence="review" in kinds and "article" not in kinds)
            elif src == "openalex":
                res = openalex(q, lang, per_query, kinds)
            else:
                res = openlibrary(q, lang, per_query)
        except SourceError as e:
            if str(e) not in errors:
                errors.append(str(e))
            continue
        for r in res:
            r["rel"] = relevance(q, r)
            if r["rel"] < min_rel:                       # off-topic hit: drop it
                continue
            key = (r["url"] or r["title"]).lower()
            tk = re.sub(r"\W+", "", r["title"].lower())[:60]
            if key in seen or tk in seen:
                continue
            seen.update({key, tk})
            r["why"] = q
            r["query_lang"] = lang
            items.append(r)
    bonus = {"guideline": 0.25, "review": 0.15, "article": 0.0, "book": 0.1}
    items.sort(key=lambda r: -(r["rel"] + bonus.get(r["kind"], 0) + min(0.2, (r.get("cited") or 0) / 2000)))
    return items, errors
