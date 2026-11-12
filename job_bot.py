"""Bot de ofertas: consulta las APIs públicas de los ATS, filtra y avisa por Telegram.

Uso:
  python job_bot.py            # ejecución normal
  python job_bot.py --dry-run  # no envía a Telegram ni guarda estado, solo imprime
  python job_bot.py --dry-run --only "oreal"   # prueba solo una o varias empresas (separadas por coma)
"""
import csv
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).parent
SEEN_FILE = ROOT / "seen.json"
JOBS_CSV = ROOT / "jobs.csv"
HEADERS = {"User-Agent": "personal-job-alert-bot/1.0", "Accept": "application/json"}
TIMEOUT = 20
PAUSE = 1.5  # segundos entre empresas, para ser educados con los servidores


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------- Conectores: cada uno devuelve una lista de dicts {id, title, location, url} ----------

def fetch_lever(c):
    r = requests.get(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json", headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 404:  # algunas cuentas europeas viven en la instancia EU de Lever
        r = requests.get(f"https://api.eu.lever.co/v0/postings/{c['slug']}?mode=json", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"id": j["id"], "title": j.get("text", ""),
             "location": (j.get("categories") or {}).get("location", "") or "",
             "url": j.get("hostedUrl", ""), "posted": j.get("createdAt")} for j in r.json()]


def fetch_ashby(c):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc += " remote"
        out.append({"id": j["id"], "title": j.get("title", ""), "location": loc, "url": j.get("jobUrl", ""),
                    "posted": j.get("publishedAt") or j.get("publishedDate")})
    return out


def fetch_greenhouse(c):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"id": str(j["id"]), "title": j.get("title", ""),
             "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", ""), "posted": j.get("first_published") or j.get("updated_at")}
            for j in r.json().get("jobs", [])]


def fetch_smartrecruiters(c):
    out, offset = [], 0
    while True:
        r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings",
                         params={"limit": 100, "offset": offset, **c.get("params", {})},  # ej: {"country": "fr"}
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append({"id": j["id"], "title": j.get("name", ""),
                        "location": f"{loc.get('city', '')} {'France' if loc.get('country') == 'fr' else loc.get('country', '')} {'remote' if loc.get('remote') else ''}",
                        "url": f"https://jobs.smartrecruiters.com/{c['slug']}/{j['id']}",
                        "posted": j.get("releasedDate")})
        offset += 100
        if offset >= data.get("totalFound", 0) or offset >= c.get("max_results", 3000):
            return out
        time.sleep(PAUSE)


