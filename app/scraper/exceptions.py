class ScrapeBlockedError(RuntimeError):
    """CAPTCHA или блокировка поисковика / карты."""


class ScrapeProxyExhaustedError(ScrapeBlockedError):
    """Все прокси исчерпаны, запрос заблокирован."""
