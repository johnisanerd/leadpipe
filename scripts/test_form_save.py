import json

from app.db.connection import get_connection
from app.db.forms import fetch_sites_for_form_collection, list_site_forms, run_form_collection_batch

sites = fetch_sites_for_form_collection(limit=1, only_without_forms=True)
if not sites:
    print("No sites pending")
    raise SystemExit(0)

site_id = sites[0]["id"]
print("test insert for", sites[0]["domain"])

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '120s'")
        cur.execute(
            """
            insert into public.site_forms (
              site_id, page_url, form_index, form_selector, fields
            ) values (%s, %s, 0, %s, %s::jsonb)
            returning id
            """,
            (
                site_id,
                "https://example.com/contacts",
                "form:nth-of-type(1)",
                json.dumps([{"purpose": "message"}]),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        print("insert ok:", row[0])
        cur.execute("delete from public.site_forms where id = %s", (row[0],))
        conn.commit()
        print("cleanup ok")

print("--- running collection ---")
result = run_form_collection_batch(limit=1, headless=True, only_without_forms=True)
print("processed:", result["processed"])
print("forms_found:", result["forms_found"])
for item in result["results"]:
    print("site:", item["domain"], "status:", item["status"], "saved:", item["forms_saved"])
    if item.get("error_message"):
        print("error:", item["error_message"])

forms = list_site_forms(limit=5)
print("forms in DB:", len(forms))
for f in forms:
    print("-", f["domain"], f["page_url"], f["form_type"], "score", f["form_score"])
    print("  purposes:", [x["purpose"] for x in f["fields"]])
