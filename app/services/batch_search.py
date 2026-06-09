from collections.abc import Callable

from app.db.repository import (
    save_google_search_results,
    save_yandex_maps_search_results,
    save_yandex_search_results,
)
from app.utils.cities import resolve_cities

SAVE_FN_BY_SOURCE = {
    "google": save_google_search_results,
    "yandex": save_yandex_search_results,
    "yandex_maps": save_yandex_maps_search_results,
}


def run_batch_search(
    *,
    source: str,
    scrape_batch_fn: Callable,
    query: str,
    cities: list[str] | None,
    use_all_cities: bool,
    max_pages: int,
    headless: bool,
    save_to_db: bool,
    stop_on_block: bool,
) -> dict:
    if source not in SAVE_FN_BY_SOURCE:
        raise ValueError(f"Неизвестный источник: {source}")

    save_fn = SAVE_FN_BY_SOURCE[source]
    selected_cities = resolve_cities(cities=cities, use_all_cities=use_all_cities)
    scraped = scrape_batch_fn(
        query,
        selected_cities,
        max_pages=max_pages,
        headless=headless,
        stop_on_block=stop_on_block,
    )

    city_summaries = []
    total_results = 0
    total_saved = 0
    total_new_sites = 0
    completed = 0
    blocked = 0
    failed = 0

    for item in scraped:
        status = item["status"]
        if status == "completed":
            completed += 1
        elif status == "blocked":
            blocked += 1
        else:
            failed += 1

        total_results += item["total_results"]
        saved_meta = None

        if save_to_db:
            saved_meta = save_fn(
                query=query,
                city=item["city"],
                search_phrase=item["search_phrase"],
                max_pages=max_pages,
                pages_scraped=item["pages_scraped"],
                results=item["results"],
                status=status,
                error_message=item["error_message"],
            )
            total_saved += saved_meta["saved_count"]
            total_new_sites += saved_meta["new_sites_count"]

        city_summaries.append(
            {
                "city": item["city"],
                "search_phrase": item["search_phrase"],
                "status": status,
                "total_results": item["total_results"],
                "pages_scraped": item["pages_scraped"],
                "error_message": item["error_message"],
                "search_run_id": saved_meta["search_run_id"] if saved_meta else None,
                "saved_count": saved_meta["saved_count"] if saved_meta else None,
                "new_sites_count": saved_meta["new_sites_count"] if saved_meta else None,
                "scrape_source": item.get("scrape_source"),
                "proxy_server": item.get("proxy_server"),
                "results": item["results"] if not save_to_db else [],
            }
        )

    return {
        "source": source,
        "query": query,
        "cities_requested": len(selected_cities),
        "cities_processed": len(city_summaries),
        "completed": completed,
        "blocked": blocked,
        "failed": failed,
        "total_results": total_results,
        "total_saved": total_saved,
        "total_new_sites": total_new_sites,
        "db_saved": save_to_db,
        "cities": city_summaries,
    }


def run_google_batch_search(**kwargs) -> dict:
    from app.scraper.google import scrape_google_serp_batch

    return run_batch_search(source="google", scrape_batch_fn=scrape_google_serp_batch, **kwargs)


def run_yandex_batch_search(**kwargs) -> dict:
    from app.scraper.yandex import scrape_yandex_serp_batch

    return run_batch_search(source="yandex", scrape_batch_fn=scrape_yandex_serp_batch, **kwargs)


def run_yandex_maps_batch_search(**kwargs) -> dict:
    from app.scraper.yandex_maps import scrape_yandex_maps_batch

    return run_batch_search(source="yandex_maps", scrape_batch_fn=scrape_yandex_maps_batch, **kwargs)
