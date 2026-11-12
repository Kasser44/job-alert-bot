# job-alert-bot

Bot personal que consulta cada 2 horas las APIs públicas de empleo (Lever, Ashby, Greenhouse, SmartRecruiters y Workday) de una lista de empresas, filtra las ofertas de Data / BI / Pricing en Île-de-France y avisa por Telegram solo de las nuevas.

- `companies.json`: empresas y tipo de portal.
- `config.json`: palabras clave de título, exclusiones y ubicaciones.
- `job_bot.py`: el script. `python job_bot.py --dry-run` para probar sin enviar nada.
- `.github/workflows/job-bot.yml`: ejecución programada en GitHub Actions.

Stack: Python (requests), APIs REST, GitHub Actions, Telegram Bot API.
