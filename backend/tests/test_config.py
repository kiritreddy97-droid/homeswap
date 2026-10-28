import pytest

from app import otp
from app.config import DEV_JWT_SECRET, Settings


def test_production_refuses_default_secret_and_console_otp(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings().validate()
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    with pytest.raises(RuntimeError, match="OTP_SENDER"):
        Settings().validate()


def test_dev_defaults_are_allowed(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    s = Settings()
    s.validate()
    assert s.jwt_secret == DEV_JWT_SECRET


def test_admin_contacts_parsing(monkeypatch):
    monkeypatch.setenv("ADMIN_CONTACTS", " A@x.in , +919999999999 ")
    assert Settings().admin_contacts == {"a@x.in", "+919999999999"}


def test_unknown_sender_fails_loudly():
    with pytest.raises(RuntimeError):
        otp.get_sender("sms-provider-not-wired")


def test_console_sender_logs_code(caplog):
    with caplog.at_level("WARNING", logger="homeswap.otp"):
        otp.get_sender("console").send("a@x.in", "123456")
    assert "123456" in caplog.text


@pytest.mark.parametrize("raw", ["", "12345", "a@b", "+0123456789", "hello"])
def test_bad_identifiers(raw):
    with pytest.raises(ValueError):
        otp.normalise_identifier(raw)
