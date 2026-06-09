import json
from functools import lru_cache

from app.config import BASE_DIR, MAX_BATCH_CITIES

CITIES_FILE = BASE_DIR / "data" / "cities.json"


@lru_cache
def load_all_cities() -> list[dict]:
    if not CITIES_FILE.exists():
        raise FileNotFoundError(f"Файл городов не найден: {CITIES_FILE}")
    with CITIES_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("cities.json должен содержать массив объектов")
    return data


def list_city_names() -> list[str]:
    return [item["name"] for item in load_all_cities()]


def resolve_cities(
    *,
    cities: list[str] | None,
    use_all_cities: bool,
) -> list[str]:
    catalog = {item["name"].casefold(): item["name"] for item in load_all_cities()}

    if use_all_cities:
        selected = list_city_names()
    elif cities:
        selected = []
        for city in cities:
            normalized = city.strip()
            if not normalized:
                continue
            canonical = catalog.get(normalized.casefold())
            if canonical is None:
                raise ValueError(f"Город не найден в справочнике: {city}")
            selected.append(canonical)
    else:
        raise ValueError("Укажите cities или установите use_all_cities=true")

    if not selected:
        raise ValueError("Список городов пуст")

    if len(selected) > MAX_BATCH_CITIES:
        raise ValueError(f"Максимум {MAX_BATCH_CITIES} городов за один batch-запрос")

    # preserve order, remove duplicates
    seen: set[str] = set()
    unique: list[str] = []
    for city in selected:
        key = city.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(city)
    return unique
