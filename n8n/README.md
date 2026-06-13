# n8n — оркестрация LeadPipe

## Главный workflow

Импортируйте **`unified-pipeline.json`** — один пайплайн на все сценарии.

```
поиск (опционально) → enrich → forms → LLM outreach → submit → Google Sheets
```

### Триггеры

| Триггер | Когда использовать |
|---------|-------------------|
| **Manual Trigger** | Ручной запуск из n8n UI |
| **Schedule Trigger** | Cron (по умолчанию `0 9 * * 1-5`) |
| **Webhook** `POST /leadpipe` | Внешний вызов, интеграции |

### Режимы запуска (`start_mode`)

Задаётся в ноде **Default Config** или в body webhook.

| Режим | Что делает |
|-------|------------|
| `full` | Сначала Google-поиск, потом обработка N сайтов |
| `existing` | **Без поиска** — берёт лиды из БД (`scrape_status`) |
| `single_site` | Один лид по `site_id` |

**Типичные сценарии:**

- Данных ещё нет → `start_mode: full`
- Поиск уже был, нужно только outreach → `start_mode: existing`
- Один конкретный лид → `start_mode: single_site` + `site_id`

### Google Sheets

1. Создайте таблицу, лист **`Results`**
2. Первая строка — заголовки (см. ниже)
3. В n8n: Credentials → **Google Sheets OAuth2**
4. В **Default Config** укажите `google_sheet_id` (ID из URL таблицы)
5. Нода **Google Sheets Append** → привяжите credential

Если `google_sheet_id` пуст — результаты только в логе выполнения n8n.

**Колонки листа Results:**

```
run_at | trigger | start_mode | site_id | domain | pipeline_status | enrich_status | forms_status | outreach_status | submit_status | outreach_id | submitted | dry_run | fields_filled | sent_status | message_subject | message_text | llm_fit_score | llm_is_fit | error_message
```

| Поле | Значение |
|------|----------|
| `submitted` | `true` — форма реально отправлена |
| `dry_run` | `true` — только заполнение без Submit |
| `sent_status` | `pending` / `sent` / `skipped` / `failed` |
| `pipeline_status` | `done` / `dry_run` / `skipped` / `failed` |

---

## Предварительно

1. API запущен: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (или `docker compose up`)
2. В `.env` API: `DATABASE_URL`, `OPENAI_API_KEY`
3. SQL-миграции: `001_site_forms.sql`, `002_llm_qualify.sql`
4. n8n установлен (Docker или desktop)

### n8n в Docker + API на хосте (Windows)

```
http://host.docker.internal:8000
```

Если API тоже в Docker (тот же compose) — `http://api:8000`.

---

## Примеры webhook

### Полный цикл: поиск + обработка

```bash
curl -X POST "https://your-n8n.com/webhook/leadpipe" \
  -H "Content-Type: application/json" \
  -d '{
    "start_mode": "full",
    "search_query": "стоматология",
    "search_cities": ["Казань"],
    "sites_limit": 3,
    "dry_run_submit": true
  }'
```

### Только существующие лиды (без поиска)

```bash
curl -X POST "https://your-n8n.com/webhook/leadpipe" \
  -H "Content-Type: application/json" \
  -d '{
    "start_mode": "existing",
    "sites_limit": 10,
    "scrape_status": "pending",
    "dry_run_submit": true
  }'
```

### Один сайт

```bash
curl -X POST "https://your-n8n.com/webhook/leadpipe" \
  -H "Content-Type: application/json" \
  -d '{
    "start_mode": "single_site",
    "site_id": "uuid-из-supabase",
    "dry_run_submit": true
  }'
```

Поля `offer_description`, `sender_*`, `google_sheet_id` можно передать в body — перекроют Config.

---

## Ручной запуск без поиска

1. Импортируйте `unified-pipeline.json`
2. Откройте **Default Config**
3. Установите `start_mode` = **`existing`**
4. Настройте `sites_limit`, `scrape_status` (`pending` / `done`)
5. Execute Workflow

---

## Legacy workflow'ы

Старые файлы оставлены для совместимости:

| Файл | Назначение |
|------|------------|
| `batch-outreach-pipeline.json` | Только batch, без Sheets |
| `site-outreach-webhook.json` | Один сайт, webhook |
| `site-outreach-steps.json` | Пошаговая отладка |

---

## Безопасность

- Webhook URL держите в секрете или добавьте Header Auth в n8n
- Начинайте с `dry_run_submit: true`
- Формы с капчей → `sent_status: skipped`
- Боевая отправка: `dry_run_submit: false` только после тестов
