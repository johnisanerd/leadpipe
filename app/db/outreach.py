from __future__ import annotations

import json
from uuid import UUID

from app.db.connection import get_connection
from app.db.forms import list_site_forms
from app.llm.form_fill import build_form_fill_plan
from app.llm.outreach import generate_outreach_message
from app.llm.qualify import qualify_site


def _site_context_query(extra_conditions: list[str] | None = None) -> str:
    conditions = extra_conditions or []
    where = (" where " + " and ".join(conditions)) if conditions else ""
    return f"""
        select
          s.id,
          s.url,
          s.domain,
          s.title,
          e.page_title,
          e.meta_description,
          e.raw_text,
          coalesce(e.emails, '{{}}') as emails,
          coalesce(e.phones, '{{}}') as phones,
          coalesce(e.messengers, '{{}}'::jsonb) as messengers,
          coalesce(e.has_contact_form, false) as has_contact_form,
          coalesce(e.has_feedback_form, false) as has_feedback_form,
          coalesce(e.scrape_status, 'pending') as scrape_status,
          e.llm_is_fit,
          e.llm_fit_score,
          e.llm_business_type,
          e.llm_business_name,
          e.llm_qualify_reason,
          (
            select sr.snippet
            from public.serp_results sr
            where sr.site_id = s.id
            order by sr.found_at desc
            limit 1
          ) as serp_snippet,
          (
            select sr2.city
            from public.serp_results sr
            join public.search_runs sr2 on sr2.id = sr.search_run_id
            where sr.site_id = s.id
            order by sr.found_at desc
            limit 1
          ) as city,
          (
            select sq.query
            from public.serp_results sr
            join public.search_runs sr2 on sr2.id = sr.search_run_id
            join public.search_queries sq on sq.id = sr2.search_query_id
            where sr.site_id = s.id
            order by sr.found_at desc
            limit 1
          ) as search_query
        from public.sites s
        left join public.site_enrichments e on e.site_id = s.id
        {where}
    """


def _row_to_site_context(row: tuple) -> dict:
    messengers = row[9]
    if not isinstance(messengers, dict):
        messengers = json.loads(messengers or "{}")
    qualify = None
    if row[13] is not None:
        qualify = {
            "is_fit": row[13],
            "score": row[14],
            "business_type": row[15],
            "business_name": row[16],
            "reason": row[17],
        }
    return {
        "site_id": str(row[0]),
        "url": row[1],
        "domain": row[2],
        "title": row[3],
        "page_title": row[4],
        "meta_description": row[5],
        "raw_text": row[6],
        "emails": list(row[7] or []),
        "phones": list(row[8] or []),
        "messengers": messengers,
        "has_contact_form": row[10],
        "has_feedback_form": row[11],
        "scrape_status": row[12],
        "qualify": qualify,
        "serp_snippet": row[18],
        "city": row[19],
        "search_query": row[20],
    }


