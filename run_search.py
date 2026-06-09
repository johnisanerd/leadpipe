"""CLI entrypoint for quick local experiments."""

import argparse
import json

from app.scraper.google import scrape_google_serp


def main() -> None:
    parser = argparse.ArgumentParser(description="Google SERP scraper via Playwright")
    parser.add_argument("query", help="Поисковый запрос")
    parser.add_argument("city", help="Город")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--headed", action="store_true", help="Показать браузер")
    args = parser.parse_args()

    search_phrase, results, meta = scrape_google_serp(
        args.query,
        args.city,
        max_pages=args.max_pages,
        headless=not args.headed,
    )

    print(f"Search: {search_phrase}")
    print(f"Source: {meta.scrape_source}, proxy: {meta.proxy_server}")
    print(f"Found: {len(results)}")
    print(json.dumps([item.model_dump() for item in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
