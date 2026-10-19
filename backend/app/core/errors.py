class DomainError(Exception):
    """Business-rule failure that maps cleanly to an HTTP response."""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code, self.code, self.detail = status_code, code, detail


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, *, status: int | None = None,
                 retryable: bool = False, client_error: bool = False):
        super().__init__(f"[{provider}] {message}")
        self.provider, self.message, self.status = provider, message, status
        self.retryable, self.client_error = retryable, client_error


class ProviderUnavailable(ProviderError):
    """Circuit breaker is open for this provider."""


class PriceChanged(ProviderError):
    """The supplier re-priced the offer between search and booking."""


class PaymentError(Exception):
    pass
