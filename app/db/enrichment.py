import json
from uuid import UUID

from app.db.connection import get_connection


def fetch_pending_sites(*, limit: int = 10, retry_failed: bool = True) -> list[dict]:
    statuses = ("pending", "failed") if retry_failed else ("pending",)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select s.id, s.url, s.domain, s.title
                from public.sites s
                join public.site_enrichments e on e.site_id = s.id
                where e.scrape_status = any(%s)
                order by s.first_seen_at asc
                limit %s
                """,
                (list(statuses), limit),
            )
            rows = cur.fetchall()
    return [
        {"id": row[0], "url": row[1], "domain": row[2], "title": row[3]}
        for row in rows
    ]


def fetch_site(site_id: UUID) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select s.id, s.url, s.domain, s.title, coalesce(e.scrape_status, 'pending')
                from public.sites s
                left join public.site_enrichments e on e.site_id = s.id
                where s.id = %s
                """,
                (site_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "url": row[1],
        "domain": row[2],
        "title": row[3],
        "scrape_status": row[4],
    }


def mark_enrichment_running(site_ids: list[UUID]) -> None:
    if not site_ids:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.site_enrichments
                set scrape_status = 'running',
                    error_message = null,
                    updated_at = now()
                where site_id = any(%s)
                """,
                (site_ids,),
            )
            conn.commit()


def save_enrichment_result(site_id: UUID, payload: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.site_enrichments (
                  site_id, emails, phones, messengers,
                  has_contact_form, has_feedback_form, has_online_widget, has_order_button,
                  page_title, meta_description, raw_text,
                  scrape_status, error_message, scraped_at, updated_at
                )
                values (
                  %s, %s, %s, %s::jsonb,
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, now(), now()
                )
                on conflict (site_id) do update set
                  emails = excluded.emails,
                  phones = excluded.phones,
                  messengers = excluded.messengers,
                  has_contact_form = excluded.has_contact_form,
                  has_feedback_form = excluded.has_feedback_form,
                  has_online_widget = excluded.has_online_widget,
                  has_order_button = excluded.has_order_button,
                  page_title = excluded.page_title,
                  meta_description = excluded.meta_description,
                  raw_text = excluded.raw_text,
                  scrape_status = excluded.scrape_status,
                  error_message = excluded.error_message,
                  scraped_at = excluded.scraped_at,
                  updated_at = now()
                """,
                (
                    site_id,
                    payload.get("emails", []),
                    payload.get("phones", []),
                    json.dumps(payload.get("messengers", {})),
                    payload.get("has_contact_form", False),
                    payload.get("has_feedback_form", False),
                    payload.get("has_online_widget", False),
                    payload.get("has_order_button", False),
                    payload.get("page_title"),
                    payload.get("meta_description"),
                    payload.get("raw_text"),
                    payload.get("status", "done"),
                    payload.get("error_message"),
                ),
            )
            conn.commit()


def list_sites(*, limit: int = 50, offset: int = 0, scrape_status: str | None = None) -> list[dict]:
    query = """
        select
          s.id, s.url, s.domain, s.title, s.first_seen_at, s.last_seen_at,
          coalesce(e.scrape_status, 'pending') as scrape_status,
          coalesce(e.emails, '{}') as emails,
          coalesce(e.phones, '{}') as phones,
          coalesce(e.messengers, '{}'::jsonb) as messengers,
          coalesce(e.has_contact_form, false),
          coalesce(e.has_feedback_form, false),
          coalesce(e.has_online_widget, false),
          coalesce(e.has_order_button, false),
          e.scraped_at
        from public.sites s
        left join public.site_enrichments e on e.site_id = s.id
    """
    params: list = []
    if scrape_status:
        query += " where coalesce(e.scrape_status, 'pending') = %s"
        params.append(scrape_status)
    query += " order by s.last_seen_at desc limit %s offset %s"
    params.extend([limit, offset])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        {
            "id": str(row[0]),
            "url": row[1],
            "domain": row[2],
            "title": row[3],
            "first_seen_at": row[4].isoformat() if row[4] else None,
            "last_seen_at": row[5].isoformat() if row[5] else None,
            "scrape_status": row[6],
            "emails": list(row[7] or []),
            "phones": list(row[8] or []),
            "messengers": row[9] if isinstance(row[9], dict) else json.loads(row[9] or "{}"),
            "has_contact_form": row[10],
            "has_feedback_form": row[11],
            "has_online_widget": row[12],
            "has_order_button": row[13],
            "scraped_at": row[14].isoformat() if row[14] else None,
        }
        for row in rows
    ]


def run_enrichment_batch(*, limit: int = 10, headless: bool = False, retry_failed: bool = True) -> dict:
    sites = fetch_pending_sites(limit=limit, retry_failed=retry_failed)
    if not sites:
        return {"processed": 0, "succeeded": 0, "failed": 0, "results": []}

    from app.scraper.enricher import enrich_sites

    site_ids = [site["id"] for site in sites]
    mark_enrichment_running(site_ids)
    enriched = enrich_sites(sites, headless=headless)

    succeeded = 0
    failed = 0
    for item in enriched:
        save_enrichment_result(UUID(item["site_id"]), item)
        if item["status"] == "done":
            succeeded += 1
        else:
            failed += 1

    return {
        "processed": len(enriched),
        "succeeded": succeeded,
        "failed": failed,
        "results": enriched,
    }


def run_enrichment_for_site(site_id: UUID, *, headless: bool = False) -> dict:
    site = fetch_site(site_id)
    if site is None:
        raise ValueError(f"Сайт {site_id} не найден")

    from app.scraper.enricher import enrich_sites

    mark_enrichment_running([site_id])
    enriched = enrich_sites([site], headless=headless)
    if not enriched:
        raise RuntimeError("Enrichment не вернул результат")

    item = enriched[0]
    save_enrichment_result(site_id, item)
    return item