def fetch_workday(c, terms):
    """API no oficial: la que usa la propia web de Workday. Puede cambiar sin aviso."""
    url = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    out, seen = [], set()
    for term in c.get("search_terms", terms):
        total = None
        for offset in range(0, c.get("max_results", 100), 20):  # hasta 5 páginas por término
            body = {"appliedFacets": c.get("facets", {}), "limit": 20, "offset": offset, "searchText": term}
            r = requests.post(url, json=body, headers={**HEADERS, "Content-Type": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            postings = data.get("jobPostings", [])
            if total is None:  # Workday solo da el total en la primera página
                total = data.get("total", 0)
            for j in postings:
                path = j.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                out.append({"id": path, "title": j.get("title", ""), "location": j.get("locationsText", ""),
                            "url": f"https://{c['host']}/{c['site']}{path}", "posted": j.get("postedOn")})
            time.sleep(PAUSE)
            if len(postings) < 20 or offset + 20 >= total:
                break
    return out


def _get_path(obj, path):
    """Lee un campo anidado con puntos: "location.city" o "data.jobs"."""
    for part in path.split(".") if path else []:
        if isinstance(obj, list):
            obj = obj[int(part)] if part.isdigit() and int(part) < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return obj


def fetch_custom_json(c):
    """Portales propios que cargan las ofertas desde un JSON (se ve en DevTools > Red > Fetch/XHR).

    En companies.json:
      "url": endpoint, "method": "GET" o "POST", "body": {...} (opcional, para POST),
      "list_path": dónde está la lista de ofertas ("" si la respuesta ya es una lista),
      "fields": {"id": ..., "title": ..., "location": ..., "url": ...},
      "url_prefix": texto que se pone delante del campo url si viene relativo (opcional).
    """
    method = c.get("method", "GET").upper()
    h = {**HEADERS, **c.get("headers", {})}
    pag = c.get("pagination")  # ej: {"param": "resultsOffset", "step": 10, "total_path": "total", "max_pages": 50}
    items = []
    for page in range(pag.get("max_pages", 20) if pag else 1):
        params = dict(c.get("params") or {})
        if pag:
            params[pag["param"]] = page * pag.get("step", 1) + pag.get("start", 0)
        if method == "POST":
            r = requests.post(c["url"], params=params, json=c.get("body", {}), headers={**h, "Content-Type": "application/json"}, timeout=TIMEOUT)
        else:
            r = requests.get(c["url"], params=params, headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        batch = (_get_path(data, c["list_path"]) if c.get("list_path") else data) or []
        items.extend(batch)
        if not pag or not batch:
            break
        total = _get_path(data, pag["total_path"]) if pag.get("total_path") else None
        if total is not None and len(items) >= int(total):
            break
        time.sleep(PAUSE)
    f, out = c["fields"], []

    def val(j, spec):
        # spec puede ser "campo" o ["campo1", "campo2"] (se unen con " | ")
        specs = spec if isinstance(spec, list) else [spec]
        parts = [str(_get_path(j, s)) for s in specs if s and _get_path(j, s)]
        return " | ".join(parts)

    for j in items or []:
        link = val(j, f.get("url", ""))
        out.append({"id": val(j, f.get("id", "")) or link,
                    "title": val(j, f.get("title", "")),
                    "location": val(j, f.get("location", "")),
                    "url": c.get("url_prefix", "") + link, "posted": val(j, f.get("posted", ""))})
    return out


def fetch_talentbrew(c):
    """Portales Radancy/TalentBrew (L'Oréal, Sanofi...): devuelven JSON con HTML dentro."""
    base = c["base"].rstrip("/")
    params = dict(c.get("params", {}))
    params.setdefault("RecordsPerPage", 100)
    h = {**HEADERS, "X-Requested-With": "XMLHttpRequest", **c.get("headers", {})}
    out, seen_ids = [], set()
    for page in range(1, c.get("max_pages", 10) + 1):
        params["CurrentPage"] = page
        r = requests.get(base + c.get("path", "/en/search-jobs/results"), params=params, headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        html_txt = r.json().get("results", "")
        found = 0
        for m in re.finditer(r'<a\b([^>]*href="([^"]*/job/[^"]*)"[^>]*)>(.*?)</a>', html_txt, re.S | re.I):
            attrs, href, inner = m.groups()
            jid = re.search(r'data-job-id="([^"]+)"', attrs)
            jid = jid.group(1) if jid else href
            if jid in seen_ids:
                continue
            seen_ids.add(jid)
            found += 1
            t = re.search(r"<h[23][^>]*>(.*?)</h[23]>", inner, re.S | re.I)
            loc = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</span>', inner, re.S | re.I)
            clean = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()
            out.append({"id": jid, "title": clean(t.group(1) if t else inner),
                        "location": clean(loc.group(1)) if loc else "",
                        "url": href if href.startswith("http") else base + href,
                        "posted": clean(d.group(1)) if (d := re.search(r'class="[^"]*date[^"]*"[^>]*>(.*?)</span>', inner, re.S | re.I)) else ""})
        if found == 0:
            break
        time.sleep(PAUSE)
    return out


def fetch_eightfold(c, terms):
    """Portales Eightfold AI (Kering...): careers.<empresa>.com/careers?pid=...  API nueva: /api/pcsx/search"""
    base = c["base"].rstrip("/")
    h = {**HEADERS, "Accept": "application/json, text/plain, */*", "Referer": f"{base}/careers",
         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"}
    out, seen_ids = [], set()
    for term in c.get("search_terms", terms):
        for start in range(0, c.get("max_results", 100), 10):
            params = {"domain": c["domain"], "query": term, "location": c.get("location", "France"),
                      "start": start, "sort_by": "match"}
            r = requests.get(f"{base}/api/pcsx/search", params=params, headers=h, timeout=TIMEOUT)
            r.raise_for_status()
            positions = (r.json().get("data") or {}).get("positions", [])
            for j in positions:
                jid = str(j.get("id"))
                if jid in seen_ids:
                    continue
                seen_ids.add(jid)
                locs = j.get("standardizedLocations") or j.get("locations") or []
                link = j.get("positionUrl") or f"/careers/job/{jid}"
                out.append({"id": jid, "title": j.get("name", ""),
                            "location": ", ".join(str(l) for l in locs if l) + ", France",
                            "url": link if link.startswith("http") else base + link,
                            "posted": j.get("postedTs") or j.get("creationTs")})
            if len(positions) < 10:
                break
            time.sleep(PAUSE)
    return out

def fetch_recruitee(c):
    r = requests.get(f"https://{c['slug']}.recruitee.com/api/offers/", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"id": str(j["id"]), "title": j.get("title", ""),
             "location": f"{j.get('city') or ''} {j.get('country') or ''} {j.get('location') or ''}",
             "url": j.get("careers_url", ""), "posted": j.get("published_at") or j.get("created_at")}
            for j in r.json().get("offers", [])]


def fetch_workable(c):
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{c['slug']}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"id": j.get("shortcode") or j.get("url", ""), "title": j.get("title", ""),
             "location": f"{j.get('city') or ''} {j.get('state') or ''} {j.get('country') or ''}",
             "url": j.get("url") or j.get("application_url", ""),
             "posted": j.get("published_on") or j.get("created_at")} for j in r.json().get("jobs", [])]


def fetch_teamtailor(c):
    """Teamtailor: feed RSS público en <portal>/jobs.rss (documentado por Teamtailor)."""
    import xml.etree.ElementTree as ET
    base = c["base"].rstrip("/")
    out, offset = [], 0
    while True:
        r = requests.get(f"{base}/jobs.rss", params={"offset": offset, "per_page": 100}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        items = ET.fromstring(r.content).iter("item")
        batch = 0
        for it in items:
            batch += 1
            locs = [e.text.strip() for e in it.iter() if e.text and e.text.strip()
                    and any(k in e.tag.lower() for k in ("city", "location", "country", "remote"))
                    and len(list(e)) == 0]
            link = (it.findtext("link") or "").strip()
            out.append({"id": (it.findtext("guid") or link).strip(), "title": (it.findtext("title") or "").strip(),
                        "location": ", ".join(dict.fromkeys(locs)), "url": link,
                        "posted": it.findtext("pubDate")})
        if batch < 100:
            return out
        offset += 100
        time.sleep(PAUSE)


def to_date(v):
    """Normaliza la fecha de publicación de cualquier ATS a AAAA-MM-DD ('' si no hay)."""
    import datetime as dt
    from email.utils import parsedate_to_datetime
    if v in (None, "", 0):
        return ""
    today = dt.date.today()
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
        n = float(v)
        n = n / 1000 if n > 1e11 else n  # milisegundos o segundos
        return dt.datetime.fromtimestamp(n, dt.timezone.utc).strftime("%Y-%m-%d")
    t = str(v).strip()
    low = t.lower()
    if "today" in low or "aujourd" in low:
        return today.isoformat()
    if "yesterday" in low or "hier" in low:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*(?:days?|jours?)", low)  # Workday: "Posted 3 Days Ago", "30+ Days Ago"
    if m:
        return ("<=" if "+" in low else "") + (today - dt.timedelta(days=int(m.group(1)))).isoformat()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", t)  # ISO 8601
    if m:
        return m.group(1)
    try:
        return parsedate_to_datetime(t).strftime("%Y-%m-%d")  # RSS: "Mon, 03 Nov 2026 10:00:00 +0100"
    except (TypeError, ValueError):
        pass
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t)  # 03/11/2026
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return t[:20]


# ---------- Filtro ----------

def matches(job, cfg):
    title = f" {job['title'].lower()} "
    loc = job["location"].lower()
    def has(text, words):
        return any(re.search(rf"(?<![a-zà-ÿ]){re.escape(w.strip())}(?![a-zà-ÿ])", text) for w in words)
    return (has(title, cfg["include_keywords"])
            and not has(title, cfg["exclude_keywords"])
            and has(loc, cfg["location_keywords"]))


# ---------- Telegram ----------

def send_telegram(text):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[aviso] Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID; no se envía nada.")
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=TIMEOUT)
    r.raise_for_status()


def write_csv(path, jobs, mode="a"):
    is_new_file = mode == "w" or not path.exists()
    with path.open(mode, newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        if is_new_file:
            w.writerow(["detectada", "publicada", "empresa", "titulo", "ubicacion", "url"])
        for name, j in jobs:
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), j.get("posted", ""), name, j["title"], j["location"].strip(), j["url"]])


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- Programa principal ----------

