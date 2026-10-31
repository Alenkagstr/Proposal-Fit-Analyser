"""Adapted from agent_colab.ipynb: reusable parsing and analysis, without Colab execution."""

from __future__ import annotations

import argparse

import csv

import json

import os

import re

import sys

import time

from difflib import SequenceMatcher

from pathlib import Path

try:
    import requests
except ImportError:  # requests нужен только для запросов в сеть
    requests = None

def read_file(path: str) -> str:
    """Возвращает текст файла вместе со всеми гиперссылками из него."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".txt", ".md", ".csv"):
        return p.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        return _read_pdf(p)
    if ext == ".docx":
        return _read_docx(p)
    if ext == ".xlsx":
        return _read_xlsx(p)
    raise ValueError(f"Неподдерживаемый формат: {p.name} (нужен pdf, docx, xlsx или txt)")

def _read_pdf(p: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
        # ссылки-аннотации: в PDF текст «Google Scholar» может вести на URL, которого нет в тексте
        for annot in page.get("/Annots") or []:
            try:
                uri = annot.get_object().get("/A", {}).get("/URI")
                if uri:
                    parts.append(str(uri))
            except Exception:
                pass
    return "\n".join(parts)

def _read_docx(p: Path) -> str:
    import docx
    d = docx.Document(str(p))
    parts = [para.text for para in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    # гиперссылки хранятся отдельно от текста
    for rel in d.part.rels.values():
        if "hyperlink" in rel.reltype:
            parts.append(rel.target_ref)
    return "\n".join(parts)

def _read_xlsx(p: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(p), data_only=True)
    parts = []
    for ws in wb:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    parts.append(str(cell.value))
                if cell.hyperlink is not None and cell.hyperlink.target:
                    parts.append(cell.hyperlink.target)
    return "\n".join(parts)

PROFILE_PATTERNS = {
    "orcid":          r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])",
    "google_scholar": r"scholar\.google\.[a-z.]+/citations\?[^\s]*?user=([\w-]{12})",
    "scopus":         r"scopus\.com/[^\s]*?authorId=(\d+)",
    "researchgate":   r"researchgate\.net/profile/([^\s/?#)\]]+)",
    "web_of_science": r"webofscience\.com/wos/author/(?:record|rid)/([\w-]+)",
    "researcherid":   r"\b([A-Z]{1,3}-\d{4}-\d{4})\b",
    "elibrary":       r"elibrary\.ru/author_(?:profile|items)\.asp\?(?:[^\s]*?)(?:id|authorid)=(\d+)",
}

PROFILE_URLS = {
    "orcid":          "https://orcid.org/{}",
    "google_scholar": "https://scholar.google.com/citations?user={}",
    "scopus":         "https://www.scopus.com/authid/detail.uri?authorId={}",
    "researchgate":   "https://www.researchgate.net/profile/{}",
    "web_of_science": "https://www.webofscience.com/wos/author/record/{}",
    "researcherid":   "https://www.webofscience.com/wos/author/rid/{}",
    "elibrary":       "https://elibrary.ru/author_profile.asp?id={}",
}

def find_profiles(texts: dict[str, str]) -> dict[str, dict]:
    """
    texts: {"анкета": "...", "CV": "..."} — имя источника → текст.
    Возвращает {"orcid": {"id": ..., "url": ..., "sources": ["анкета", "CV"]}, ...}
    """
    found: dict[str, dict] = {}
    for source, text in texts.items():
        for kind, pattern in PROFILE_PATTERNS.items():
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                ident = match.upper() if kind in ("orcid", "researcherid") else match
                if kind == "researcherid" and "webofscience" not in text.lower() \
                        and "researcherid" not in text.lower():
                    continue  # шаблон A-1234-5678 слишком общий без контекста
                entry = found.setdefault(kind, {"id": ident, "url": PROFILE_URLS[kind].format(ident),
                                                "sources": [], "other_ids": []})
                if ident != entry["id"] and ident not in entry["other_ids"]:
                    entry["other_ids"].append(ident)  # два разных ID одного типа — повод проверить
                if source not in entry["sources"]:
                    entry["sources"].append(source)
    return found

URL_PATTERN = r"(?:https?://|www\.)[^\s<>\"'(){}\[\]]+"

LINK_TYPES = [  # (тип, признаки в адресе) — порядок важен, первое совпадение
    ("ИСТИНА",               ["istina.msu.ru", "istina."]),
    ("диссертация",          ["dissercat", "vak.minobrnauki", "dissovet", "diss.", "/dissertation", "disser"]),
    ("файл в облаке",        ["drive.google", "docs.google", "disk.yandex", "yadi.sk", "cloud.mail.ru",
                              "dropbox.com", "onedrive", "1drv.ms"]),
    ("код",                  ["github.com", "gitlab.com", "huggingface.co", "kaggle.com", "bitbucket.org"]),
    ("препринт / статья",    ["arxiv.org", "doi.org", "biorxiv", "medrxiv", "ssrn.com", "semanticscholar", "dblp."]),
    ("соцсеть",              ["linkedin.com", "vk.com", "facebook.com", "t.me", "twitter.com", "x.com",
                              "instagram.com", "youtube.com", "habr.com"]),
    ("сайт вуза / организации", [".edu", "msu.ru", "itmo.ru", "spbu.ru", "hse.ru", "mipt.ru", "bmstu.ru",
                                 "urfu.ru", "nsu.ru", "skoltech.ru", "ras.ru", ".ac.", "univ", "university"]),
]

FETCHABLE_TYPES = {"ИСТИНА", "диссертация", "сайт вуза / организации", "прочее"}

def classify_link(url: str) -> str:
    u = url.lower()
    for kind, marks in LINK_TYPES:
        if any(m in u for m in marks):
            return kind
    return "прочее"

def find_other_links(texts: dict[str, str]) -> list[dict]:
    """Все ссылки, кроме известных научных профилей и почты."""
    known = [re.compile(p, re.I) for k, p in PROFILE_PATTERNS.items() if k not in ("orcid", "researcherid")]
    known.append(re.compile(r"orcid\.org", re.I))
    out: dict[str, dict] = {}
    for source, text in texts.items():
        for url in re.findall(URL_PATTERN, text or "", flags=re.I):
            url = url.rstrip(".,;:!?»”")
            if url.lower().startswith("www."):
                url = "https://" + url
            if any(k.search(url) for k in known) or url.lower().startswith("mailto:"):
                continue
            key = url.rstrip("/").lower()
            e = out.setdefault(key, {"url": url, "type": classify_link(url), "sources": []})
            if source not in e["sources"]:
                e["sources"].append(source)
    return list(out.values())

def page_text(url: str, max_chars: int = 50000) -> tuple[str | None, str | None]:
    """Скачивает страницу и возвращает (текст, ошибка). Только HTML, без файлов."""
    if requests is None:
        return None, "нет requests"
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts, self.skip = [], 0
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript", "svg"):
                self.skip += 1
            elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "td"):
                self.parts.append("\n")
        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript", "svg") and self.skip:
                self.skip -= 1
        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (ProposalFitAnalyser research prototype)"})
        if r.status_code == 404:
            return None, "страница не найдена — ссылка устарела или неверна (вопрос кандидату)"
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype:
            return None, f"не веб-страница ({ctype.split(';')[0] or 'неизвестный тип'})"
        r.encoding = r.apparent_encoding or r.encoding
        parser = _P()
        parser.feed(r.text)
        text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\xa0]+", " ", "".join(parser.parts))).strip()
        if len(text) < 200:
            if re.search(r"не найден|не существует|not found|404|does not exist|no longer available", text, re.I):
                return None, "страница не найдена — ссылка устарела или неверна (вопрос кандидату)"
            return None, "почти пустая страница (возможно, нужен JavaScript или вход)"
        return text[:max_chars], None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

OPENALEX = "https://api.openalex.org"

ORCID_API = "https://pub.orcid.org/v3.0"

def _get(url: str, params: dict | None = None, headers: dict | None = None, tries: int = 3) -> dict | None:
    if requests is None:
        raise RuntimeError("Установите requests: pip install requests")
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 429:  # слишком часто — подождать
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == tries - 1:
                print(f"  ! Ошибка запроса {url}: {e}", file=sys.stderr)
            time.sleep(1 + attempt)
    return None

def _openalex_params(mailto: str | None) -> dict:
    params = {}
    if mailto:
        params["mailto"] = mailto  # «вежливый» режим OpenAlex: быстрее и стабильнее
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    return params

def fetch_openalex(key: str, mailto: str | None = None) -> tuple[dict | None, list[dict]]:
    """
    Автор и все его работы из OpenAlex.
    key: ORCID ("0000-0002-1825-0097") или ID автора в OpenAlex ("A5023888391").
    """
    base = _openalex_params(mailto)
    key = key.rsplit("/", 1)[-1]
    lookup = f"orcid:{key}" if re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", key, re.I) else key
    author = _get(f"{OPENALEX}/authors/{lookup}", params=base)
    if not author:
        return None, []
    author_id = author["id"].rsplit("/", 1)[-1]
    return author, _openalex_works(author_id, base)

def _openalex_works(author_id: str, base: dict) -> list[dict]:
    works, cursor = [], "*"
    while cursor:
        page = _get(f"{OPENALEX}/works",
                    params={**base, "filter": f"author.id:{author_id}", "per-page": 200, "cursor": cursor})
        if not page or not page.get("results"):
            break
        works.extend(page["results"])
        cursor = page.get("meta", {}).get("next_cursor")
    return works

DOI_PATTERN = r"\b(10\.\d{4,9}/[^\s\"<>]+)"

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                      "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))

def _latin(name: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in (name or "").lower())

def same_person(name: str | None, other: str | None) -> bool:
    """Грубая проверка «похоже на того же человека»: совпадает фамилия (с учётом транслитерации)."""
    if not name or not other:
        return False
    a = [t for t in _norm_title(_latin(name)).split() if len(t) > 2]
    b = [t for t in _norm_title(_latin(other)).split() if len(t) > 2]
    return any(SequenceMatcher(None, x, y).ratio() >= 0.8 for x in a for y in b)

def find_dois(text: str) -> list[str]:
    dois = []
    for d in re.findall(DOI_PATTERN, text or ""):
        d = d.rstrip(".,;)]").lower()
        if d not in dois:
            dois.append(d)
    return dois

def find_author_by_dois(cv_text: str, name: str | None, mailto: str | None = None) -> dict | None:
    """
    Берёт DOI статей из CV, находит эти статьи в OpenAlex и смотрит, какой автор встречается в них чаще всего.
    Если указано имя — учитываются только авторы с похожей фамилией.
    """
    base = _openalex_params(mailto)
    dois = find_dois(cv_text)[:30]
    if not dois:
        return None
    counts: dict[str, dict] = {}
    found = 0
    for doi in dois:
        w = _get(f"{OPENALEX}/works/doi:{doi}", params=base)
        if not w:
            continue
        found += 1
        for a in w.get("authorships") or []:
            au = a.get("author") or {}
            if not au.get("id"):
                continue
            if name and not same_person(name, au.get("display_name")):
                continue
            c = counts.setdefault(au["id"], {"id": au["id"], "name": au.get("display_name"),
                                             "orcid": au.get("orcid"), "hits": 0})
            c["hits"] += 1
    if not counts:
        return None
    best = sorted(counts.values(), key=lambda c: -c["hits"])
    top = best[0]
    ambiguous = len(best) > 1 and best[1]["hits"] == top["hits"]
    if top["hits"] < 2 and not name:
        return None  # одна статья без имени — нельзя понять, кто из соавторов кандидат
    if ambiguous:
        return None
    top["dois_in_cv"], top["dois_found"] = len(dois), found
    top["confidence"] = "высокая" if top["hits"] >= 2 else "средняя"
    return top

def search_authors(name: str, cv_text: str = "", mailto: str | None = None, limit: int = 8) -> list[dict]:
    """
    Ищет авторов по имени и для каждого считает, сколько его статей упомянуто в CV.
    Возвращает список кандидатов, лучшие сверху.
    """
    base = _openalex_params(mailto)
    queries = [name] + ([_latin(name)] if _latin(name) != name.lower() else [])
    seen, result = set(), []
    for q in queries:
        page = _get(f"{OPENALEX}/authors", params={**base, "search": q, "per-page": limit})
        for a in (page or {}).get("results", []):
            if a["id"] in seen:
                continue
            seen.add(a["id"])
            overlap = 0
            if cv_text.strip():
                titles = _openalex_works(a["id"].rsplit("/", 1)[-1], base)
                overlap = sum(in_cv(t.get("display_name") or "", cv_text) in ("да", "похоже") for t in titles)
            result.append({
                "id": a["id"].rsplit("/", 1)[-1],
                "name": a.get("display_name"),
                "orcid": a.get("orcid"),
                "works_count": a.get("works_count"),
                "institutions": ", ".join(i.get("display_name", "") for i in (a.get("last_known_institutions") or [])),
                "top_topics": ", ".join(t.get("display_name", "") for t in (a.get("topics") or [])[:3]),
                "статей_из_CV": overlap,
            })
    return sorted(result, key=lambda r: (-r["статей_из_CV"], -(r["works_count"] or 0)))

def fetch_orcid(orcid: str) -> tuple[dict | None, list[dict]]:
    """Имя и список работ из публичного ORCID API."""
    h = {"Accept": "application/json"}
    person = _get(f"{ORCID_API}/{orcid}/person", headers=h)
    works = _get(f"{ORCID_API}/{orcid}/works", headers=h)
    return person, (works or {}).get("group", [])

def _scholar_table(table: list[dict]) -> dict:
    """SerpAPI: [{'citations': {'all': 10, 'since_2021': 5}}, {'h_index': ...}, ...] → плоский словарь."""
    out = {}
    for entry in table or []:
        for key, vals in entry.items():
            k = "citations" if "citation" in key else ("i10_index" if "i10" in key else "h_index")
            vals = vals or {}
            out[k] = vals.get("all")
            recent = [v for kk, v in vals.items() if kk != "all"]
            if recent:
                out[k + "_recent"] = recent[0]
    return out

def fetch_scholar_serpapi(user_id: str, api_key: str) -> tuple[dict | None, list[dict]]:
    base = {"engine": "google_scholar_author", "author_id": user_id, "api_key": api_key, "hl": "en", "num": 100}
    first = _get("https://serpapi.com/search.json", params=base)
    if not first or "error" in first:
        raise RuntimeError((first or {}).get("error", "нет ответа от SerpAPI"))
    articles = list(first.get("articles") or [])
    start = 100
    while len(articles) >= start and start < 1000:  # следующие страницы
        page = _get("https://serpapi.com/search.json", params={**base, "start": start}) or {}
        if not page.get("articles"):
            break
        articles.extend(page["articles"])
        start += 100
    a = first.get("author") or {}
    metrics = {"name": a.get("name"), "affiliation": a.get("affiliations"),
               "interests": [i.get("title") for i in (a.get("interests") or [])],
               **_scholar_table((first.get("cited_by") or {}).get("table"))}
    pubs = [{"title": x.get("title", ""), "year": int(x["year"]) if str(x.get("year", "")).isdigit() else None,
             "venue": x.get("publication"), "cited_by": (x.get("cited_by") or {}).get("value")} for x in articles]
    return metrics, pubs

def fetch_scholar_free(user_id: str, timeout: int = 90) -> tuple[dict | None, list[dict]]:
    from concurrent.futures import ThreadPoolExecutor
    from scholarly import scholarly

    def job():
        author = scholarly.search_author_id(user_id)
        return scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])

    with ThreadPoolExecutor(max_workers=1) as ex:
        a = ex.submit(job).result(timeout=timeout)  # если Google тормозит/блокирует — не ждём вечно
    metrics = {"name": a.get("name"), "affiliation": a.get("affiliation"), "interests": a.get("interests", []),
               "citations": a.get("citedby"), "citations_recent": a.get("citedby5y"),
               "h_index": a.get("hindex"), "h_index_recent": a.get("hindex5y"),
               "i10_index": a.get("i10index"), "i10_index_recent": a.get("i10index5y")}
    pubs = []
    for x in a.get("publications", []):
        bib = x.get("bib", {})
        y = str(bib.get("pub_year", ""))
        pubs.append({"title": bib.get("title", ""), "year": int(y) if y.isdigit() else None,
                     "venue": bib.get("citation"), "cited_by": x.get("num_citations")})
    return metrics, pubs

def fetch_scholar(user_id: str, serpapi_key: str | None = None) -> tuple[dict | None, list[dict], str | None]:
    """Возвращает (метрики, публикации, ошибка)."""
    serpapi_key = serpapi_key or os.environ.get("SERPAPI_KEY")
    try:
        if serpapi_key:
            m, p = fetch_scholar_serpapi(user_id, serpapi_key)
        else:
            m, p = fetch_scholar_free(user_id)
        return m, p, None
    except Exception as e:
        if isinstance(e, ImportError):
            hint = " (проблема установки библиотек: выполните !pip install \"bibtexparser<2\" scholarly и перезапустите сеанс)"
        else:
            hint = "" if serpapi_key else " (возможно, капча Google; можно задать SERPAPI_KEY)"
        return None, [], f"{type(e).__name__}: {e}{hint}"

ELSEVIER = "https://api.elsevier.com/content"

def _scopus_headers(api_key: str, insttoken: str | None) -> dict:
    h = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    if insttoken:
        h["X-ELS-Insttoken"] = insttoken
    return h

def _scopus_get(url: str, headers: dict, params: dict | None = None) -> dict:
    if requests is None:
        raise RuntimeError("Установите requests")
    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code in (401, 403):
        raise PermissionError(f"Scopus отказал в доступе ({r.status_code}): нужен ключ с подпиской организации")
    if r.status_code == 404:
        raise LookupError("автор не найден в Scopus")
    r.raise_for_status()
    return r.json()

def fetch_scopus(author_id: str | None = None, orcid: str | None = None, api_key: str | None = None,
                 insttoken: str | None = None, max_docs: int = 200) -> tuple[dict | None, list[dict], str | None]:
    """Метрики и публикации автора из Scopus по Author ID (или по ORCID). Возвращает (метрики, публикации, ошибка)."""
    api_key = api_key or os.environ.get("SCOPUS_API_KEY")
    insttoken = insttoken or os.environ.get("SCOPUS_INSTTOKEN")
    if not api_key:
        return None, [], "нет SCOPUS_API_KEY"
    h = _scopus_headers(api_key, insttoken)
    try:
        path = f"author_id/{author_id}" if author_id else f"orcid/{orcid}"
        data = _scopus_get(f"{ELSEVIER}/author/{path}", h, {"view": "ENHANCED"})
        resp = data.get("author-retrieval-response")
        a = resp[0] if isinstance(resp, list) else (resp or {})
        core = a.get("coredata") or {}
        sid = (core.get("dc:identifier") or "").replace("AUTHOR_ID:", "") or author_id
        prof = a.get("author-profile") or {}
        pname = prof.get("preferred-name") or {}
        areas = (a.get("subject-areas") or {}).get("subject-area") or []
        if isinstance(areas, dict):
            areas = [areas]
        aff = ((prof.get("affiliation-current") or {}).get("affiliation") or {})
        if isinstance(aff, list):
            aff = aff[0] if aff else {}
        metrics = {
            "scopus_id": sid,
            "name": " ".join(filter(None, [pname.get("given-name"), pname.get("surname")])),
            "affiliation": ((aff.get("ip-doc") or {}).get("afdispname")),
            "documents": core.get("document-count"),
            "citations": core.get("citation-count"),
            "cited_by_docs": core.get("cited-by-count"),
            "h_index": a.get("h-index") or core.get("h-index"),
            "subject_areas": [x.get("$") for x in areas if isinstance(x, dict)],
        }
        pubs, start = [], 0
        while start < max_docs:
            res = _scopus_get(f"{ELSEVIER}/search/scopus", h,
                              {"query": f"AU-ID({sid})", "count": 25, "start": start}).get("search-results", {})
            entries = [e for e in res.get("entry", []) if "error" not in e]
            for e in entries:
                date = e.get("prism:coverDate") or ""
                pubs.append({"title": e.get("dc:title", ""), "year": int(date[:4]) if date[:4].isdigit() else None,
                             "venue": e.get("prism:publicationName"), "doi": _norm_doi(e.get("prism:doi")),
                             "cited_by": int(e["citedby-count"]) if str(e.get("citedby-count", "")).isdigit() else None,
                             "type": e.get("subtypeDescription")})
            total = int(res.get("opensearch:totalResults", 0) or 0)
            start += 25
            if not entries or start >= total:
                break
        return metrics, pubs, None
    except Exception as e:
        return None, [], f"{type(e).__name__}: {e}"

def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.I).lower()

def _norm_title(t: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (t or "").lower()).strip()

def _abstract(inv: dict | None) -> str:
    """OpenAlex хранит аннотацию как {слово: [позиции]} — собираем обратно в текст."""
    if not inv:
        return ""
    words = {}
    for word, positions in inv.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))

def parse_openalex_work(w: dict) -> dict:
    topics = []
    if w.get("primary_topic"):
        pt = w["primary_topic"]
        topics.append(pt.get("display_name"))
        for level in ("subfield", "field"):
            if pt.get(level):
                topics.append(pt[level].get("display_name"))
    keywords = [k.get("display_name") for k in (w.get("keywords") or [])]
    source = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    return {
        "title": w.get("display_name") or w.get("title") or "",
        "year": w.get("publication_year"),
        "doi": _norm_doi(w.get("doi")),
        "type": w.get("type"),
        "venue": source,
        "cited_by": w.get("cited_by_count"),
        "language": w.get("language"),
        "topics": [t for t in topics if t],
        "keywords": [k for k in keywords if k],
        "abstract": _abstract(w.get("abstract_inverted_index")),
        "n_authors": len(w.get("authorships") or []),
        "found_in": ["OpenAlex"],
        "cited_src": {"OpenAlex": w.get("cited_by_count")} if w.get("cited_by_count") is not None else {},
    }

def parse_orcid_group(g: dict) -> dict | None:
    summaries = g.get("work-summary") or []
    if not summaries:
        return None
    s = summaries[0]
    title = (((s.get("title") or {}).get("title")) or {}).get("value", "")
    year = (((s.get("publication-date") or {}).get("year")) or {}).get("value")
    doi = None
    for ext in ((s.get("external-ids") or {}).get("external-id") or []):
        if ext.get("external-id-type") == "doi":
            doi = ext.get("external-id-value")
    return {
        "title": title,
        "year": int(year) if year and str(year).isdigit() else None,
        "doi": _norm_doi(doi),
        "type": s.get("type"),
        "venue": ((s.get("journal-title") or {}) or {}).get("value"),
        "cited_by": None, "language": None, "topics": [], "keywords": [],
        "abstract": "", "n_authors": None,
        "found_in": ["ORCID"], "cited_src": {},
    }

def parse_simple_pub(x: dict, source: str) -> dict:
    """Публикация из Scholar / Scopus → общий вид."""
    return {"title": x.get("title", ""), "year": x.get("year"), "doi": x.get("doi"), "type": x.get("type"),
            "venue": x.get("venue"), "cited_by": x.get("cited_by"), "language": None, "topics": [],
            "keywords": [], "abstract": "", "n_authors": None, "found_in": [source],
            "cited_src": {source: x.get("cited_by")} if x.get("cited_by") is not None else {}}

def merge_works(*lists: list[dict]) -> list[dict]:
    """Склеивает одни и те же статьи из разных источников (по DOI, иначе по названию)."""
    merged: list[dict] = []
    for works in lists:
        for w in works:
            twin = None
            for m in merged:
                if w["doi"] and m["doi"] and w["doi"] == m["doi"]:
                    twin = m
                    break
                if _norm_title(w["title"]) and _norm_title(w["title"]) == _norm_title(m["title"]):
                    twin = m
                    break
            if twin is None:
                merged.append(dict(w))
            else:
                for k, v in w.items():
                    if k == "found_in":
                        twin["found_in"] = sorted(set(twin["found_in"]) | set(v))
                    elif k == "cited_src":
                        twin["cited_src"] = {**v, **twin.get("cited_src", {})}
                    elif not twin.get(k) and v:
                        twin[k] = v
    return merged

def in_cv(title: str, cv_text: str) -> str:
    """Есть ли статья в CV: 'да' / 'похоже' / 'нет' / '' (CV не передан)."""
    if not cv_text.strip():
        return ""
    t, cv = _norm_title(title), _norm_title(cv_text)
    if not t:
        return ""
    if t in cv:
        return "да"
    words = [x for x in t.split() if len(x) > 3]
    if words and sum(x in cv for x in words) / len(words) >= 0.8:
        return "похоже"
    return "нет"

def name_matches(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True  # нечего сравнивать
    return SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio() > 0.6 \
        or _norm_title(a).split()[-1] in _norm_title(b)

EVIDENCE_COLUMNS = ["id", "Кратко (EN)", "Тип", "Источник", "Сила", "Год", "Цитата",
                    "Темы", "Ключевые слова", "Журнал / конференция", "Цитирований",
                    "DOI", "Найдено в", "Указано в CV", "Аннотация"]

def build_profile(candidate_id: str, texts: dict[str, str], orcid: str | None = None,
                  mailto: str | None = None, offline: bool = False,
                  name: str | None = None, openalex_id: str | None = None,
                  use_scholar: bool = True, use_scopus: bool = True,
                  fetch_pages: bool = True) -> tuple[dict, list[dict]]:
    """
    Как ищется автор (по порядку):
      1. ORCID (из файлов или orcid=...)                      — надёжно
      2. openalex_id=... (выбран вручную после search_authors) — надёжно
      3. DOI статей из CV                                      — надёжно, если DOI 2+
      4. если ничего нет — предупреждение; дальше search_authors(name, cv_text) и выбор вручную
    """
    profiles = find_profiles(texts)
    warnings: list[str] = []

    if orcid:
        profiles.setdefault("orcid", {"id": orcid.upper(), "url": PROFILE_URLS["orcid"].format(orcid),
                                      "sources": ["задан вручную"], "other_ids": []})
    for kind, p in profiles.items():
        if p["other_ids"]:
            warnings.append(f"Найдено несколько разных {kind}: {[p['id']] + p['other_ids']} — проверить")

    profile = {
        "candidate": candidate_id,
        "links": profiles,
        "metrics": {},
        "warnings": warnings,
        "not_fetched": [k for k in profiles if k != "orcid"],  # по ним данные не тянем (нет открытого API)
    }

    works: list[dict] = []
    oid = profiles.get("orcid", {}).get("id")
    cv_text = texts.get("CV", "") + "\n" + texts.get("прочее", "")
    ident = {"method": None, "confidence": None}
    oa_author, oa_works = None, []

    if not offline:
        if oid:
            print(f"[{candidate_id}] ORCID {oid}: запрашиваю OpenAlex и ORCID…")
            oa_author, oa_works = fetch_openalex(oid, mailto)
            ident = {"method": "ORCID", "confidence": "высокая"}
        elif openalex_id:
            print(f"[{candidate_id}] OpenAlex ID {openalex_id}: запрашиваю…")
            oa_author, oa_works = fetch_openalex(openalex_id, mailto)
            ident = {"method": "OpenAlex ID (выбран вручную)", "confidence": "высокая"}
        else:
            print(f"[{candidate_id}] ORCID нет — ищу автора по DOI статей из CV…")
            hit = find_author_by_dois(cv_text, name, mailto)
            if hit:
                oa_author, oa_works = fetch_openalex(hit["id"], mailto)
                ident = {"method": f"DOI из CV ({hit['hits']} из {hit['dois_in_cv']} статей)",
                         "confidence": hit["confidence"]}
                warnings.append("Автор найден без ORCID, по DOI из CV — проверьте имя и список статей")
            else:
                n_doi = len(find_dois(cv_text))
                warnings.append(
                    f"ORCID нет, по DOI автора определить не удалось (DOI в CV: {n_doi}). "
                    "Следующий шаг: search_authors(\"Имя Фамилия\", cv_text) и выбор openalex_id вручную. "
                    "Вопрос кандидату: есть ли ORCID?")

        # если OpenAlex знает ORCID автора — используем его и для ORCID API
        if oa_author and not oid and oa_author.get("orcid"):
            oid = oa_author["orcid"].rsplit("/", 1)[-1]
            profiles["orcid"] = {"id": oid, "url": PROFILE_URLS["orcid"].format(oid),
                                 "sources": ["найден через OpenAlex"], "other_ids": []}
        orcid_person, orcid_groups = fetch_orcid(oid) if oid else (None, [])

        if oa_author:
            stats = oa_author.get("summary_stats") or {}
            insts = oa_author.get("last_known_institutions") or []
            profile["metrics"] = {
                "openalex_id": oa_author.get("id"),
                "name_in_openalex": oa_author.get("display_name"),
                "works_count": oa_author.get("works_count"),
                "cited_by_count": oa_author.get("cited_by_count"),
                "h_index": stats.get("h_index"),
                "i10_index": stats.get("i10_index"),
                "institutions": [i.get("display_name") for i in insts],
                "top_topics": [t.get("display_name") for t in (oa_author.get("topics") or [])[:10]],
            }
            if name and not same_person(name, oa_author.get("display_name")):
                warnings.append(f"Имя в OpenAlex ({oa_author.get('display_name')}) не похоже на {name} — проверить")
        elif oid:
            warnings.append("Автор с этим ORCID не найден в OpenAlex (часто бывает у русскоязычных авторов)")

        orcid_name = None
        if orcid_person and orcid_person.get("name"):
            n = orcid_person["name"]
            orcid_name = " ".join(filter(None, [
                ((n.get("given-names") or {}) or {}).get("value"),
                ((n.get("family-name") or {}) or {}).get("value")]))
            profile["metrics"]["name_in_orcid"] = orcid_name
        if oa_author and orcid_name and not name_matches(orcid_name, oa_author.get("display_name")):
            warnings.append(f"Имя в ORCID ({orcid_name}) и OpenAlex ({oa_author.get('display_name')}) "
                            f"не совпадает — возможно, профиль чужой или склеен с однофамильцем")

        # --- Google Scholar и Scopus: метрики каждой базы храним отдельно ---
        by_source = {}
        if oa_author:
            by_source["OpenAlex"] = {k: profile["metrics"].get(k) for k in
                                     ("works_count", "cited_by_count", "h_index", "i10_index")}
        scholar_pubs, scopus_pubs = [], []
        gs = profiles.get("google_scholar")
        if use_scholar and gs:
            print(f"[{candidate_id}] Google Scholar {gs['id']}: запрашиваю…")
            m, scholar_pubs, err = fetch_scholar(gs["id"])
            if m:
                by_source["Google Scholar"] = m
            else:
                warnings.append(f"Google Scholar: данные не получены — {err}")
                profile.setdefault("not_fetched_reason", {})["google_scholar"] = err
        sc = profiles.get("scopus")
        if use_scopus and (sc or oid) and os.environ.get("SCOPUS_API_KEY"):
            print(f"[{candidate_id}] Scopus {'ID ' + sc['id'] if sc else 'по ORCID ' + oid}: запрашиваю…")
            m, scopus_pubs, err = fetch_scopus(author_id=sc["id"] if sc else None, orcid=None if sc else oid)
            if m:
                by_source["Scopus"] = m
                if not sc and m.get("scopus_id"):
                    profiles["scopus"] = {"id": m["scopus_id"], "url": PROFILE_URLS["scopus"].format(m["scopus_id"]),
                                          "sources": ["найден по ORCID"], "other_ids": []}
            else:
                warnings.append(f"Scopus: данные не получены — {err}")
                profile.setdefault("not_fetched_reason", {})["scopus"] = err
        elif sc and use_scopus:
            profile.setdefault("not_fetched_reason", {})["scopus"] = "нет SCOPUS_API_KEY"
        profile["metrics_by_source"] = by_source

        # проверка: тот ли это человек в Scholar / Scopus
        ref_name = name or profile["metrics"].get("name_in_openalex") or profile["metrics"].get("name_in_orcid")
        for src in ("Google Scholar", "Scopus"):
            other = (by_source.get(src) or {}).get("name")
            if ref_name and other and not same_person(ref_name, other):
                warnings.append(f"Имя в {src} ({other}) не похоже на {ref_name} — возможно, ссылка на чужой профиль")

        works = merge_works([parse_openalex_work(w) for w in oa_works],
                            [x for x in (parse_orcid_group(g) for g in orcid_groups) if x],
                            [parse_simple_pub(x, "Scopus") for x in scopus_pubs],
                            [parse_simple_pub(x, "Google Scholar") for x in scholar_pubs])
        if (oa_author or oid or by_source) and not works:
            warnings.append("В профилях нет ни одной публикации")

    # --- прочие ссылки (ИСТИНА, сайты вузов, сканы дипломов, GitHub...) ---
    other = find_other_links(texts)
    for i, link in enumerate(other, 1):
        if link["type"] == "файл в облаке":
            link["note"] = "документ (например, скан диплома) — не скачивается автоматически, проверяет эксперт"
        elif fetch_pages and not offline and link["type"] in FETCHABLE_TYPES:
            text, err = page_text(link["url"])
            if text:
                link["text_file"] = f"{candidate_id}_pages/link_{i:02d}_{link['type'].split()[0]}.txt"
                link["_text"] = text
                link["chars"] = len(text)
            else:
                link["fetch_error"] = err
    profile["other_links"] = other

    profile["identification"] = ident
    fetched = {"orcid"} | {k for k, src in (("google_scholar", "Google Scholar"), ("scopus", "Scopus"))
                           if src in profile.get("metrics_by_source", {})}
    profile["not_fetched"] = [k for k in profiles if k not in fetched]

    # сверка с CV
    rows = []
    for i, w in enumerate(sorted(works, key=lambda x: -(x["year"] or 0)), 1):
        rows.append({
            "id": f"{candidate_id}-P{i}",
            "Кратко (EN)": w["title"],
            "Тип": "публикация",
            "Источник": "научный профиль",
            "Сила": "публикация",
            "Год": w["year"] or "",
            "Цитата": w["title"],
            "Темы": "; ".join(w["topics"]),
            "Ключевые слова": "; ".join(w["keywords"]),
            "Журнал / конференция": w["venue"] or "",
            # цитирования в разных базах разные — показываем по каждой отдельно
            "Цитирований": "; ".join(f"{k}: {v}" for k, v in sorted(w.get("cited_src", {}).items())),
            "DOI": w["doi"] or "",
            "Найдено в": ", ".join(w["found_in"]),
            "Указано в CV": in_cv(w["title"], cv_text),
            "Аннотация": w["abstract"],
        })

    if cv_text.strip() and rows:
        missing = sum(r["Указано в CV"] == "нет" for r in rows)
        profile["cv_check"] = {"in_profiles": len(rows), "not_in_cv": missing}
    return profile, rows

def save(profile: dict, rows: list[dict], out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cid = profile["candidate"]
    for link in profile.get("other_links", []):  # тексты страниц — отдельными файлами, для шага с LLM
        text = link.pop("_text", None)
        if text and link.get("text_file"):
            f = out / link["text_file"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"URL: {link['url']}\n\n{text}", encoding="utf-8")
    (out / f"{cid}_profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out / f"{cid}_evidence.csv", "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig — для Excel
        w = csv.DictWriter(f, fieldnames=EVIDENCE_COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"[{cid}] Сохранено: {out / (cid + '_profile.json')}, {out / (cid + '_evidence.csv')} "
          f"({len(rows)} публикаций)")
    for warn in profile["warnings"]:
        print(f"  ⚠ {warn}")

import json

import re

import time

from openai import OpenAI

PROVIDERS = {
    "neuraldeep": "https://api.neuraldeep.tech/v1",
    "llm7": "https://api.llm7.io/v1",
    "mistral": "https://api.mistral.ai/v1",
}

MAX_INPUT_CHARS = 60000

class LLM:
    def __init__(self, api_key: str, model: str, base_url: str = PROVIDERS["neuraldeep"],
                 temperature: float = 0.0, max_tokens: int = 8000):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens
        self.log: list[dict] = []  # сколько токенов ушло на каждый шаг

    def json(self, system: str, user: str, schema: dict, name: str, tries: int = 3) -> dict:
        """Запрос с ответом строго по JSON-схеме. Если провайдер не поддерживает схему — просим JSON текстом."""
        modes = [
            {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
            {"type": "json_object"},
            None,
        ]
        last_err = None
        for mode in modes:
            for attempt in range(tries):
                try:
                    kwargs = dict(model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
                                  messages=[{"role": "system", "content": system},
                                            {"role": "user", "content": user}])
                    if mode:
                        kwargs["response_format"] = mode
                    if mode is None or mode["type"] == "json_object":
                        kwargs["messages"][0]["content"] += (
                            "\n\nОтвет — только JSON по этой схеме, без пояснений и без ```:\n"
                            + json.dumps(schema, ensure_ascii=False))
                    r = self.client.chat.completions.create(**kwargs)
                    u = getattr(r, "usage", None)
                    self.log.append({"step": name, "model": self.model,
                                     "prompt_tokens": getattr(u, "prompt_tokens", None),
                                     "completion_tokens": getattr(u, "completion_tokens", None)})
                    return _parse_json(r.choices[0].message.content or "")
                except Exception as e:
                    last_err = e
                    msg = str(e).lower()
                    if mode and ("response_format" in msg or "json_schema" in msg or "guided" in msg):
                        break  # провайдер не умеет этот режим — пробуем следующий
                    if "429" in msg or "rate" in msg:
                        time.sleep(5 * (attempt + 1))
                    else:
                        time.sleep(1 + attempt)
        raise RuntimeError(f"LLM не вернула корректный JSON ({name}): {last_err}")

def _parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # рассуждения reasoning-моделей
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise

def chunks(text: str, limit: int = MAX_INPUT_CHARS) -> list[str]:
    """Режет длинный текст по абзацам на куски не длиннее limit символов."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) + 2 > limit and cur:
            parts.append(cur)
            cur = ""
        while len(para) > limit:  # очень длинный абзац
            parts.append(para[:limit])
            para = para[limit:]
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur)
    return parts

