"""Authenticated API contract tests with no external identity provider."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fi_intel.api.app import create_app
from fi_intel.api.auth import (
    AuthenticationError,
    Authenticator,
    RequestPrincipal,
    VerifiedToken,
)
from fi_intel.api.models import SignalView
from fi_intel.api.service import InMemoryAnalystService
from fi_intel.retrieval.entitlement import Principal, Side

NOW = datetime(2025, 1, 2, 10, tzinfo=UTC)


class _Verifier:
    async def verify(self, credential: str) -> VerifiedToken:
        if credential == "invalid":
            raise AuthenticationError("invalid bearer token")
        return VerifiedToken(subject=credential, issuer="https://issuer.test")


class _Directory:
    def __init__(self) -> None:
        self.identities = {
            "alice": RequestPrincipal(
                subject="alice",
                principal=Principal(
                    principal_id="directory-alice",
                    entitlement_group="fi-public",
                    side=Side.PUBLIC,
                ),
                desks=frozenset({"fi_gcc"}),
                roles=frozenset({"analyst", "reviewer"}),
                purposes=frozenset({"market_intelligence"}),
            )
        }

    async def resolve(self, subject: str) -> RequestPrincipal | None:
        return self.identities.get(subject)

    async def close(self) -> None:
        return None


def _client() -> tuple[TestClient, InMemoryAnalystService]:
    service = InMemoryAnalystService(
        signals=(
            SignalView(
                signal_id="signal-1",
                pattern_id="programme",
                pattern_version="2",
                entity_id="entity-1",
                entity_name="Example Bank",
                desk="fi_gcc",
                status="confirmed",
                score=0.91,
                as_of=NOW,
                changed_at=NOW,
                assertion_ids=("assertion-1",),
            ),
        )
    )
    app = create_app(Authenticator(_Verifier(), _Directory()), service)
    return TestClient(app), service


def _headers(subject: str = "alice") -> dict[str, str]:
    return {"Authorization": f"Bearer {subject}"}


def test_health_is_minimal_and_request_ids_are_always_returned() -> None:
    client, _ = _client()
    response = client.get("/health/live", headers={"x-request-id": "request-1"})
    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    assert response.headers["x-request-id"] == "request-1"
    assert response.headers["cache-control"] == "no-store"


def test_signal_access_requires_verified_and_directory_assigned_identity() -> None:
    client, _ = _client()
    assert client.get("/v1/signals", params={"desk": "fi_gcc"}).status_code == 401
    assert (
        client.get(
            "/v1/signals", params={"desk": "fi_gcc"}, headers=_headers("invalid")
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/v1/signals", params={"desk": "fi_gcc"}, headers=_headers("unknown")
        ).status_code
        == 403
    )


def test_directory_attributes_control_desk_and_feedback_identity() -> None:
    client, service = _client()
    denied = client.get(
        "/v1/signals",
        params={"desk": "private_mna", "group": "private-admin", "side": "private"},
        headers=_headers(),
    )
    assert denied.status_code == 403

    allowed = client.get("/v1/signals", params={"desk": "fi_gcc"}, headers=_headers())
    assert allowed.status_code == 200
    assert [item["signal_id"] for item in allowed.json()] == ["signal-1"]

    feedback = client.post(
        "/v1/signals/signal-1/feedback",
        headers=_headers(),
        json={"verdict": "useful", "reason": "Timely and actionable."},
    )
    assert feedback.status_code == 201
    assert service.feedback_principals == ["directory-alice"]


def test_request_contracts_forbid_identity_overrides() -> None:
    client, _ = _client()
    response = client.post(
        "/v1/briefs",
        headers=_headers(),
        json={
            "desk": "fi_gcc",
            "as_of": NOW.isoformat(),
            "entitlement_group": "private-admin",
            "side": "private",
        },
    )
    assert response.status_code == 422

    schema = client.get("/openapi.json").json()
    parameters = schema["paths"]["/v1/signals"]["get"]["parameters"]
    assert {parameter["name"] for parameter in parameters} == {"desk", "status", "limit"}