def main():
    dry = "--dry-run" in sys.argv
    companies = [c for c in load_json(ROOT / "companies.json", []) if c.get("enabled", True)]
    if "--only" in sys.argv:  # ej: --only "L'Oréal,LVMH"  (solo para pruebas)
        norm = lambda x: unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
        wanted = [norm(w.strip()) for w in sys.argv[sys.argv.index("--only") + 1].split(",")]
        companies = [c for c in companies if any(w in norm(c["name"]) for w in wanted)]
        print("Empresas seleccionadas:", [c["name"] for c in companies] or "NINGUNA (revisa el nombre o companies.json)")
        if not companies:
            sys.exit(1)
        if "--dry-run" not in sys.argv:
            sys.exit("--only solo se puede usar con --dry-run (si no, marcarías como vistas solo esas empresas)")
    cfg = load_json(ROOT / "config.json", {})
    seen = load_json(SEEN_FILE, {})
    first_run = not seen

    fetchers = {"lever": fetch_lever, "ashby": fetch_ashby, "greenhouse": fetch_greenhouse,
                "smartrecruiters": fetch_smartrecruiters, "custom_json": fetch_custom_json,
                "talentbrew": fetch_talentbrew,
                "recruitee": fetch_recruitee, "workable": fetch_workable,
                "teamtailor": fetch_teamtailor}
    new_jobs, errors = [], []

    for c in companies:
        try:
            if c["ats"] == "workday":
                jobs = fetch_workday(c, cfg.get("workday_search_terms", ["data"]))
            elif c["ats"] == "eightfold":
                jobs = fetch_eightfold(c, cfg.get("workday_search_terms", ["data"]))
            else:
                jobs = fetchers[c["ats"]](c)
        except Exception as e:  # una empresa caída no debe tumbar el resto
            code = getattr(getattr(e, "response", None), "status_code", "")
            errors.append(f"{c['name']}: {type(e).__name__} {code}".strip())
            continue
        for j in jobs:
            try:
                j["posted"] = to_date(j.get("posted"))
            except Exception:
                j["posted"] = ""
            key = f"{c['ats']}:{c['name']}:{j['id']}"
            if key in seen:
                continue
            seen[key] = time.strftime("%Y-%m-%d")
            if matches(j, cfg):
                new_jobs.append((c["name"], j))
        print(f"{c['name']}: {len(jobs)} ofertas leídas")
        time.sleep(PAUSE)

    if first_run:
        msg = (f"✅ Bot activado. Guardé {len(seen)} ofertas actuales como ya vistas "
               f"({len(new_jobs)} coinciden con tus filtros). A partir de ahora solo te aviso de las nuevas.")
    elif new_jobs:
        lines = [f"🆕 <b>{len(new_jobs)} oferta(s) nueva(s)</b>"]
        for name, j in new_jobs[:20]:
            fecha = f"   🗓 {j['posted']}" if j.get("posted") else ""
            lines.append(f"\n<b>{esc(name)}</b> · {esc(j['title'])}\n📍 {esc(j['location'].strip()[:80])}{fecha}\n{j['url']}")
        msg = "\n".join(lines)
    else:
        msg = None  # nada nuevo: silencio

    if errors:
        print("Errores:", "; ".join(errors))
        if len(errors) >= max(3, len(companies) // 3):  # solo molesta si falla mucho
            msg = (msg + "\n\n" if msg else "") + f"⚠️ {len(errors)} portales fallaron: " + ", ".join(errors[:10])

    if dry:
        print("\n--- DRY RUN ---")
        for name, j in new_jobs:
            print(f"{name} | {j['title']} | {j['location']} | {j.get('posted', '')} | {j['url']}")
        print(msg or "(sin mensaje)")
        # vista previa en Excel, se sobrescribe en cada dry-run
        write_csv(ROOT / "jobs_preview.csv", new_jobs, mode="w")
        print(f"\nVista previa guardada en jobs_preview.csv ({len(new_jobs)} ofertas)")
        return

    if new_jobs:  # incluye la primera ejecución: así el CSV arranca con lo que ya está abierto
        write_csv(JOBS_CSV, new_jobs, mode="a")

    if msg:
        send_telegram(msg)
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
