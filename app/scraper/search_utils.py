def build_search_phrase(query: str, city: str) -> str:
    return f"{query.strip()} {city.strip()}"
