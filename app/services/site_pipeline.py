from __future__ import annotations

from uuid import UUID

from app.db.enrichment import run_enrichment_for_site
from app.db.forms import run_form_collection_for_site
from app.db.outreach import run_generate_for_site, run_submit_for_site


def run_site_outreach_pipeline(
    site_id: UUID,
    *,
    headless: bool = True,
    skip_enrich_if_done: bool = True,
    offer_description: str,
    sender_name: str,
    sender_company: str,
    sender_phone: str = "",
    sender_email: str = "",
    call_to_action: str = "Ответьте на сообщение — обсудим детали.",
    auto_qualify: bool = True,
    skip_unqualified: bool = True,
    target_query: str = "стоматология",
    target_description: str = "стоматологическая клиника",
    dry_run_submit: bool = True,
) -> dict:
    steps: list[dict] = []

    try:
        from app.db.outreach import get_site_context

        site = get_site_context(site_id)
        if not site:
            raise ValueError(f"Сайт {site_id} не найден")

        if skip_enrich_if_done and site.get("scrape_status") == "done":
            steps.append({"step": "enrich", "status": "skipped", "detail": "already done"})
        else:
            enrich = run_enrichment_for_site(site_id, headless=headless)
            steps.append(
                {
                    "step": "enrich",
                    "status": enrich.get("status", "done"),
                    "emails": len(enrich.get("emails", [])),
                    "phones": len(enrich.get("phones", [])),
                }
            )
            if enrich.get("status") == "failed":
                return _pipeline_result(site_id, site["domain"], "failed", steps, enrich.get("error_message"))

        forms = run_form_collection_for_site(site_id, headless=headless)
        steps.append(
            {
                "step": "forms",
                "status": forms.get("status", "done"),
                "forms_saved": forms.get("forms_saved", 0),
            }
        )
        if forms.get("status") == "failed":
            return _pipeline_result(site_id, site["domain"], "failed", steps, forms.get("error_message"))

        outreach = run_generate_for_site(
            site_id,
            offer_description=offer_description,
            sender_name=sender_name,
            sender_company=sender_company,
            call_to_action=call_to_action,
            auto_qualify=auto_qualify,
            skip_unqualified=skip_unqualified,
            target_query=target_query,
            target_description=target_description,
        )
        steps.append(
            {
                "step": "outreach",
                "status": outreach.get("status", "done"),
                "outreach_id": outreach.get("outreach_id"),
            }
        )
        if outreach.get("status") in {"skipped", "failed"}:
            return _pipeline_result(
                site_id,
                site["domain"],
                outreach["status"],
                steps,
                outreach.get("error_message"),
                outreach_id=outreach.get("outreach_id"),
            )

        submit = run_submit_for_site(
            site_id,
            sender_name=sender_name,
            sender_phone=sender_phone,
            sender_email=sender_email,
            outreach_id=UUID(outreach["outreach_id"]) if outreach.get("outreach_id") else None,
            headless=headless,
            dry_run=dry_run_submit,
        )
        steps.append(
            {
                "step": "submit",
                "status": "done",
                "dry_run": submit.get("dry_run", True),
                "submitted": submit.get("submitted", False),
                "fields_filled": submit.get("fields_filled", 0),
            }
        )

        final_status = "dry_run" if dry_run_submit else "done"
        return _pipeline_result(
            site_id,
            site["domain"],
            final_status,
            steps,
            outreach_id=outreach.get("outreach_id"),
            submit=submit,
        )
    except Exception as exc:
        steps.append({"step": "pipeline", "status": "failed", "error": str(exc)})
        domain = site["domain"] if "site" in locals() and site else str(site_id)
        return _pipeline_result(site_id, domain, "failed", steps, str(exc))


def _pipeline_result(
    site_id: UUID,
    domain: str,
    status: str,
    steps: list[dict],
    error_message: str | None = None,
    *,
    outreach_id: str | None = None,
    submit: dict | None = None,
) -> dict:
    return {
        "site_id": str(site_id),
        "domain": domain,
        "status": status,
        "steps": steps,
        "outreach_id": outreach_id,
        "error_message": error_message,
        "submit": submit,
    }