REQ_TYPES = ["цель", "задача", "метод", "компетенция", "ресурс", "срок-этап", "результат"]

REQ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["language", "requirements", "missing", "self_claims"],
    "properties": {
        "language": {"type": "string", "description": "язык Proposal: RU, EN или RU+EN"},
        "requirements": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["summary_en", "type", "importance", "stated", "quote", "section"],
            "properties": {
                "summary_en": {"type": "string"},
                "type": {"type": "string", "enum": REQ_TYPES},
                "importance": {"type": "string", "enum": ["ключевое", "желательное"]},
                "stated": {"type": "string", "enum": ["прямо", "выведено"]},
                "quote": {"type": "string"},
                "section": {"type": "string"},
            }}},
        "missing": {"type": "array", "items": {"type": "string", "enum": ["срок-этап", "ресурс", "результат", "метод"]}},
        "self_claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["summary_en", "quote", "section"],
            "properties": {"summary_en": {"type": "string"}, "quote": {"type": "string"},
                           "section": {"type": "string"}}}},
    },
}

REQ_SYSTEM = """Ты — аналитик, который готовит Research Proposal к экспертной оценке.
Разложи Proposal на отдельные требования проекта к исполнителю. Proposal может быть на русском или английском.

Типы требований:
- цель, задача — что проект обещает сделать;
- метод — методы и подходы, которые нужно применить;
- компетенция — что исполнитель должен уметь. Отдельного раздела с компетенциями обычно НЕТ:
  выводи их из задач и методов (например, «обучение GNN на больших графах» → опыт GNN, опыт работы с большими данными, GPU);
- ресурс — оборудование, данные, вычисления, команда, бюджет;
- срок-этап — этапы и сроки;
- результат — ожидаемые результаты (статьи, ПО, датасеты, курсы).

Правила:
- одно требование = одна проверяемая вещь; не склеивай несколько методов в одно требование;
- summary_en — коротко по-английски (до 15 слов), даже если Proposal на русском;
- quote — дословная цитата из Proposal на языке оригинала (до 300 символов), из которой следует требование;
- stated = «прямо», если требование написано явно; «выведено», если ты вывел его сам (тогда quote — откуда вывел);
- importance = «ключевое», если без этого проект не выполнить;
- section — название раздела Proposal, где найдено;
- разделы-контекст (Abstract, Введение, State of the art, Новизна, список литературы) не превращай в требования,
  но методы, упомянутые там как планируемые, — превращай;
- missing — какие из типов «срок-этап», «ресурс», «результат», «метод» в Proposal ВООБЩЕ не описаны;
- self_claims — утверждения кандидата о СЕБЕ внутри Proposal (Previous Experience, PI's competitive profile,
  Personal statement, Publications, Collaborations): что он уже делал, умеет, опубликовал.
Ничего не придумывай: только то, что есть в тексте или прямо из него следует."""

