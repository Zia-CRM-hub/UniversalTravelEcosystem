from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-secret-change-me-in-production-0000"
KNOWN_PROVIDERS = {"demo", "duffel", "amadeus"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    database_url: str = "sqlite+aiosqlite:///./ute.db"
    # Dev/test convenience; production schema is managed by `alembic upgrade head`.
    auto_create_tables: bool | None = None
    secret_key: SecretStr = SecretStr(_DEV_SECRET)
    token_minutes: int = 60 * 24 * 7
    offer_ttl_minutes: int = 15
    cors_origins: str = ""  # comma separated; native mobile apps don't need CORS
    rate_limit_per_minute: int = 120

    # --- supply providers ---------------------------------------------------
    providers: str = "demo"  # comma separated: demo,duffel,amadeus
    allow_demo_in_prod: bool = False
    provider_timeout_s: float = 20
    provider_retries: int = 2
    breaker_threshold: int = 5
    breaker_reset_s: float = 30

    duffel_token: SecretStr | None = None
    duffel_base_url: str = "https://api.duffel.com"

    amadeus_client_id: str | None = None
    amadeus_client_secret: SecretStr | None = None
    # Deliberately no default: Amadeus Self-Service was decommissioned on
    # 2026-07-17, so set this to the host your Enterprise contract gives you.
    amadeus_base_url: str | None = None
    amadeus_currency: str | None = None

    # --- places -------------------------------------------------------------
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    user_agent: str = "universal-travel-ecosystem/0.1 (self-hosted)"

    # --- payments -----------------------------------------------------------
    payment_gateway: Literal["demo", "razorpay"] = "demo"
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_base_url: str = "https://api.razorpay.com"

    # --- conversational agent (any OpenAI-compatible server: Ollama, vLLM, llama.cpp) ---
    llm_base_url: str | None = None  # e.g. http://ollama:11434/v1
    llm_model: str = "llama3.1"
    llm_api_key: SecretStr | None = None
    llm_timeout_s: float = 60
    agent_max_steps: int = 5
    web_base_url: str = "http://localhost:5173"  # used for hand-off links in chat

    # --- WhatsApp Cloud API (Meta) -------------------------------------------
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_graph_url: str = "https://graph.facebook.com/v21.0"

    @property
    def enabled_providers(self) -> list[str]:
        return [p.strip().lower() for p in self.providers.split(",") if p.strip()]

    @property
    def create_tables(self) -> bool:
        return self.auto_create_tables if self.auto_create_tables is not None else self.app_env == "dev"

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_verify_token and self.whatsapp_app_secret
                    and self.whatsapp_access_token and self.whatsapp_phone_number_id)

    def validate_runtime(self) -> None:
        """Fail fast on unsafe or incomplete configuration."""
        errs: list[str] = []
        names = self.enabled_providers
        if not names:
            errs.append("PROVIDERS is empty")
        for n in names:
            if n not in KNOWN_PROVIDERS:
                errs.append(f"unknown provider '{n}' (known: {sorted(KNOWN_PROVIDERS)})")
        if "duffel" in names and not self.duffel_token:
            errs.append("DUFFEL_TOKEN is required when duffel is enabled")
        if "amadeus" in names and not (self.amadeus_client_id and self.amadeus_client_secret and self.amadeus_base_url):
            errs.append("AMADEUS_CLIENT_ID, AMADEUS_CLIENT_SECRET and AMADEUS_BASE_URL are required when amadeus is enabled")
        if self.payment_gateway == "razorpay" and not (self.razorpay_key_id and self.razorpay_key_secret):
            errs.append("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required when PAYMENT_GATEWAY=razorpay")
        if self.app_env == "prod":
            if self.payment_gateway == "demo" and not self.allow_demo_in_prod:
                errs.append("PAYMENT_GATEWAY=demo takes no real payment; set ALLOW_DEMO_IN_PROD=true to override")
            key = self.secret_key.get_secret_value()
            if key == _DEV_SECRET or len(key) < 32:
                errs.append("SECRET_KEY must be set to a unique value of 32+ characters in prod")
            if "demo" in names and not self.allow_demo_in_prod:
                errs.append("the demo provider produces fake bookings; set ALLOW_DEMO_IN_PROD=true to override")
        if errs:
            raise RuntimeError("Invalid configuration: " + "; ".join(errs))


settings = Settings()
