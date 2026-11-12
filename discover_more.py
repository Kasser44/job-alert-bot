"""Prueba si las empresas que faltan usan SmartRecruiters, Eightfold o Workday (con nombres típicos).
Uso:  python discover_more.py
Hace pocas peticiones por empresa, con pausa. Lo que funciona se añade solo a companies.json.
"""
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}

# nombre: (posibles slugs SmartRecruiters, posible dominio Eightfold, posibles tenants Workday)
CANDIDATES = {
    "EssilorLuxottica": (["EssilorLuxottica", "Essilor", "Luxottica"], "essilorluxottica.com", ["essilorluxottica", "luxottica", "essilor"]),
    "Engie": (["Engie", "ENGIE"], "engie.com", ["engie"]),
    "Capgemini": (["Capgemini"], "capgemini.com", ["capgemini"]),
    "Michelin": (["Michelin"], "michelin.com", ["michelin"]),
    "Saint-Gobain": (["SaintGobain", "Saint-Gobain"], "saint-gobain.com", ["saintgobain"]),
    "Schneider Electric": (["SchneiderElectric"], "se.com", ["schneiderelectric", "se"]),
    "Orange": (["Orange"], "orange.com", ["orange"]),
    "Carrefour": (["Carrefour", "CarrefourFrance"], "carrefour.com", ["carrefour"]),
    "Publicis Groupe": (["PublicisGroupe", "Publicis"], "publicisgroupe.com", ["publicis"]),
    "Legrand": (["Legrand", "LegrandGroup"], "legrand.com", ["legrand"]),
    "Bureau Veritas": (["BureauVeritas", "BureauVeritasGroup"], "bureauveritas.com", ["bureauveritas"]),
    "TotalEnergies": (["TotalEnergies"], "totalenergies.com", ["totalenergies"]),
    "Renault": (["RenaultGroup", "Renault"], "renaultgroup.com", ["renault", "alliance"]),
    "Stellantis": (["Stellantis"], "stellantis.com", ["stellantis"]),
    "Unibail-Rodamco-Westfield": (["URW", "UnibailRodamcoWestfield"], "urw.com", ["urw"]),
    "Vinci": (["VINCI", "Vinci"], "vinci.com", ["vinci"]),
    "Bouygues": (["Bouygues", "BouyguesConstruction"], "bouygues.com", ["bouygues"]),
    "Eiffage": (["Eiffage"], "eiffage.com", ["eiffage"]),
    "Hermès": (["Hermes"], "hermes.com", ["hermes"]),
    "Danone": ([], None, ["danone"]),
}
WD_HOSTS = ["wd3", "wd103"]
WD_SITES = ["{t}", "External", "Careers", "{t}careers", "External_Careers"]


def try_smartrecruiters(slug):
    r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                     params={"limit": 1, "country": "fr"}, headers=UA, timeout=15)
    if r.status_code == 200 and r.json().get("totalFound", 0) > 0:
        return {"ats": "smartrecruiters", "slug": slug, "params": {"country": "fr"}}, r.json()["totalFound"]
    return None, 0


def try_eightfold(domain):
    base = f"https://careers.{domain}"
    try:
        r = requests.get(f"{base}/api/pcsx/search", params={"domain": domain, "query": "data", "location": "France", "start": 0},
                     headers={**UA, "Referer": f"{base}/careers"}, timeout=15)
    except requests.RequestException:
        return None, 0  # careers.<dominio> no existe
    if r.status_code == 200 and "application/json" in r.headers.get("content-type", ""):
        n = len(((r.json().get("data") or {}).get("positions")) or [])
        if n:
            return {"ats": "eightfold", "base": base, "domain": domain, "location": "France"}, n
    return None, 0


def try_workday(tenant):
    for h in WD_HOSTS:
        host = f"{tenant}.{h}.myworkdayjobs.com"
        for site in WD_SITES:
            site = site.format(t=tenant)
            try:
                r = requests.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                  json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "data"},
                                  headers={**UA, "Content-Type": "application/json"}, timeout=15)
            except requests.RequestException:
                break  # el host no existe: no probar más sites
            if r.status_code == 200 and "jobPostings" in r.text:
                return {"ats": "workday", "host": host, "tenant": tenant, "site": site}, r.json().get("total", 0)
            time.sleep(0.5)
    return None, 0


ONLY_SKIP = {"EssilorLuxottica", "Capgemini", "Schneider Electric", "Publicis Groupe", "Legrand",
             "Bureau Veritas", "TotalEnergies", "Stellantis"}  # ya probadas sin éxito


def main():
    path = ROOT / "companies.json"
    companies = json.loads(path.read_text(encoding="utf-8"))
    have = {c["name"].lower() for c in companies}
    added = []
    for name, (sr_slugs, ef_domain, wd_tenants) in CANDIDATES.items():
        if name.lower() in have or name in ONLY_SKIP:
            print(f"{name}: ya está, se salta")
            continue
        entry, info = None, ""
        try:
            for slug in sr_slugs:
                entry, n = try_smartrecruiters(slug)
                if entry:
                    info = f"SmartRecruiters ({slug}), {n} ofertas en Francia"; break
                time.sleep(0.5)
            if not entry and ef_domain:
                entry, n = try_eightfold(ef_domain)
                if entry:
                    info = f"Eightfold, {n} ofertas 'data' en Francia (1ª página)"
            if not entry:
                for t in wd_tenants:
                    entry, n = try_workday(t)
                    if entry:
                        info = f"Workday ({entry['host']}/{entry['site']}), {n} ofertas 'data' mundial"; break
        except Exception as e:
            info = f"error {type(e).__name__}"
        print(f"{name}: {info or 'nada detectado (manual)'}")
        if entry:
            companies.append({"name": name, **entry})
            added.append(name)
        time.sleep(1)
    if added:
        path.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nAñadidas a companies.json:", added or "ninguna")


if __name__ == "__main__":
    main()
