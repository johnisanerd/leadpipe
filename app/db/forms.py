import json
from uuid import UUID

from app.db.connection import get_connection


def fetch_sites_for_form_collection(
    *,
    limit: int = 10,
    site_id: UUID | None = None,
    only_without_forms: bool = True,
    only_with_contact_flag: bool = False,
) -> list[dict]:
    query = """
        select s.id, s.url, s.domain, s.title
        from public.sites s
        left join public.site_enrichments e on e.site_id = s.id
    """
    conditions: list[str] = []
    params: list = []

    if site_id:
        conditions.append("s.id = %s")
        params.append(site_id)
    if only_without_forms:
        conditions.append(
            """
            not exists (
              select 1 from public.site_forms f
              where f.site_id = s.id and f.scrape_status = 'discovered'
            )
            """
        )
    if only_with_contact_flag:
        conditions.append("coalesce(e.has_contact_form, false) = true")

    if conditions:
        query += " where " + " and ".join(conditions)

    query += " order by s.last_seen_at desc limit %s"
    params.append(limit)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        {"id": row[0], "url": row[1], "domain": row[2], "title": row[3]}
        for row in rows
    ]


def _dedupe_forms(forms: list[dict]) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    unique: list[dict] = []
    for form in forms:
        key = (form["page_url"], form["form_index"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(form)
    return unique


def replace_site_forms(site_id: UUID, forms: list[dict]) -> int:
    from app.scraper.form_collector import pick_best_form

    forms = _dedupe_forms(forms)
    best = pick_best_form(forms)
    forms = [best] if best else []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '120s'")
            cur.execute("delete from public.site_forms where site_id = %s", (site_id,))
        conn.commit()

    if not forms:
        return 0

    saved = 0
    for form in forms:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '120s'")
                cur.execute(
                    """
                    insert into public.site_forms (
                      site_id, page_url, form_index, form_action, form_method,
                      form_selector, submit_selector, fields, form_type, form_score,
                      has_captcha, in_iframe, is_submittable, scrape_status, error_message
                    )
                    values (
                      %s, %s, %s, %s, %s,
                      %s, %s, %s::jsonb, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    on conflict (site_id, page_url, form_index) do update set
                      form_action = excluded.form_action,
                      form_method = excluded.form_method,
                      form_selector = excluded.form_selector,
                      submit_selector = excluded.submit_selector,
                      fields = excluded.fields,
                      form_type = excluded.form_type,
                      form_score = excluded.form_score,
                      has_captcha = excluded.has_captcha,
                      in_iframe = excluded.in_iframe,
                      is_submittable = excluded.is_submittable,
                      scrape_status = excluded.scrape_status,
                      error_message = excluded.error_message,
                      updated_at = now()
                    """,
                    (
                        site_id,
                        form["page_url"],
                        form["form_index"],
                        form.get("form_action"),
                        form.get("form_method", "post"),
                        form["form_selector"],
                        form.get("submit_selector"),
                        json.dumps(form.get("fields", [])),
                        form.get("form_type", "unknown"),
                        form.get("form_score", 0),
                        form.get("has_captcha", False),
                        form.get("in_iframe", False),
                        form.get("is_submittable", True),
                        form.get("scrape_status", "discovered"),
                        form.get("error_message"),
                    ),
                )
            conn.commit()
        saved += 1
    return saved


def list_site_forms(
    *,
    limit: int = 50,
    offset: int = 0,
    site_id: UUID | None = None,
    submittable_only: bool = False,
) -> list[dict]:
    query = """
        select
          f.id, f.site_id, s.url, s.domain,
          f.page_url, f.form_index, f.form_action, f.form_method,
          f.form_selector, f.submit_selector, f.fields,
          f.form_type, f.form_score, f.has_captcha, f.in_iframe,
          f.is_submittable, f.scrape_status, f.error_message,
          f.discovered_at
        from public.site_forms f
        join public.sites s on s.id = f.site_id
    """
    params: list = []
    conditions: list[str] = []

    if site_id:
        conditions.append("f.site_id = %s")
        params.append(site_id)
    if submittable_only:
        conditions.append("f.is_submittable = true and f.scrape_status = 'discovered'")

    if conditions:
        query += " where " + " and ".join(conditions)

    query += " order by f.form_score desc, f.discovered_at desc limit %s offset %s"
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
            "page_url": row[4],
            "form_index": row[5],
            "form_action": row[6],
            "form_method": row[7],
            "form_selector": row[8],
            "submit_selector": row[9],
            "fields": row[10] if isinstance(row[10], list) else json.loads(row[10] or "[]"),
            "form_type": row[11],
            "form_score": row[12],
            "has_captcha": row[13],
            "in_iframe": row[14],
            "is_submittable": row[15],
            "scrape_status": row[16],
            "error_message": row[17],
            "discovered_at": row[18].isoformat() if row[18] else None,
        }
        for row in rows
    ]


def run_form_collection_batch(
    *,
    limit: int = 10,
    headless: bool = True,
    only_without_forms: bool = True,
    only_with_contact_flag: bool = False,
) -> dict:
    sites = fetch_sites_for_form_collection(
        limit=limit,
        only_without_forms=only_without_forms,
        only_with_contact_flag=only_with_contact_flag,
    )
    if not sites:
        return {"processed": 0, "forms_found": 0, "results": []}

    from app.scraper.form_collector import collect_forms_for_sites

    collected = collect_forms_for_sites(sites, headless=headless)
    total_forms = 0
    results = []

    for item in collected:
        saved_count = 0
        if item["status"] == "done":
            saved_count = replace_site_forms(UUID(item["site_id"]), item["forms"])
            total_forms += saved_count
        results.append(
            {
                "site_id": item["site_id"],
                "url": item["url"],
                "domain": item["domain"],
                "status": item["status"],
                "forms_found": len(item.get("forms", [])),
                "forms_saved": saved_count,
                "error_message": item.get("error_message"),
            }
        )

    return {
        "processed": len(results),
        "forms_found": total_forms,
        "results": results,
    }


def run_form_collection_for_site(site_id: UUID, *, headless: bool = True) -> dict:
    sites = fetch_sites_for_form_collection(
        limit=1,
        site_id=site_id,
        only_without_forms=False,
    )
    if not sites:
        raise ValueError(f"Сайт {site_id} не найден")

    from app.scraper.form_collector import collect_forms_for_sites

    collected = collect_forms_for_sites(sites, headless=headless)
    if not collected:
        raise RuntimeError("Сбор форм не вернул результат")

    item = collected[0]
    saved_count = 0
    if item["status"] == "done":
        saved_count = replace_site_forms(site_id, item["forms"])

    return {
        **item,
        "forms_saved": saved_count,
    }
