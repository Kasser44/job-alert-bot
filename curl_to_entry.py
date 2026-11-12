"""Convierte un "Copy as cURL (bash)" de DevTools en una entrada para companies.json.

Uso:
  1. En DevTools > Network: clic derecho en la petición > Copy > Copy as cURL (bash)
  2. Pega el contenido en curl.txt (en esta carpeta) y guarda
  3. python curl_to_entry.py "Nombre Empresa"
  4. Revisa la propuesta y pégala dentro de companies.json

No guarda cookies ni tokens: los descarta a propósito, porque el bot debe
funcionar sin tu sesión (así es como correrá en GitHub Actions).
"""
import json
import shlex
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
KEEP_HEADERS = {"origin", "referer", "user-agent", "accept", "content-type"}
TITLE_KEYS = ["title", "name", "jobTitle", "job_title", "positionName", "text", "label", "intitule"]
URL_KEYS = ["url", "link", "applyUrl", "apply_url", "jobUrl", "hostedUrl", "absolute_url", "slug", "externalPath", "path"]
ID_KEYS = ["id", "objectID", "jobId", "job_id", "reference", "ref", "uuid", "requisitionId"]
LOC_KEYS = ["location", "city", "locationName", "location_name", "country", "place", "ville", "lieu", "address"]


def parse_curl(text):
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")
    tokens = shlex.split(text)
    url, method, headers, body = None, None, {}, None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if t in ("-X", "--request"):
            method = nxt; i += 2; continue
        if t in ("-H", "--header"):
            k, _, v = nxt.partition(":")
            if k.strip().lower() in KEEP_HEADERS:
                headers[k.strip()] = v.strip()
            i += 2; continue
        if t in ("--data-raw", "--data", "-d", "--data-binary"):
            body = nxt; i += 2; continue
        if t in ("-b", "--cookie"):
            i += 2; continue  # cookies descartadas a propósito
        if t == "--url":
            url = nxt; i += 2; continue
        if t.startswith("http"):
            url = t
        i += 1
    method = (method or ("POST" if body else "GET")).upper()
    return url, method, headers, body


def find_job_list(obj, path=""):
    """Busca la lista de diccionarios más grande que tenga algo parecido a un título."""
    best = None
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:5]):
        if any(k in obj[0] for k in TITLE_KEYS):
            best = (len(obj), path)
    items = obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else []
    for k, v in items:
        if isinstance(v, (dict, list)):
            sub = find_job_list(v, f"{path}.{k}" if path else str(k))
            if sub and (not best or sub[0] > best[0]):
                best = sub
    return best


def first_key(d, candidates):
    for k in candidates:
        if k in d and d[k] not in (None, "", [], {}):
            return k
    return None


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Empresa"
    url, method, headers, body = parse_curl((ROOT / "curl.txt").read_text(encoding="utf-8"))
    print(f"URL: {url}\nMETODO: {method}\nCABECERAS: {list(headers)}")
    if not any(k.lower() == "user-agent" for k in headers):
        headers["User-Agent"] = ( "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

    body_json = json.loads(body) if body else None
    if method == "POST":
        r = requests.post(url, json=body_json, headers=headers, timeout=20)
    else:
        r = requests.get(url, headers=headers, timeout=20)
    print("STATUS:", r.status_code)
    if r.status_code != 200:
        print("Falla sin cookies. Seguramente hay anti-bots o hace falta sesión. Pásaselo a Claude.")
        print(r.text[:300]); return
    try:
        data = r.json()
    except ValueError:
        print("La respuesta no es JSON (es HTML). Esta petición no sirve; busca otra en Network.")
        return

    found = find_job_list(data)
    if not found:
        print("No encontré una lista de ofertas en la respuesta. Pásaselo a Claude.")
        print(json.dumps(data, ensure_ascii=False)[:800]); return
    count, list_path = found
    items = data
    for part in list_path.split(".") if list_path else []:
        items = items[int(part)] if isinstance(items, list) else items[part]
    sample = items[0]

    fields = {"id": first_key(sample, ID_KEYS), "title": first_key(sample, TITLE_KEYS),
              "location": first_key(sample, LOC_KEYS), "url": first_key(sample, URL_KEYS)}
    entry = {"name": name, "ats": "custom_json", "url": url, "method": method}
    if headers:
        entry["headers"] = headers
    if body_json is not None:
        entry["body"] = body_json
    entry["list_path"] = list_path
    entry["fields"] = {k: v for k, v in fields.items() if v}

    print(f"\nOfertas en esta respuesta: {count}")
    print("CAMPOS DISPONIBLES:", list(sample.keys()))
    print("\nEJEMPLO:", {k: sample.get(v) for k, v in entry["fields"].items()})
    missing = [k for k, v in fields.items() if not v]
    if missing:
        print(f"\n[atención] No adiviné: {missing}. Elige a mano entre CAMPOS DISPONIBLES.")
    link = str(sample.get(fields["url"] or "", ""))
    if link and not link.startswith("http"):
        print("[atención] El enlace es relativo: añade \"url_prefix\": \"https://dominio-de-la-empresa\"")
    print("\n--- PEGA ESTO EN companies.json (dentro de la lista, con una coma) ---")
    print(json.dumps(entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
