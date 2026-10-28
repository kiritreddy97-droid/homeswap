import os
from dataclasses import dataclass, field

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"


def _csv(name: str) -> frozenset[str]:
    return frozenset(x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.getenv("ENV", "dev"))
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", DEV_JWT_SECRET))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("JWT_TTL_MINUTES", "1440")))
    otp_sender: str = field(default_factory=lambda: os.getenv("OTP_SENDER", "console"))
    otp_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("OTP_TTL_MINUTES", "10")))
    # Contacts (email or +phone) that become admins on login. Bootstraps the first admin without a seed script.
    admin_contacts: frozenset[str] = field(default_factory=lambda: _csv("ADMIN_CONTACTS"))
    # Shared secret for the KYC provider webhook; the webhook is disabled while unset.
    kyc_webhook_secret: str = field(default_factory=lambda: os.getenv("KYC_WEBHOOK_SECRET", ""))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "120")))
    auth_rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "10")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def validate(self) -> None:
        """Fail fast on unsafe production configuration."""
        if self.env != "production":
            return
        if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters in production")
        if self.otp_sender == "console":
            raise RuntimeError("OTP_SENDER=console would log login codes; configure a real OTP sender in production")


def get_settings() -> Settings:
    return Settings()