def fetch_sites_for_llm(
    *,
    limit: int = 10,
    site_id: UUID | None = None,
    only_enriched: bool = True,
    only_without_outreach: bool = True,
    only_without_qualify: bool = False,
    min_qualify_score: int | None = None,
    only_fit: bool = False,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []

    if site_id:
        conditions.append("s.id = %s")
        params.append(site_id)
    if only_enriched:
        conditions.append("e.scrape_status = 'done'")
    if only_without_outreach:
        conditions.append(
            """
            not exists (
              select 1 from public.site_outreach o
              where o.site_id = s.id
                and o.sent_status in ('pending', 'sent')
                and o.message_text is not null
            )
            """
        )
    if only_without_qualify:
        conditions.append("e.llm_qualified_at is null")
    if only_fit:
        conditions.append("e.llm_is_fit = true")
    if min_qualify_score is not None:
        conditions.append("coalesce(e.llm_fit_score, 0) >= %s")
        params.append(min_qualify_score)

    query = _site_context_query(conditions)
    query += " order by s.last_seen_at desc limit %s"
    params.append(limit)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [_row_to_site_context(row) for row in rows]


def get_site_context(site_id: UUID) -> dict | None:
    sites = fetch_sites_for_llm(
        limit=1,
        site_id=site_id,
        only_enriched=False,
        only_without_outreach=False,
    )
    return sites[0] if sites else None


def save_qualify_result(site_id: UUID, result: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.site_enrichments
                set
                  llm_is_fit = %s,
                  llm_fit_score = %s,
                  llm_business_type = %s,
                  llm_business_name = %s,
                  llm_qualify_reason = %s,
                  llm_qualified_at = now(),
                  updated_at = now()
                where site_id = %s
                """,
                (
                    result.get("is_fit"),
                    result.get("score"),
                    result.get("business_type"),
                    result.get("business_name"),
                    result.get("reason"),
                    site_id,
                ),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Enrichment для сайта {site_id} не найден — сначала POST /enrich/sites")
        conn.commit()


def save_outreach_message(
    site_id: UUID,
    *,
    message_subject: str | None,
    message_text: str,
    sent_status: str = "pending",
) -> UUID:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.site_outreach (
                  site_id, message_subject, message_text, sent_status
                )
                values (%s, %s, %s, %s)
                returning id
                """,
                (site_id, message_subject, message_text, sent_status),
            )
            row = cur.fetchone()
        conn.commit()
    return row[0]


def list_outreach(
    *,
    limit: int = 50,
    offset: int = 0,
    site_id: UUID | None = None,
    sent_status: str | None = None,
) -> list[dict]:
    query = """
        select
          o.id, o.site_id, s.url, s.domain,
          o.message_subject, o.message_text, o.sent_status,
          o.sent_at, o.error_message, o.created_at,
          e.llm_fit_score, e.llm_is_fit, e.llm_business_name
        from public.site_outreach o
        join public.sites s on s.id = o.site_id
        left join public.site_enrichments e on e.site_id = s.id
    """
    params: list = []
    conditions: list[str] = []

    if site_id:
        conditions.append("o.site_id = %s")
        params.append(site_id)
    if sent_status:
        conditions.append("o.sent_status = %s")
        params.append(sent_status)

    if conditions:
        query += " where " + " and ".join(conditions)

    query += " order by o.created_at desc limit %s offset %s"
    params.extend([limit, offset])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        {
            "id": str(row[0]),
            "site_id": str(row[1]),
            "site_url": row[2],
            "domain": row[3],
            "message_subject": row[4],
            "message_text": row[5],
            "sent_status": row[6],
            "sent_at": row[7].isoformat() if row[7] else None,
            "error_message": row[8],
            "created_at": row[9].isoformat() if row[9] else None,
            "llm_fit_score": row[10],
            "llm_is_fit": row[11],
            "llm_business_name": row[12],
        }
        for row in rows
    ]


def run_qualify_batch(
    *,
    limit: int = 10,
    target_query: str = "стоматология",
    target_description: str = "стоматологическая клиника",
    only_without_qualify: bool = True,
    save_results: bool = True,
) -> dict:
    sites = fetch_sites_for_llm(
        limit=limit,
        only_enriched=True,
        only_without_outreach=False,
        only_without_qualify=only_without_qualify,
    )
    results = []
    fit_count = 0

    for site in sites:
        site_id = UUID(site["site_id"])
        try:
            qualify_result = qualify_site(
                site,
                target_query=target_query or site.get("search_query") or "стоматология",
                target_description=target_description,
            )
            if save_results:
                save_qualify_result(site_id, qualify_result)
            if qualify_result.get("is_fit"):
                fit_count += 1
            results.append(
                {
                    "site_id": site["site_id"],
                    "domain": site["domain"],
                    "status": "done",
                    "qualify": qualify_result,
                    "error_message": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "site_id": site["site_id"],
                    "domain": site["domain"],
                    "status": "failed",
                    "qualify": None,
                    "error_message": str(exc),
                }
            )

    return {
        "processed": len(results),
        "fit_count": fit_count,
        "results": results,
    }


def run_qualify_for_site(
    site_id: UUID,
    *,
    target_query: str = "стоматология",
    target_description: str = "стоматологическая клиника",
) -> dict:
    site = get_site_context(site_id)
    if not site:
        raise ValueError(f"Сайт {site_id} не найден")
    if site.get("scrape_status") != "done":
        raise ValueError(f"Сайт {site_id} не обогащён — сначала POST /enrich/site/{site_id}")

    qualify_result = qualify_site(
        site,
        target_query=target_query or site.get("search_query") or "стоматология",
        target_description=target_description,
    )
    save_qualify_result(site_id, qualify_result)
    return {
        "site_id": str(site_id),
        "domain": site["domain"],
        "status": "done",
        "qualify": qualify_result,
        "error_message": None,
    }


