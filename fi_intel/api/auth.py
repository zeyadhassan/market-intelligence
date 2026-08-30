"""OIDC verification and server-side authorization identity resolution."""

import asyncio
from typing import Protocol, runtime_checkable

import asyncpg
import jwt
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.application.runtime_resources import PostgresPoolProvider
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


class OIDCTokenVerifier:
    """Verify signed OIDC JWTs against a configured issuer JWKS."""

    def __init__(self, issuer: str, audience: str, jwks_url: str) -> None:
        if not issuer or not audience or not jwks_url:
            raise ValueError("OIDC issuer, audience, and JWKS URL are required")
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client = jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)

    async def verify(self, token: str) -> VerifiedToken:
        try:
            key = await asyncio.to_thread(self._client.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid bearer token") from exc
        subject = claims.get("sub")
        issuer = claims.get("iss")
        if not isinstance(subject, str) or not isinstance(issuer, str):
            raise AuthenticationError("bearer token lacks required identity claims")
        return VerifiedToken(subject=subject, issuer=issuer)


class PostgresIdentityDirectory:
    """Map verified OIDC subjects to access attributes controlled by the server."""

    def __init__(
        self,
        dsn: str,
        *,
        pool: asyncpg.Pool | None = None,
        pool_provider: PostgresPoolProvider | None = None,
    ) -> None:
        self._dsn = dsn
        self._pool = pool
        self._pool_provider = pool_provider
        self._owns_pool = pool is None and pool_provider is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = (
                await self._pool_provider.get_pool()
                if self._pool_provider is not None
                else await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            )
        return self._pool

    async def resolve(self, subject: str) -> RequestPrincipal | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT subject, principal_id, entitlement_group, barrier_side,
                   desks, roles, purposes
            FROM principal_access
            WHERE subject = $1
              AND active
              AND revoked_at IS NULL
              AND valid_from <= now()
              AND (valid_until IS NULL OR valid_until > now())
            """,
            subject,
        )
        if row is None:
            return None
        return RequestPrincipal(
            subject=row["subject"],
            principal=Principal(
                principal_id=row["principal_id"],
                entitlement_group=row["entitlement_group"],
                side=Side(row["barrier_side"]),
            ),
            desks=frozenset(row["desks"]),
            roles=frozenset(row["roles"]),
            purposes=frozenset(row["purposes"]),
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


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
    "OIDCTokenVerifier",
    "PostgresIdentityDirectory",
    "RequestPrincipal",
    "TokenVerifier",
    "VerifiedToken",
]