def extract_requirements(llm: LLM, proposal_text: str) -> dict:
    result = {"language": "", "requirements": [], "missing": None, "self_claims": []}
    parts = chunks(proposal_text)
    for i, part in enumerate(parts, 1):
        note = f"(часть {i} из {len(parts)} — раздел missing заполняй только по этой части)\n\n" if len(parts) > 1 else ""
        r = llm.json(REQ_SYSTEM, note + "Research Proposal:\n\n" + part, REQ_SCHEMA, "requirements")
        result["language"] = result["language"] or r.get("language", "")
        result["requirements"] += r.get("requirements", [])
        result["self_claims"] += r.get("self_claims", [])
        miss = set(r.get("missing", []))
        result["missing"] = miss if result["missing"] is None else (result["missing"] & miss)  # нет ни в одной части
    # убираем дубли и нумеруем
    seen, reqs = set(), []
    for q in result["requirements"]:
        key = (q["type"], q["summary_en"].strip().lower())
        if key not in seen:
            seen.add(key)
            reqs.append(q)
    for i, q in enumerate(reqs, 1):
        q["id"] = f"R{i}"
    result["requirements"] = reqs
    result["missing"] = sorted(result["missing"] or [])
    return result

EV_TYPES = ["метод", "технология", "область", "проект", "публикация", "образование", "степень",
            "должность", "грант-награда", "преподавание"]

