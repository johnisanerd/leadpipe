from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import PROXY_LIST, PROXY_USE_DIRECT


@dataclass(frozen=True)
class ProxyConfig:
    server: str
    username: str | None = None
    password: str | None = None

    def to_playwright(self) -> dict[str, str]:
        payload: dict[str, str] = {"server": self.server}
        if self.username:
            payload["username"] = self.username
        if self.password:
            payload["password"] = self.password
        return payload

    def display(self) -> str:
        if self.username:
            parsed = urlparse(self.server)
            return f"{parsed.scheme}://{self.username}:***@{parsed.hostname}:{parsed.port or ''}"
        return self.server


def parse_proxy(raw: str) -> ProxyConfig:
    value = raw.strip()
    if not value:
        raise ValueError("Пустая строка прокси")

    if "://" not in value:
        value = f"http://{value}"

    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError(f"Некорректный прокси: {raw}")

    port = f":{parsed.port}" if parsed.port else ""
    server = f"{parsed.scheme}://{parsed.hostname}{port}"
    return ProxyConfig(
        server=server,
        username=parsed.username or None,
        password=parsed.password or None,
    )


def load_proxy_configs() -> list[ProxyConfig]:
    proxies: list[ProxyConfig] = []
    for chunk in PROXY_LIST.replace("\n", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        proxies.append(parse_proxy(item))
    return proxies


def build_proxy_attempts() -> list[ProxyConfig | None]:
    attempts: list[ProxyConfig | None] = []
    if PROXY_USE_DIRECT:
        attempts.append(None)
    attempts.extend(load_proxy_configs())
    return attempts
