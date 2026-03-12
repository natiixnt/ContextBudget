_CACHE: dict[str, list[str]] = {}

def get(key: str) -> list[str] | None:
    return _CACHE.get(key)

def put(key: str, value: list[str]) -> None:
    _CACHE[key] = value