EV_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["language", "evidence", "claimed_metrics"],
    "properties": {
        "language": {"type": "string"},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["summary_en", "type", "strength", "year", "quote"],
            "properties": {
                "summary_en": {"type": "string"},
                "type": {"type": "string", "enum": EV_TYPES},
                "strength": {"type": "string", "enum": ["упоминание", "проект", "публикация"]},
                "year": {"type": "string"},
                "quote": {"type": "string"},
            }}},
        "claimed_metrics": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["metric", "value", "database", "quote"],
            "properties": {"metric": {"type": "string"}, "value": {"type": "string"},
                           "database": {"type": "string"}, "quote": {"type": "string"}}}},
    },
}

EV_SYSTEM = """Ты — аналитик, который готовит материалы кандидата к экспертной оценке.
Извлеки из документа кандидата (CV или анкета; русский или английский) всё, что подтверждает его опыт.

Правила:
- одна запись = один навык / метод / проект / публикация / факт об образовании или работе;
- summary_en — коротко по-английски (до 15 слов). Для проекта или публикации укажи, какие методы и в какой области;
- strength:
  «упоминание» — навык просто перечислен (например, в списке Skills);
  «проект» — есть описание работы, где навык применялся (место работы, проект, диплом);
  «публикация» — есть статья / доклад / патент;
- year — год или диапазон («2021–2023»), пустая строка, если не указан;
- quote — дословная цитата из документа на языке оригинала (до 300 символов);
- публикацию записывай целиком одной записью (название, где опубликовано), а методы из неё — отдельными записями
  со strength «публикация», если они явно названы в названии;
- claimed_metrics — метрики, которые кандидат сам указал (h-index, цитирования, число статей), с базой (Scopus, WoS, Google Scholar, РИНЦ), если она названа;
- не извлекай персональные данные: телефон, почту, адрес, дату рождения;
Ничего не придумывай: только то, что есть в тексте."""

