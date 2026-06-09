from uuid import UUID

from app.db.connection import get_connection
from app.models import SearchResultItem
from app.utils.domain import extract_domain


def _upsert_search_query(cur, query: str, city: str) -> UUID:
    cur.execute(
        """
        insert into public.search_queries (query, city)
        values (%s, %s)
        on conflict (query, city) do update
          set is_active = true
        returning id
        """,
        (query.strip(), city.strip()),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Не удалось сохранить search_queries")
    return row[0]


def _create_search_run(
    cur,
    *,
    source: str,
    search_query_id: UUID,
    search_phrase: str,
    city: str,
    max_pages: int,
    pages_scraped: int,
    total_results: int,
    status: str,
    error_message: str | None = None,
) -> UUID:
    cur.execute(
        """
        insert into public.search_runs (
          search_query_id, source, search_phrase, city,
          max_pages, pages_scraped, total_results, status, error_message, finished_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        returning id
        """,
        (
            search_query_id,
            source,
            search_phrase,
            city.strip(),
            max_pages,
            pages_scraped,
            total_results,
            status,
            error_message,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Не удалось сохранить search_runs")
    return row[0]


def _upsert_site(cur, url: str, title: str | None) -> tuple[UUID, bool]:
    domain = extract_domain(url)
    cur.execute(
        """
        insert into public.sites (url, domain, title, last_seen_at)
        values (%s, %s, %s, now())
        on conflict (domain) do update
          set url = excluded.url,
              title = coalesce(excluded.title, public.sites.title),
              last_seen_at = now()
        returning id, (xmax = 0) as is_new
        """,
        (url, domain, title),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Не удалось сохранить site: {url}")
    return row[0], bool(row[1])


def _insert_serp_result(
    cur,
    *,
    search_run_id: UUID,
    site_id: UUID,
    position: int,
    page: int,
    title: str | None,
    snippet: str | None,
) -> None:
    cur.execute(
        """
        insert into public.serp_results (
          search_run_id, site_id, position, page, title, snippet
        )
        values (%s, %s, %s, %s, %s, %s)
        on conflict (search_run_id, site_id, position) do update
          set title = excluded.title,
              snippet = excluded.snippet,
              page = excluded.page
        """,
        (search_run_id, site_id, position, page, title, snippet),
    )


def _ensure_enrichment_row(cur, site_id: UUID) -> None:
    cur.execute(
        """
        insert into public.site_enrichments (site_id)
        values (%s)
        on conflict (site_id) do nothing
        """,
        (site_id,),
    )


def save_search_results(
    *,
    source: str,
    query: str,
    city: str,
    search_phrase: str,
    max_pages: int,
    pages_scraped: int,
    results: list[SearchResultItem],
    status: str = "completed",
    error_message: str | None = None,
) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            search_query_id = _upsert_search_query(cur, query, city)
            search_run_id = _create_search_run(
                cur,
                source=source,
                search_query_id=search_query_id,
                search_phrase=search_phrase,
                city=city,
                max_pages=max_pages,
                pages_scraped=pages_scraped,
                total_results=len(results),
                status=status,
                error_message=error_message,
            )

            new_sites = 0
            if status == "completed":
                for item in results:
                    site_id, is_new = _upsert_site(cur, item.url, item.title)
                    if is_new:
                        new_sites += 1
                    _insert_serp_result(
                        cur,
                        search_run_id=search_run_id,
                        site_id=site_id,
                        position=item.position,
                        page=item.page,
                        title=item.title,
                        snippet=item.snippet,
                    )
                    _ensure_enrichment_row(cur, site_id)

            conn.commit()

    return {
        "search_query_id": str(search_query_id),
        "search_run_id": str(search_run_id),
        "saved_count": len(results),
        "new_sites_count": new_sites,
    }


def save_google_search_results(
    *,
    query: str,
    city: str,
    search_phrase: str,
    max_pages: int,
    pages_scraped: int,
    results: list[SearchResultItem],
    status: str = "completed",
    error_message: str | None = None,
) -> dict:
    return save_search_results(
        source="google",
        query=query,
        city=city,
        search_phrase=search_phrase,
        max_pages=max_pages,
        pages_scraped=pages_scraped,
        results=results,
        status=status,
        error_message=error_message,
    )


def save_yandex_search_results(
    *,
    query: str,
    city: str,
    search_phrase: str,
    max_pages: int,
    pages_scraped: int,
    results: list[SearchResultItem],
    status: str = "completed",
    error_message: str | None = None,
) -> dict:
    return save_search_results(
        source="yandex",
        query=query,
        city=city,
        search_phrase=search_phrase,
        max_pages=max_pages,
        pages_scraped=pages_scraped,
        results=results,
        status=status,
        error_message=error_message,
    )


def save_yandex_maps_search_results(
    *,
    query: str,
    city: str,
    search_phrase: str,
    max_pages: int,
    pages_scraped: int,
    results: list[SearchResultItem],
    status: str = "completed",
    error_message: str | None = None,
) -> dict:
    return save_search_results(
        source="yandex_maps",
        query=query,
        city=city,
        search_phrase=search_phrase,
        max_pages=max_pages,
        pages_scraped=pages_scraped,
        results=results,
        status=status,
        error_message=error_message,
    )
