import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.forms import fetch_sites_for_form_collection, replace_site_forms, list_site_forms
from app.scraper.form_collector import collect_forms_for_sites

sites = fetch_sites_for_form_collection(limit=1, only_without_forms=True)
print("site:", sites[0]["domain"])

collected = collect_forms_for_sites(sites, headless=True)
item = collected[0]
print("status:", item["status"])
forms = item.get("forms", [])
print("forms scraped:", len(forms))
for i, form in enumerate(forms[:5]):
    payload = json.dumps(form)
    print(
        i,
        form["page_url"][:80],
        form["form_type"],
        "fields",
        len(form.get("fields", [])),
        "json_bytes",
        len(payload),
    )
    if item.get("error_message"):
        print("error:", item["error_message"])

if item["status"] == "done" and forms:
    from app.scraper.form_collector import pick_best_form

    best = pick_best_form(forms)
    print("best form:", best["page_url"][:80] if best else None, best.get("form_score") if best else None)
    print("saving...")
    saved = replace_site_forms(UUID(item["site_id"]), forms)
    print("saved:", saved)

print("in db:", len(list_site_forms(limit=10)))