def extract_evidence(llm: LLM, text: str, source: str) -> dict:
    out = {"language": "", "evidence": [], "claimed_metrics": []}
    for part in chunks(text):
        r = llm.json(EV_SYSTEM, f"Документ кандидата ({source}):\n\n" + part, EV_SCHEMA, f"evidence_{source}")
        out["language"] = out["language"] or r.get("language", "")
        for e in r.get("evidence", []):
            e["source"] = source
            out["evidence"].append(e)
        out["claimed_metrics"] += [{**m, "source": source} for m in r.get("claimed_metrics", [])]
    return out

def build_evidence_table(cv: dict | None, anketa: dict | None, self_claims: list[dict],
                         profile_rows: list[dict] | None) -> list[dict]:
    """Все доказательства в одну таблицу с id E1, E2, …"""
    rows = []
    for block in (cv, anketa):
        for e in (block or {}).get("evidence", []):
            rows.append({"summary_en": e["summary_en"], "type": e["type"], "source": e["source"],
                         "strength": e["strength"], "year": e.get("year", ""), "quote": e["quote"]})
    for c in self_claims or []:
        rows.append({"summary_en": c["summary_en"], "type": "проект", "source": "Proposal (самоописание)",
                     "strength": "упоминание", "year": "", "quote": c["quote"]})
    for p in profile_rows or []:  # публикации из OpenAlex / ORCID / Scholar / Scopus
        topics = p.get("Темы") or ""
        rows.append({"summary_en": p.get("Кратко (EN)", "") + (f" [topics: {topics}]" if topics else ""),
                     "type": "публикация", "source": "научный профиль (" + str(p.get("Найдено в", "")) + ")",
                     "strength": "публикация", "year": str(p.get("Год", "") or ""), "quote": p.get("Цитата", "")})
    for i, r in enumerate(rows, 1):
        r["id"] = f"E{i}"
    return rows

