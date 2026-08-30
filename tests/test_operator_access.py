"""Configured OIDC principal provisioning without database surgery."""

from types import SimpleNamespace

import pytest

from fi_intel.application.operations import OperatorService
from fi_intel.config import Settings


class _Pool:
    def __init__(self) -> None:
        self.arguments: tuple[object, ...] | None = None

    async def execute(self, _: str, *arguments: object) -> None:
        self.arguments = arguments


async def test_operator_synchronizes_only_server_configured_access_attributes() -> None:
    pool = _Pool()
    settings = Settings(
        access_subject="oidc-subject-1",
        access_principal_id="analyst-1",
        access_entitlement_group="fi_gcc_public",
        access_desks="fi_gcc, credit",
        access_roles="reviewer,analyst",
        access_purposes="market_intelligence,evaluation",
    )
    service = OperatorService(SimpleNamespace(settings=settings, postgres_pool=pool))  # type: ignore[arg-type]

    result = await service.synchronize_configured_principal()

    assert result["subject"] == "oidc-subject-1"
    assert result["desks"] == ["credit", "fi_gcc"]
    assert result["roles"] == ["analyst", "reviewer"]
    assert pool.arguments is not None
    assert pool.arguments[:4] == (
        "oidc-subject-1",
        "analyst-1",
        "fi_gcc_public",
        "public",
    )


async def test_operator_rejects_unknown_access_roles_before_sql() -> None:
    pool = _Pool()
    settings = Settings(access_roles="analyst,superuser")
    service = OperatorService(SimpleNamespace(settings=settings, postgres_pool=pool))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="roles are invalid"):
        await service.synchronize_configured_principal()

    assert pool.arguments is None
