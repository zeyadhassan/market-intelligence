"""Trusted identity and authorization context shared by application interfaces.

HTTP authentication adapts verified tokens into these models.  Keeping the
models in governance prevents application use cases from depending on FastAPI
or any other delivery interface.
"""

from pydantic import BaseModel, ConfigDict

from fi_intel.retrieval.entitlement import Principal


class AuthorizationError(RuntimeError):
    """The trusted principal is not allowed to perform an operation."""


class RequestPrincipal(BaseModel):
    """Identity and access attributes loaded from a trusted directory."""

    model_config = ConfigDict(frozen=True)

    subject: str
    principal: Principal
    desks: frozenset[str]
    roles: frozenset[str]
    purposes: frozenset[str]

    def require_desk(self, desk: str) -> None:
        if desk not in self.desks:
            raise AuthorizationError(f"principal is not assigned to desk {desk!r}")

    def require_role(self, *roles: str) -> None:
        if not self.roles.intersection(roles):
            raise AuthorizationError(f"one of roles {roles!r} is required")

    def require_purpose(self, purpose: str) -> None:
        if purpose not in self.purposes:
            raise AuthorizationError(f"purpose {purpose!r} is required")


__all__ = ["AuthorizationError", "RequestPrincipal"]