STATUSES = ["подтверждено", "частично подтверждено", "не найдено в материалах", "требует экспертной проверки"]

MATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches"],
    "properties": {"matches": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["req_id", "status", "evidence_ids", "rationale", "question"],
        "properties": {
            "req_id": {"type": "string"},
            "status": {"type": "string", "enum": STATUSES},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
            "question": {"type": "string"},
        }}}},
}

MATCH_SYSTEM = """Ты — эксперт, который проверяет, подтверждён ли опыт кандидата для каждого требования проекта.
Тебе даны требования (R…) и доказательства из материалов кандидата (E…). Тексты могут быть на разных языках — сравнивай по смыслу.

Для КАЖДОГО требования выбери статус строго по правилам:
- «подтверждено» — есть доказательство силы «проект» или «публикация» именно по этому требованию (тот же метод / область / технология);
- «частично подтверждено» — есть только «упоминание» навыка, ИЛИ похожий, но не тот же метод, ИЛИ подтверждение только из «Proposal (самоописание)»;
- «не найдено в материалах» — релевантных доказательств нет;
- «требует экспертной проверки» — доказательства есть, но без специалиста в области нельзя понять, подходят ли они
  (например, требование очень узкое или специфичное для области).
Для требований типа «цель», «задача», «результат», «срок-этап», «ресурс» оценивай, есть ли у кандидата опыт,
нужный для их выполнения (похожие задачи, результаты такого типа раньше, доступ к ресурсам).

Поля:
- evidence_ids — id доказательств, на которые опираешься (пусто, если «не найдено»);
- rationale — 1–2 предложения по-русски: почему такой статус;
- question — уточняющий вопрос кандидату по-русски, если статус не «подтверждено»; иначе пустая строка.
Не придумывай доказательства, которых нет в списке."""

