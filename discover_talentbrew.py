"""Detecta qué portales usan TalentBrew (como L'Oréal) y los añade solos a companies.json.

Uso:  python discover_talentbrew.py
Prueba cada candidato UNA vez (con pausa entre empresas). Si funciona, lo añade; si no, lo descarta.
"""
import json
import re
import time
from pathlib import Path

import requests

import job_bot

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"}

# Candidatos (nombre, dominio). Si una no es TalentBrew, simplemente se descarta.
CANDIDATES = [
    ("Sanofi", "https://jobs.sanofi.com"),
    ("EssilorLuxottica", "https://jobs.essilorluxottica.com"),
    ("EssilorLuxottica", "https://careers.essilorluxottica.com"),
    ("Engie", "https://jobs.engie.com"),
    ("Veolia", "https://jobs.veolia.com"),
    ("Michelin", "https://recrutement.michelin.fr"),
    ("Saint-Gobain", "https://joinus.saint-gobain.com"),
    ("Schneider Electric", "https://careers.se.com"),
    ("Pernod Ricard", "https://jobs.pernod-ricard.com"),
    ("Danone", "https://careers.danone.com"),
    ("Kering", "https://careers.kering.com"),
    ("Capgemini", "https://careers.capgemini.com"),
    ("Accor", "https://careers.accor.com"),
    ("Orange", "https://jobs.orange.com"),
    ("Carrefour", "https://jobs.carrefour.com"),
    ("Publicis Groupe", "https://jobs.publicisgroupe.com"),
    ("Legrand", "https://jobs.legrand.com"),
    ("Bureau Veritas", "https://jobs.bureauveritas.com"),
]
LANG_PREFIXES = ["/en", "", "/fr", "/fr-fr", "/en-us"]
PARIS = {"Distance": 50, "RadiusUnitType": 1, "Keywords": "", "Location": "Paris, Île-de-France Region",
         "Latitude": 48.85341, "Longitude": 2.34880, "ShowRadius": "True", "IsPagination": "True",
         "SearchResultsModuleName": "Search Results", "SearchFiltersModuleName": "Search Filters",
         "SortCriteria": 0, "SortDirection": 0, "SearchType": 1, "LocationType": 4,
         "LocationPath": "3017382-3012874-2968815-2988507", "ResultsType": 0, "ActiveFacetID": 0}


def probe(name, base):
    for pre in LANG_PREFIXES:
        try:
            r = requests.get(f"{base}{pre}/search-jobs", headers=UA, timeout=15)
        except requests.RequestException:
            return None, "no responde"
        if r.status_code != 200 or "search-jobs" not in r.text:
            continue
        org = re.search(r'/job/[^"\s]+?/(\d+)/\d+"', r.text) or re.search(r'data-org-id="(\d+)"', r.text) \
            or re.search(r'OrganizationIds?["=:\s]+"?(\d+)', r.text)
        params = dict(PARIS)
        if org:
            params["OrganizationIds"] = org.group(1)
        entry = {"name": name, "ats": "talentbrew", "base": base, "path": f"{pre}/search-jobs/results",
                 "params": params, "max_pages": 10}
        try:
            jobs = job_bot.fetch_talentbrew({**entry, "max_pages": 1})
        except Exception as e:
            return None, f"parece TalentBrew pero falla ({type(e).__name__})"
        if jobs:
            return entry, f"OK, {len(jobs)} ofertas en la 1ª página (París 50 km)"
        return None, "TalentBrew pero 0 ofertas (quizá otro formato de ubicación)"
    return None, "no es TalentBrew"


def main():
    path = ROOT / "companies.json"
    companies = json.loads(path.read_text(encoding="utf-8"))
    have = {c["name"].lower() for c in companies}
    added = []
    for name, base in CANDIDATES:
        if name.lower() in have:
            print(f"{name}: ya está, se salta")
            continue
        entry, msg = probe(name, base)
        print(f"{name} ({base}): {msg}")
        if entry:
            companies.append(entry)
            have.add(name.lower())
            added.append(name)
        time.sleep(2)
    if added:
        path.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nAñadidas a companies.json:", added or "ninguna")


if __name__ == "__main__":
    main()