def run_generate_batch(
    *,
    limit: int = 10,
    offer_description: str,
    sender_name: str,
    sender_company: str,
    sender_phone: str = "",
    sender_email: str = "",
    call_to_action: str = "Ответьте на сообщение — обсудим детали.",
    only_without_outreach: bool = True,
    skip_unqualified: bool = True,
    min_qualify_score: int = 60,
    auto_qualify: bool = True,
    target_query: str = "стоматология",
    target_description: str = "стоматологическая клиника",
) -> dict:
    sites = fetch_sites_for_llm(
        limit=limit,
        only_enriched=True,
        only_without_outreach=only_without_outreach,
        only_fit=skip_unqualified and not auto_qualify,
        min_qualify_score=min_qualify_score if (skip_unqualified and not auto_qualify) else None,
    )
    results = []
    generated = 0

    for site in sites:
        site_id = UUID(site["site_id"])
        try:
            qualify_result = site.get("qualify")
            if auto_qualify and (qualify_result is None or qualify_result.get("score") is None):
                qualify_result = qualify_site(
                    site,
                    target_query=target_query or site.get("search_query") or "стоматология",
                    target_description=target_description,
                )
                save_qualify_result(site_id, qualify_result)
                site["qualify"] = qualify_result

            if skip_unqualified and not (qualify_result or {}).get("is_fit"):
                results.append(
                    {
                        "site_id": site["site_id"],
                        "domain": site["domain"],
                        "status": "skipped",
                        "outreach_id": None,
                        "message_subject": None,
                        "message_text": None,
                        "qualify": qualify_result,
                        "error_message": "Сайт не прошёл квалификацию",
                    }
                )
                continue

            site_with_qualify = {**site, "qualify": qualify_result}
            message = generate_outreach_message(
                site_with_qualify,
                offer_description=offer_description,
                sender_name=sender_name,
                sender_company=sender_company,
                call_to_action=call_to_action,
            )
            outreach_id = save_outreach_message(
                site_id,
                message_subject=message.get("message_subject"),
                message_text=message["message_text"],
            )
            generated += 1
            results.append(
                {
                    "site_id": site["site_id"],
                    "domain": site["domain"],
                    "status": "done",
                    "outreach_id": str(outreach_id),
                    "message_subject": message.get("message_subject"),
                    "message_text": message.get("message_text"),
                    "personalization_hook": message.get("personalization_hook"),
                    "qualify": qualify_result,
                    "error_message": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "site_id": site["site_id"],
                    "domain": site["domain"],
                    "status": "failed",
                    "outreach_id": None,
                    "message_subject": None,
                    "message_text": None,
                    "qualify": site.get("qualify"),
                    "error_message": str(exc),
                }
            )

    return {
        "processed": len(results),
        "generated": generated,
        "results": results,
    }


def run_generate_for_site(
    site_id: UUID,
    *,
    offer_description: str,
    sender_name: str,
    sender_company: str,
    call_to_action: str = "Ответьте на сообщение — обсудим детали.",
    auto_qualify: bool = True,
    skip_unqualified: bool = False,
    target_query: str = "стоматология",
    target_description: str = "стоматологическая клиника",
) -> dict:
    site = get_site_context(site_id)
    if not site:
        raise ValueError(f"Сайт {site_id} не найден")
    if site.get("scrape_status") != "done":
        raise ValueError(f"Сайт {site_id} не обогащён — сначала POST /enrich/site/{site_id}")

    qualify_result = site.get("qualify")
    if auto_qualify and (qualify_result is None or qualify_result.get("score") is None):
        qualify_result = qualify_site(
            site,
            target_query=target_query or site.get("search_query") or "стоматология",
            target_description=target_description,
        )
        save_qualify_result(site_id, qualify_result)

    if skip_unqualified and qualify_result and not qualify_result.get("is_fit"):
        return {
            "site_id": str(site_id),
            "domain": site["domain"],
            "status": "skipped",
            "outreach_id": None,
            "message_subject": None,
            "message_text": None,
            "qualify": qualify_result,
            "error_message": "Сайт не прошёл квалификацию",
        }

    site_with_qualify = {**site, "qualify": qualify_result}
    message = generate_outreach_message(
        site_with_qualify,
        offer_description=offer_description,
        sender_name=sender_name,
        sender_company=sender_company,
        call_to_action=call_to_action,
    )
    outreach_id = save_outreach_message(
        site_id,
        message_subject=message.get("message_subject"),
        message_text=message["message_text"],
    )
    return {
        "site_id": str(site_id),
        "domain": site["domain"],
        "status": "done",
        "outreach_id": str(outreach_id),
        "message_subject": message.get("message_subject"),
        "message_text": message.get("message_text"),
        "personalization_hook": message.get("personalization_hook"),
        "qualify": qualify_result,
        "error_message": None,
    }