def _compact_reqs(reqs: list[dict]) -> str:
    return "\n".join(f"{r['id']} [{r['type']}, {r['importance']}] {r['summary_en']} | цитата: {r['quote'][:200]}"
                     for r in reqs)

def _compact_ev(ev: list[dict]) -> str:
    return "\n".join(f"{e['id']} [{e['type']}, сила: {e['strength']}, источник: {e['source']}"
                     f"{', ' + e['year'] if e.get('year') else ''}] {e['summary_en']} | {e['quote'][:150]}"
                     for e in ev)

def match(llm: LLM, reqs: list[dict], evidence: list[dict], batch: int = 12) -> list[dict]:
    ev_text = _compact_ev(evidence)
    if len(ev_text) > MAX_INPUT_CHARS:  # слишком много доказательств — урезаем публикации из профилей
        keep = [e for e in evidence if not e["source"].startswith("научный профиль")]
        pubs = [e for e in evidence if e["source"].startswith("научный профиль")]
        while pubs and len(_compact_ev(keep + pubs)) > MAX_INPUT_CHARS:
            pubs = pubs[:-5]
        ev_text = _compact_ev(keep + pubs)
    results = []
    for i in range(0, len(reqs), batch):
        part = reqs[i:i + batch]
        user = f"ТРЕБОВАНИЯ:\n{_compact_reqs(part)}\n\nДОКАЗАТЕЛЬСТВА:\n{ev_text}"
        r = llm.json(MATCH_SYSTEM, user, MATCH_SCHEMA, "matching")
        results += r.get("matches", [])
    return check_matches(results, reqs, evidence)

