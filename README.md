# job-alert-bot

Personal job-alert bot. Every 2 hours on weekdays it queries the career portals of ~70 French companies (CAC 40 and French Tech 120), keeps only Data / BI / Pricing roles in Île-de-France, and sends **only the new ones** to Telegram.

Built to stop checking dozens of career sites by hand, and as a hands-on project with REST APIs, Git and CI/CD.

## How it works

```
GitHub Actions (cron, every 2 h)
        │
        ▼
job_bot.py ──► one connector per ATS ──► career-site APIs (JSON / RSS)
        │
        ├─ normalize publication date from each ATS (epoch, ISO, RSS, "Posted 3 days ago")
        ├─ filter: title keywords, exclusions, location   (config.json)
        ├─ dedupe against seen.json (state committed back to the repo)
        ├─ append matches to jobs.csv (local history, opens in Excel; not committed)
        └─ Telegram Bot API ──► message with the new offers only
```

## Supported portals (connectors)

| Connector | Source | Example companies |
|---|---|---|
| `lever` | Public postings API (EU instance fallback) | BlaBlaCar, Contentsquare, Malt |
| `ashby` | Public job-board API | Doctolib, Qonto, Mistral AI |
| `greenhouse` | Public boards API | Shift Technology, Flowdesk |
| `smartrecruiters` | Public postings API, country filter | Accor, Saint-Gobain, EcoVadis |
| `workday` | JSON endpoint used by the Workday career site, paginated per search term | Pernod Ricard, Air Liquide |
| `eightfold` | JSON search endpoint used by the career site | Kering |
| `talentbrew` | Radancy/TalentBrew results endpoint (HTML inside JSON, parsed) | L'Oréal, Sanofi, Veolia |
| `teamtailor` | Official public RSS feed (`/jobs.rss`) | Payfit, Spendesk, Akeneo |
| `recruitee` / `workable` | Public offers APIs | Mistertemp', CorWave, Homa |
| `custom_json` | Any JSON endpoint, configured in `companies.json` (URL, method, body, headers, list path, field mapping, pagination) | LVMH |

## Files

| File | Purpose |
|---|---|
| `job_bot.py` | Main script: 11 connectors, date normalization, filter, state, CSV log, Telegram |
| `companies.json` | Companies to watch and which connector each one uses |
| `config.json` | Include / exclude keywords, accepted locations, search terms |
| `.github/workflows/job-bot.yml` | Scheduled run on GitHub Actions |
| `curl_to_entry.py` | Turns a DevTools "Copy as cURL" into a `custom_json` entry and tests it without cookies |
| `discover_*.py` | Detect which ATS a company uses (HTML fingerprints, known endpoints) and add it automatically |

## Run locally

```bash
pip install -r requirements.txt
python job_bot.py --dry-run                     # read everything, print matches, write jobs_preview.csv, send nothing
python job_bot.py --dry-run --only "kering,lvmh" # test specific companies only
```

## Deploy

1. Create a Telegram bot with @BotFather and get your chat id.
2. Add `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` as repository secrets.
3. Run the workflow once from the Actions tab. The first run only records current offers as seen; later runs alert on new ones.

## Design choices

- **Official or site-own endpoints only.** No LinkedIn or job-board scraping. Cookies and session tokens are never stored: every connector must work without a logged-in session.
- **Polite by default.** Pause between requests, country/location filters applied at the source when the API allows it, silence when nothing is new.
- **Fail soft.** One portal failing does not stop the run; a warning is sent only when many portals fail at once.

## Roadmap

- [ ] Load `jobs.csv` into BigQuery and model it with dbt (staging → marts), with dbt docs lineage
- [ ] Dashboard: new offers per week, per company, per role family
- [ ] Browser-based connector for portals without a usable API (Danone, EssilorLuxottica)