def update_outreach_status(
    outreach_id: UUID,
    *,
    sent_status: str,
    error_message: str | None = None,
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.site_outreach
                set
                  sent_status = %s,
                  error_message = %s,
                  sent_at = case when %s = 'sent' then now() else sent_at end
                where id = %s
                """,
                (sent_status, error_message, sent_status, outreach_id),
            )
        conn.commit()


def run_submit_for_site(
    site_id: UUID,
    *,
    sender_name: str,
    sender_phone: str,
    sender_email: str,
    outreach_id: UUID | None = None,
    headless: bool = True,
    dry_run: bool = True,
) -> dict:
    from app.scraper.form_submitter import submit_form_on_page
    from app.scraper.resilient import run_with_proxy_retries

    fill_plan = run_form_fill_for_site(
        site_id,
        sender_name=sender_name,
        sender_phone=sender_phone,
        sender_email=sender_email,
        outreach_id=outreach_id,
    )

    forms = list_site_forms(limit=1, site_id=site_id, submittable_only=True)
    if not forms:
        forms = list_site_forms(limit=1, site_id=site_id)
    form = forms[0] if forms else {}
    if form.get("has_captcha"):
        update_outreach_status(
            UUID(fill_plan["outreach_id"]),
            sent_status="skipped",
            error_message="Форма с капчей — автосабмит недоступен",
        )
        raise ValueError("Форма с капчей — автосабмит недоступен")

    try:
        submit_result, meta = run_with_proxy_retries(
            lambda page: submit_form_on_page(
                page,
                page_url=fill_plan["page_url"],
                form_selector=fill_plan["form_selector"],
                submit_selector=fill_plan.get("submit_selector"),
                fields=fill_plan["fields"],
                dry_run=dry_run,
            ),
            headless=headless,
            retry_on_any_error=True,
        )
    except Exception as exc:
        if not dry_run:
            update_outreach_status(
                UUID(fill_plan["outreach_id"]),
                sent_status="failed",
                error_message=str(exc),
            )
        raise

    if not dry_run and submit_result.get("submitted"):
        update_outreach_status(UUID(fill_plan["outreach_id"]), sent_status="sent")
    elif not dry_run:
        update_outreach_status(
            UUID(fill_plan["outreach_id"]),
            sent_status="failed",
            error_message="Submit не подтверждён",
        )

    return {
        **fill_plan,
        **submit_result,
        "scrape_source": meta.scrape_source,
        "proxy_server": meta.proxy_server,
    }


def run_form_fill_for_site(
    site_id: UUID,
    *,
    sender_name: str,
    sender_phone: str,
    sender_email: str,
    outreach_id: UUID | None = None,
) -> dict:
    site = get_site_context(site_id)
    if not site:
        raise ValueError(f"Сайт {site_id} не найден")

    outreach_rows = list_outreach(limit=1, site_id=site_id, sent_status="pending")
    if outreach_id:
        rows = list_outreach(limit=50, site_id=site_id)
        outreach_rows = [row for row in rows if row["id"] == str(outreach_id)]
    if not outreach_rows:
        raise ValueError(f"Нет pending outreach для сайта {site_id} — сначала POST /outreach/generate")

    outreach = outreach_rows[0]
    forms = list_site_forms(limit=1, site_id=site_id, submittable_only=True)
    if not forms:
        forms = list_site_forms(limit=1, site_id=site_id)
    if not forms:
        raise ValueError(f"Нет сохранённых форм для сайта {site_id} — сначала POST /forms/collect")

    form = forms[0]
    fill_plan = build_form_fill_plan(
        form["fields"],
        message_text=outreach["message_text"],
        message_subject=outreach.get("message_subject"),
        sender_name=sender_name,
        sender_phone=sender_phone,
        sender_email=sender_email,
    )
    return {
        "site_id": str(site_id),
        "domain": site["domain"],
        "outreach_id": outreach["id"],
        "form_id": form["id"],
        "page_url": form["page_url"],
        "form_selector": form["form_selector"],
        "submit_selector": form.get("submit_selector"),
        "fields": fill_plan,
    }
