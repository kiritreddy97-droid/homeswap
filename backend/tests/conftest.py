import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "100000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import main, models  # noqa: E402
from app.db import Base, get_db  # noqa: E402


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


class CapturingSender:
    """Stands in for an SMS/email provider and remembers the last code sent to each identifier."""

    def __init__(self):
        self.codes: dict[str, str] = {}

    def send(self, identifier: str, code: str) -> None:
        self.codes[identifier] = code


@pytest.fixture
def sender():
    return CapturingSender()


@pytest.fixture
def client(engine, sender):
    factory = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[get_db] = override_db
    main.app.dependency_overrides[main.get_otp_sender] = lambda: sender
    main.limiter.hits.clear()
    main.auth_limiter.hits.clear()
    with TestClient(main.app) as c:
        c.session_factory = factory
        yield c
    main.app.dependency_overrides.clear()


class Member:
    def __init__(self, client, sender, identifier: str, name: str):
        self.client, self.identifier = client, identifier
        assert client.post("/auth/otp/request", json={"identifier": identifier}).status_code == 202
        code = sender.codes[identifier]
        r = client.post("/auth/otp/verify", json={"identifier": identifier, "code": code, "name": name})
        assert r.status_code == 200, r.text
        self.id = r.json()["user"]["id"]
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def verify_kyc(self):
        with self.client.session_factory() as s:
            u = s.get(models.User, self.id)
            u.id_verified = u.membership_active = True
            s.commit()
        return self

    def get(self, url, **kw):
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url, json=None):
        return self.client.post(url, headers=self.headers, json=json)


@pytest.fixture
def make_member(client, sender):
    def make(identifier="asha@example.in", name="Asha", verified=True):
        m = Member(client, sender, identifier, name)
        return m.verify_kyc() if verified else m

    return make


@pytest.fixture
def admin(client, sender, monkeypatch):
    monkeypatch.setenv("ADMIN_CONTACTS", "admin@homeswap.in")
    return Member(client, sender, "admin@homeswap.in", "Admin")
