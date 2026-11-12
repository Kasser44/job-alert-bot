"""Detecta qué ATS hay detrás del portal de cada empresa French Tech 120 pendiente y añade las compatibles.

Uso:  python discover_ft120.py
Por cada empresa: 1 petición a su página de empleo para buscar la "huella" del ATS
(enlaces a lever, ashby, greenhouse, smartrecruiters, workday, recruitee, workable...),
y 1 petición de prueba a la API si es compatible. Pausa de 1,5 s entre empresas.
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

import job_bot

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"}

# huella en el HTML -> (ats, regex que captura el slug)
FINGERPRINTS = [
    ("lever", r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9\-_.]+)"),
    ("ashby", r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/([A-Za-z0-9\-_.%]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9\-_]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9\-_]+)"),
    ("recruitee", r"([A-Za-z0-9\-]+)\.recruitee\.com"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9\-_]+)"),
    ("workday", r"([a-z0-9\-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)"),
]
# huellas que detectamos pero el bot aún no lee (para que sepas qué hay)
KNOWN_UNSUPPORTED = {"welcomekit/WTTJ": "welcomekit.co",
                     "WTTJ": "welcometothejungle.com", "flatchr": "flatchr.io", "werecruit": "werecruit.io",
                     "jobaffinity": "jobaffinity", "taleez": "taleez.com", "breezy": "breezy.hr",
                     "personio": "personio.", "bamboohr": "bamboohr.com", "jobvite": "jobvite.com"}
SKIP_SLUGS = {"embed", "api", "jobs", "v0", "postings", "careers", "job-board", "search"}


def detect(html):
    for ats, rx in FINGERPRINTS:
        for m in re.finditer(rx, html):
            if ats == "workday":
                host, site = m.group(1), m.group(2)
                if site.lower() not in SKIP_SLUGS and site not in ("wday", "cxs"):
                    return {"ats": "workday", "host": host, "tenant": host.split(".")[0], "site": site}
            else:
                slug = m.group(1).strip("/.")
                if slug.lower() not in SKIP_SLUGS and slug.lower() != "www":
                    return {"ats": ats, "slug": slug}
    if "teamtailor" in html:
        return {"ats": "teamtailor"}  # la base se completa con la URL del portal
    for name, needle in KNOWN_UNSUPPORTED.items():
        if needle in html:
            return {"unsupported": name}
    return None


def main():
    pending = json.loads((ROOT / "ft120_pendientes.json").read_text(encoding="utf-8"))
    path = ROOT / "companies.json"
    companies = json.loads(path.read_text(encoding="utf-8"))
    have = {c["name"].lower() for c in companies}
    fetchers = {"lever": job_bot.fetch_lever, "ashby": job_bot.fetch_ashby, "greenhouse": job_bot.fetch_greenhouse,
                "smartrecruiters": job_bot.fetch_smartrecruiters, "recruitee": job_bot.fetch_recruitee,
                "workable": job_bot.fetch_workable, "teamtailor": job_bot.fetch_teamtailor}
    added, unsupported, nothing = [], {}, []

    for p in pending:
        name, url = p["name"], p["url"]
        if name.lower() in have:
            continue
        try:
            html = requests.get(url, headers=UA, timeout=15).text
        except requests.RequestException as e:
            print(f"{name}: no responde ({type(e).__name__})"); nothing.append(name); continue
        found = detect(html)
        if not found:
            print(f"{name}: sin huella conocida (portal propio o carga por JavaScript)"); nothing.append(name)
        elif "unsupported" in found:
            print(f"{name}: usa {found['unsupported']} (el bot aún no lo lee)")
            unsupported.setdefault(found["unsupported"], []).append(name)
        else:
            entry = {"name": name, **found}
            if found["ats"] == "teamtailor":
                m = re.match(r"(https?://[^/]+)", url)
                entry["base"] = m.group(1)
            try:
                jobs = (job_bot.fetch_workday(entry, ["data"]) if found["ats"] == "workday"
                        else fetchers[found["ats"]](entry))
                print(f"{name}: {found['ats']} ({found.get('slug') or found.get('host') or entry.get('base')}) OK, {len(jobs)} ofertas")
                companies.append(entry); have.add(name.lower()); added.append(name)
            except Exception as e:
                code = getattr(getattr(e, "response", None), "status_code", "")
                print(f"{name}: parece {found['ats']} ({found.get('slug') or found.get('host')}) pero la API falla {code}")
                nothing.append(name)
        time.sleep(1.5)

    if added:
        path.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== RESUMEN ===")
    print("Añadidas:", added or "ninguna")
    for k, v in unsupported.items():
        print(f"Usan {k} ({len(v)}):", ", ".join(v))
    print(f"Sin detectar ({len(nothing)}):", ", ".join(nothing))


if __name__ == "__main__":
    sys.exit(main())
