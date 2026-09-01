"""Authentication and server-side authorization identity resolution."""

import secrets
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.governance.access import AuthorizationError, RequestPrincipal
from fi_intel.retrieval.entitlement import Principal, Side


class AuthenticationError(RuntimeError):
    pass


class VerifiedToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    issuer: str = Field(min_length=1)


@runtime_checkable
class TokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedToken: ...


@runtime_checkable
class IdentityDirectory(Protocol):
    async def resolve(self, subject: str) -> RequestPrincipal | None: ...

    async def close(self) -> None: ...


LOCAL_BEARER_TOKEN = "fi-intel-local"  # noqa: S105 - loopback-only product token
_LOCAL_SUBJECT = "local-analyst"


class LocalTokenVerifier:
    """Verify the built-in token used by the loopback-only local product."""

    async def verify(self, token: str) -> VerifiedToken:
        if not secrets.compare_digest(token, LOCAL_BEARER_TOKEN):
            raise AuthenticationError("invalid bearer token")
        return VerifiedToken(subject=_LOCAL_SUBJECT, issuer="fi-intel-local")


class LocalIdentityDirectory:
    """Resolve the product's single built-in local analyst identity."""

    async def resolve(self, subject: str) -> RequestPrincipal | None:
        if subject != _LOCAL_SUBJECT:
            return None
        return RequestPrincipal(
            subject=subject,
            principal=Principal(
                principal_id=_LOCAL_SUBJECT,
                entitlement_group="fi_gcc_public",
                side=Side.PUBLIC,
            ),
            desks=frozenset({"fi_gcc"}),
            roles=frozenset({"analyst", "reviewer", "publisher"}),
            purposes=frozenset({"market_intelligence", "evaluation"}),
        )

    async def close(self) -> None:
        return None


class Authenticator:
    def __init__(self, verifier: TokenVerifier, directory: IdentityDirectory) -> None:
        self._verifier = verifier
        self._directory = directory

    async def authenticate(self, token: str) -> RequestPrincipal:
        verified = await self._verifier.verify(token)
        principal = await self._directory.resolve(verified.subject)
        if principal is None:
            raise AuthorizationError("verified subject has no active access assignment")
        return principal


__all__ = [
    "AuthenticationError",
    "Authenticator",
    "AuthorizationError",
    "IdentityDirectory",
    "LOCAL_BEARER_TOKEN",
    "LocalIdentityDirectory",
    "LocalTokenVerifier",
    "RequestPrincipal",
    "TokenVerifier",
    "VerifiedToken",
]