def check_matches(matches: list[dict], reqs: list[dict], evidence: list[dict]) -> list[dict]:
    """Проверки кодом: модель не может поставить статус, который противоречит правилам."""
    ev = {e["id"]: e for e in evidence}
    by_req = {m["req_id"]: m for m in matches}
    out = []
    for r in reqs:
        m = dict(by_req.get(r["id"]) or {"req_id": r["id"], "status": "требует экспертной проверки",
                                          "evidence_ids": [], "rationale": "Модель не вернула оценку по требованию.",
                                          "question": ""})
        m["evidence_ids"] = [x for x in m.get("evidence_ids", []) if x in ev]  # убираем несуществующие id
        notes = []
        used = [ev[x] for x in m["evidence_ids"]]
        def downgrade(status, why):
            m["status"] = status
            notes.append(why)

        if m["status"] == "подтверждено":
            if not used:
                downgrade("требует экспертной проверки", "нет ссылок на доказательства")
            elif all(e["strength"] == "упоминание" for e in used):
                downgrade("частично подтверждено", "только упоминания навыка")
            elif all(e["source"] == "Proposal (самоописание)" for e in used):
                downgrade("частично подтверждено", "только самоописание в Proposal")
        if m["status"] == "не найдено в материалах" and used:
            downgrade("требует экспертной проверки", "модель указала доказательства, но статус «не найдено»")
        if m["status"] != "подтверждено" and not m.get("question"):
            m["question"] = f"Есть ли у вас опыт по пункту: «{r['summary_en']}»? Приведите пример работы или публикации."
        m["auto_check"] = "; ".join(notes)
        m.update({"requirement": r["summary_en"], "type": r["type"], "importance": r["importance"],
                  "stated": r["stated"], "quote": r["quote"]})
        out.append(m)
    return out

RISK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["risks", "questions", "summary"],
    "properties": {
        "risks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["risk", "level", "basis"],
            "properties": {"risk": {"type": "string"},
                           "level": {"type": "string", "enum": ["высокий", "средний", "низкий"]},
                           "basis": {"type": "string"}}}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}

RISK_SYSTEM = """Ты готовишь краткую аналитическую справку для эксперта, который оценивает заявку на исследовательский проект.
Тебе даны: матрица соответствия требований и доказательств, разделы, которых нет в Proposal, предупреждения
по научным профилям и метрики. Пиши по-русски, деловым языком.

- risks — риски выполнимости проекта этим кандидатом: ключевые требования без подтверждения, отсутствие сроков / ресурсов,
  расхождения между заявленным и найденным в базах, слишком широкий объём работ и т.п. basis — на чём основан риск (id требований или факт);
- questions — 5–10 самых важных уточняющих вопросов кандидату, без повторов, от важных к менее важным;
- summary — 5–8 предложений: что предлагает проект, насколько опыт кандидата ему соответствует, главные сильные стороны,
  главные пробелы, что проверить эксперту в первую очередь. Не выноси решение «принять / отклонить» — это делает эксперт.
Опирайся только на переданные данные."""

def risks_and_summary(llm: LLM, matrix: list[dict], req_info: dict, profile: dict | None,
                      claimed_metrics: list[dict]) -> dict:
    counts = {s: sum(m["status"] == s for m in matrix) for s in STATUSES}
    lines = [f"{m['req_id']} [{m['type']}, {m['importance']}] {m['requirement']} → {m['status']}"
             f" ({m['rationale']}){' | вопрос: ' + m['question'] if m.get('question') else ''}" for m in matrix]
    prof = profile or {}
    user = (f"СТАТИСТИКА СТАТУСОВ: {json.dumps(counts, ensure_ascii=False)}\n\n"
            f"МАТРИЦА:\n" + "\n".join(lines) + "\n\n"
            f"В PROPOSAL НЕ ОПИСАНО: {', '.join(req_info.get('missing', [])) or 'всё основное описано'}\n\n"
            f"МЕТРИКИ ИЗ БАЗ: {json.dumps(prof.get('metrics_by_source', {}), ensure_ascii=False)}\n"
            f"МЕТРИКИ, ЗАЯВЛЕННЫЕ КАНДИДАТОМ: {json.dumps(claimed_metrics, ensure_ascii=False)}\n"
            f"ПРЕДУПРЕЖДЕНИЯ ПО ПРОФИЛЯМ: {json.dumps(prof.get('warnings', []), ensure_ascii=False)}\n")
    return llm.json(RISK_SYSTEM, user[:MAX_INPUT_CHARS], RISK_SCHEMA, "risks_summary")

def make_report(candidate_id: str, req_info: dict, matrix: list[dict], evidence: list[dict],
                rs: dict, profile: dict | None, claimed_metrics: list[dict]) -> str:
    """Справка в Markdown (собирается кодом из готовых частей)."""
    ev = {e["id"]: e for e in evidence}
    counts = {s: sum(m["status"] == s for m in matrix) for s in STATUSES}
    key = [m for m in matrix if m["importance"] == "ключевое"]
    key_ok = sum(m["status"] == "подтверждено" for m in key)
    prof = profile or {}
    md = [f"# Аналитическая справка: кандидат {candidate_id}", "",
          "_Сформировано автоматически. Решение принимает эксперт._", "",
          "## Кратко", "", rs.get("summary", ""), "",
          "## Соответствие требованиям", "",
          f"Всего требований: {len(matrix)}, из них ключевых: {len(key)} (подтверждено {key_ok}).", ""]
    md += [f"- {s}: {c}" for s, c in counts.items()]
    if req_info.get("missing"):
        md += ["", f"**В Proposal не описано:** {', '.join(req_info['missing'])}."]
    md += ["", "| ID | Требование | Тип | Важность | Статус | Доказательства | Комментарий |", "|---|---|---|---|---|---|---|"]
    order = {s: i for i, s in enumerate(["не найдено в материалах", "требует экспертной проверки",
                                         "частично подтверждено", "подтверждено"])}
    for m in sorted(matrix, key=lambda x: (x["importance"] != "ключевое", order[x["status"]])):
        evs = "; ".join(f"{x}: {ev[x]['summary_en'][:60]} ({ev[x]['source']})" for x in m["evidence_ids"][:3])
        comment = m["rationale"] + (f" _[проверка: {m['auto_check']}]_" if m.get("auto_check") else "")
        md.append(f"| {m['req_id']} | {m['requirement']} | {m['type']} | {m['importance']} | **{m['status']}** "
                  f"| {evs.replace('|', '/')} | {comment.replace('|', '/')} |")
    md += ["", "## Риски", ""]
    md += [f"- **{r['level']}:** {r['risk']} _(основание: {r['basis']})_" for r in rs.get("risks", [])] or ["- не выявлено"]
    md += ["", "## Уточняющие вопросы кандидату", ""]
    md += [f"{i}. {q}" for i, q in enumerate(rs.get("questions", []), 1)]
    md += ["", "## Наукометрия", ""]
    mbs = prof.get("metrics_by_source", {})
    if mbs:
        md += ["| База | Публикаций | Цитирований | h-index |", "|---|---|---|---|"]
        for src, m in mbs.items():
            md.append(f"| {src} | {m.get('works_count') or m.get('documents') or '—'} "
                      f"| {m.get('cited_by_count') or m.get('citations') or '—'} | {m.get('h_index') or '—'} |")
    else:
        md.append("Данные из баз не получены.")
    if claimed_metrics:
        md += ["", "Заявлено кандидатом (не проверено): " +
               "; ".join(f"{c['metric']} {c['value']} ({c['database'] or 'база не указана'})" for c in claimed_metrics)]
    links = prof.get("links", {})
    if links:
        md += ["", "Профили: " + ", ".join(f"[{k}]({v['url']})" for k, v in links.items())]
    if prof.get("warnings"):
        md += ["", "**Предупреждения:**"] + [f"- {w}" for w in prof["warnings"]]
    other = prof.get("other_links", [])
    if other:
        md += ["", "Прочие ссылки: " + "; ".join(
            f"{o['type']}: {o['url']}" + (f" ({o['fetch_error']})" if o.get("fetch_error") else "") for o in other)]
    return "\n".join(md)
