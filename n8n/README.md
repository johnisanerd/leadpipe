# n8n — оркестрация outreach-пайплайна

Workflow'ы вызывают FastAPI (`http://127.0.0.1:8000`) и проходят полный цикл:

```
поиск (опционально) → enrich → forms → LLM outreach → form-fill → submit
```

## Предварительно

1. API запущен: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. В `.env` API: `DATABASE_URL`, `OPENAI_API_KEY`
3. SQL-миграции: `001_site_forms.sql`, `002_llm_qualify.sql`
4. n8n установлен (Docker или desktop)

### n8n в Docker + API на хосте (Windows)

В workflow вместо `127.0.0.1` используйте:

```
http://host.docker.internal:8000
```

## Импорт workflow

1. n8n → **Workflows** → **Import from File**
2. Выберите один из файлов:
   - `site-outreach-webhook.json` — один сайт по webhook
   - `batch-outreach-pipeline.json` — batch: поиск + N сайтов
   - `site-outreach-steps.json` — пошагово (видно каждый этап в логах)

3. Откройте ноду **Config** и задайте `api_base`, оффер и отправителя.

## Эндпойнты, которые дергает n8n

| Шаг | Метод | Назначение |
|-----|-------|------------|
| Поиск | `POST /search/google/batch` | Собрать сайты из выдачи |
| Список | `GET /sites?scrape_status=pending` | Взять сайты для обработки |
| **Всё сразу** | `POST /pipeline/site/{site_id}` | enrich → forms → generate → submit |
| **Batch** | `POST /pipeline/batch` | поиск + pipeline для N сайтов |
| Пошагово | `POST /enrich/site/{id}` … | Отладка отдельных шагов |

### Один сайт (рекомендуется для webhook)

```http
POST /pipeline/site/{site_id}
Content-Type: application/json

{
  "offer_description": "Помогаем стоматологиям получать больше заявок через SEO.",
  "sender_name": "Иван Петров",
  "sender_company": "Dental Marketing",
  "sender_phone": "+7 (999) 123-45-67",
  "sender_email": "ivan@example.com",
  "dry_run_submit": true,
  "headless": true,
  "skip_unqualified": true
}
```

`dry_run_submit: true` — заполнит форму, но **не нажмёт Submit**.  
Для боевой отправки: `"dry_run_submit": false`.

### Batch (cron / manual)

```http
POST /pipeline/batch
Content-Type: application/json

{
  "search_query": "стоматология",
  "search_cities": ["Казань"],
  "search_max_pages": 1,
  "sites_limit": 5,
  "scrape_status": "pending",
  "offer_description": "...",
  "sender_name": "Иван Петров",
  "sender_company": "Dental Marketing",
  "dry_run_submit": true
}
```

Если `search_query` и `search_cities` не заданы — batch обработает уже существующие сайты из БД.

## Webhook (один сайт)

После импорта `site-outreach-webhook.json` активируйте workflow и скопируйте Production URL.

Пример вызова:

```bash
curl -X POST "https://your-n8n.com/webhook/outreach-pipeline" \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": "uuid-сайта-из-supabase",
    "dry_run_submit": true
  }'
```

Поля `offer_description`, `sender_*` можно передать в body — они перекроют значения из ноды Config.

## Cron (batch по расписанию)

1. Импортируйте `batch-outreach-pipeline.json`
2. Замените **Manual Trigger** на **Schedule Trigger** (например, `0 9 * * 1-5` — будни в 9:00)
3. Установите `dry_run_submit: false` только после тестов

## Безопасность

- Webhook URL держите в секрете или добавьте Header Auth в n8n
- Начинайте всегда с `dry_run_submit: true`
- Формы с капчей автоматически получают `sent_status: skipped`

## Ответ pipeline

```json
{
  "site_id": "...",
  "domain": "example.ru",
  "status": "dry_run",
  "steps": [
    {"step": "enrich", "status": "done", "emails": 1, "phones": 2},
    {"step": "forms", "status": "done", "forms_saved": 1},
    {"step": "outreach", "status": "done", "outreach_id": "..."},
    {"step": "submit", "status": "done", "dry_run": true, "submitted": false}
  ],
  "outreach_id": "..."
}
```

Статусы: `done` | `dry_run` | `skipped` | `failed`.
