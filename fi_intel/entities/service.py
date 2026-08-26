"""Deterministic entity resolution and reviewer workflow."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fi_intel.entities.identifiers import IdentifierScheme
from fi_intel.entities.models import (
    EntityMentionContext,
    EntityReferenceBundle,
    EntityRelationshipKind,
    EntityResolution,
    EntityResolutionCandidate,
    EntityScoreContribution,
    ResolutionDisposition,
    ResolutionPolicy,
    entity_link_decision_id,
    entity_link_id,
    entity_resolution_candidate_id,
    entity_resolution_id,
)
from fi_intel.entities.normalization import normalize_entity_name
from fi_intel.entities.repository import EntityRepository, EntityRepositoryInvariantError
from fi_intel.ledger.models import EntityLinkDecision, EntityLinkStatus


class EntityResolutionService:
    """Resolve exact candidates conservatively and persist every review action."""

    def __init__(
        self,
        repository: EntityRepository,
        *,
        resolver_version: str = "entity-resolver-v2",
        policy: ResolutionPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._resolver_version = resolver_version
        self._policy = policy or ResolutionPolicy()

    async def resolve(
        self,
        mention: EntityMentionContext,
        *,
        as_of: datetime,
    ) -> EntityResolution:
        bundles = await self._repository.matching_bundles(mention, as_of)
        resolution_id = entity_resolution_id(
            mention,
            self._resolver_version,
            self._policy.version,
            as_of,
        )
        candidates = tuple(
            sorted(
                (
                    self._score_candidate(resolution_id, mention, bundle, as_of)
                    for bundle in bundles
                ),
                key=lambda item: (-item.score, str(item.entity_id)),
            )
        )
        unblocked = [item for item in candidates if not item.blocked_reasons]
        top = unblocked[0] if unblocked else None
        runner_up_score = unblocked[1].score if len(unblocked) > 1 else 0.0
        score = top.score if top is not None else 0.0
        margin = round(max(0.0, score - runner_up_score), 4)
        strong_identifier = bool(
            top
            and self._candidate_has_strong_identifier(
                top,
                next(bundle for bundle in bundles if bundle.entity.entity_id == top.entity_id),
            )
        )
        if (
            top is not None
            and strong_identifier
            and score >= self._policy.auto_link_threshold
            and margin >= self._policy.minimum_margin
        ):
            disposition = ResolutionDisposition.AUTO_LINK
            reason = "unique strong identifier cleared score and margin policy"
        elif top is not None and score >= self._policy.review_threshold:
            disposition = ResolutionDisposition.REVIEW_REQUIRED
            reason = "candidate evidence requires reviewer confirmation"
        else:
            disposition = ResolutionDisposition.ABSTAINED
            reason = "no unblocked candidate cleared the review threshold"
        resolution = EntityResolution(
            resolution_id=resolution_id,
            mention=mention,
            disposition=disposition,
            candidates=candidates,
            recommended_candidate_id=top.candidate_id if top is not None else None,
            score=score,
            margin=margin,
            reason=reason,
            resolver_version=self._resolver_version,
            policy_version=self._policy.version,
            resolved_at=as_of,
        )
        await self._repository.append_resolution(resolution)
        return resolution

    async def approve(
        self,
        resolution_id: UUID,
        candidate_id: UUID,
        *,
        reviewer: str,
        reason: str,
        decided_at: datetime,
        supersedes_entity_link_id: UUID | None = None,
    ) -> EntityLinkDecision:
        resolution, candidate = await self._load_candidate(resolution_id, candidate_id)
        if candidate.blocked_reasons:
            raise EntityRepositoryInvariantError("a blocked candidate cannot be approved")
        link_id = entity_link_id(
            resolution.mention.mention_id,
            candidate.entity_id,
            candidate.candidate_id,
        )
        decision = EntityLinkDecision(
            decision_id=entity_link_decision_id(
                link_id,
                resolution.mention.mention_id,
                EntityLinkStatus.LINKED.value,
                decided_at,
                reviewer,
                reason,
            ),
            mention_id=resolution.mention.mention_id,
            status=EntityLinkStatus.LINKED,
            entity_link_id=link_id,
            entity_id=candidate.entity_id,
            resolution_candidate_id=candidate.candidate_id,
            supersedes_entity_link_id=supersedes_entity_link_id,
            candidate_entity_ids=tuple(
                item.entity_id for item in resolution.candidates if not item.blocked_reasons
            ),
            confidence=candidate.score,
            resolver_version=resolution.resolver_version,
            reason=reason,
            decided_at=decided_at,
            decided_by=reviewer,
            policy_id=resolution.mention.policy_id,
        )
        await self._repository.append_link_decision(decision)
        return decision

    async def auto_link(self, resolution: EntityResolution) -> EntityLinkDecision:
        if (
            resolution.disposition is not ResolutionDisposition.AUTO_LINK
            or resolution.recommended_candidate_id is None
        ):
            raise EntityRepositoryInvariantError("resolution is not eligible for auto-linking")
        return await self.approve(
            resolution.resolution_id,
            resolution.recommended_candidate_id,
            reviewer=f"resolver:{self._resolver_version}",
            reason=resolution.reason,
            decided_at=resolution.resolved_at,
        )

    async def reject(
        self,
        resolution_id: UUID,
        candidate_id: UUID,
        *,
        reviewer: str,
        reason: str,
        decided_at: datetime,
    ) -> EntityLinkDecision:
        resolution, candidate = await self._load_candidate(resolution_id, candidate_id)
        decision = EntityLinkDecision(
            decision_id=entity_link_decision_id(
                None,
                resolution.mention.mention_id,
                EntityLinkStatus.REJECTED.value,
                decided_at,
                reviewer,
                reason,
            ),
            mention_id=resolution.mention.mention_id,
            status=EntityLinkStatus.REJECTED,
            resolution_candidate_id=candidate.candidate_id,
            candidate_entity_ids=tuple(item.entity_id for item in resolution.candidates),
            confidence=candidate.score,
            resolver_version=resolution.resolver_version,
            reason=reason,
            decided_at=decided_at,
            decided_by=reviewer,
            policy_id=resolution.mention.policy_id,
        )
        await self._repository.append_link_decision(decision)
        return decision

    async def invalidate(
        self,
        mention_id: UUID,
        entity_link_id_value: UUID,
        *,
        reviewer: str,
        reason: str,
        decided_at: datetime,
    ) -> EntityLinkDecision:
        active = await self._repository.active_link_for_mention(mention_id, decided_at)
        if active is None or active.entity_link_id != entity_link_id_value:
            raise EntityRepositoryInvariantError("entity link is not active")
        decision = EntityLinkDecision(
            decision_id=entity_link_decision_id(
                entity_link_id_value,
                mention_id,
                EntityLinkStatus.INVALIDATED.value,
                decided_at,
                reviewer,
                reason,
            ),
            mention_id=mention_id,
            status=EntityLinkStatus.INVALIDATED,
            invalidates_entity_link_id=entity_link_id_value,
            candidate_entity_ids=active.candidate_entity_ids,
            resolver_version=active.resolver_version,
            reason=reason,
            decided_at=decided_at,
            decided_by=reviewer,
            policy_id=active.policy_id,
        )
        await self._repository.append_link_decision(decision)
        return decision

    async def _load_candidate(
        self,
        resolution_id: UUID,
        candidate_id: UUID,
    ) -> tuple[EntityResolution, EntityResolutionCandidate]:
        resolution = await self._repository.get_resolution(resolution_id)
        if resolution is None:
            raise EntityRepositoryInvariantError("resolution is not persisted")
        candidate = next(
            (item for item in resolution.candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None:
            raise EntityRepositoryInvariantError("candidate is absent from the resolution")
        return resolution, candidate

    def _score_candidate(
        self,
        resolution_id: UUID,
        mention: EntityMentionContext,
        bundle: EntityReferenceBundle,
        generated_at: datetime,
    ) -> EntityResolutionCandidate:
        normalized_identifiers = {
            item.normalized().match_key: item.normalized().scheme for item in mention.identifiers
        }
        matched_identifiers = tuple(
            sorted(
                (
                    item
                    for item in bundle.identifiers
                    if (item.scheme.value, item.scope, item.normalized_value)
                    in normalized_identifiers
                ),
                key=lambda item: str(item.identifier_id),
            )
        )
        name_key = normalize_entity_name(mention.surface)
        matched_names = tuple(
            sorted(
                (item for item in bundle.names if item.normalized_name == name_key),
                key=lambda item: str(item.name_id),
            )
        )
        blocked = self._blocking_reasons(mention, bundle)
        contributions: list[EntityScoreContribution] = []
        if not blocked:
            contributions.append(
                EntityScoreContribution(
                    component="entity_type",
                    value=0.04,
                    explanation="Mention and reference entity types agree.",
                )
            )
            matched_schemes = {item.scheme for item in matched_identifiers}
            if matched_schemes & self._policy.strong_identifier_schemes:
                contributions.append(
                    EntityScoreContribution(
                        component="strong_identifier",
                        value=0.95,
                        explanation="Checksum-valid exact identifier match.",
                    )
                )
            elif IdentifierScheme.TICKER in matched_schemes:
                contributions.append(
                    EntityScoreContribution(
                        component="venue_scoped_ticker",
                        value=0.70,
                        explanation="Exact ticker match within the stated venue.",
                    )
                )
            if matched_names:
                contributions.append(
                    EntityScoreContribution(
                        component="registered_name",
                        value=0.62,
                        explanation="Exact registered legal name or alias match.",
                    )
                )
            if mention.jurisdiction and bundle.entity.jurisdiction == mention.jurisdiction:
                contributions.append(
                    EntityScoreContribution(
                        component="jurisdiction",
                        value=0.04,
                        explanation="Jurisdiction agrees with the reference profile.",
                    )
                )
            if mention.sector and bundle.entity.sector == mention.sector:
                contributions.append(
                    EntityScoreContribution(
                        component="sector",
                        value=0.03,
                        explanation="Sector agrees with the reference profile.",
                    )
                )
            if self._hierarchy_matches(mention, bundle):
                contributions.append(
                    EntityScoreContribution(
                        component="hierarchy",
                        value=0.08,
                        explanation="Parent or issuer context agrees with governed hierarchy.",
                    )
                )
        score = 0.0 if blocked else round(min(1.0, sum(item.value for item in contributions)), 4)
        return EntityResolutionCandidate(
            candidate_id=entity_resolution_candidate_id(
                resolution_id,
                bundle.entity.entity_id,
            ),
            resolution_id=resolution_id,
            mention_id=mention.mention_id,
            entity_id=bundle.entity.entity_id,
            score=score,
            contributions=tuple(contributions),
            matched_identifier_ids=tuple(item.identifier_id for item in matched_identifiers),
            matched_name_ids=tuple(item.name_id for item in matched_names),
            blocked_reasons=tuple(blocked),
            resolver_version=self._resolver_version,
            policy_version=self._policy.version,
            generated_at=generated_at,
            policy_id=mention.policy_id,
        )

    @staticmethod
    def _blocking_reasons(
        mention: EntityMentionContext,
        bundle: EntityReferenceBundle,
    ) -> list[str]:
        blocked: list[str] = []
        if bundle.entity.entity_type is not mention.entity_type:
            blocked.append("entity_type_mismatch")
        if (
            mention.jurisdiction is not None
            and bundle.entity.jurisdiction is not None
            and mention.jurisdiction != bundle.entity.jurisdiction
        ):
            blocked.append("jurisdiction_mismatch")
        if (
            mention.sector is not None
            and bundle.entity.sector is not None
            and mention.sector != bundle.entity.sector
        ):
            blocked.append("sector_mismatch")
        if (
            mention.parent_entity_id is not None or mention.issuer_entity_id is not None
        ) and not EntityResolutionService._hierarchy_matches(mention, bundle):
            blocked.append("hierarchy_mismatch")
        return blocked

    @staticmethod
    def _hierarchy_matches(
        mention: EntityMentionContext,
        bundle: EntityReferenceBundle,
    ) -> bool:
        if mention.parent_entity_id is not None:
            return any(
                item.kind is EntityRelationshipKind.PARENT_OF
                and item.subject_entity_id == mention.parent_entity_id
                and item.object_entity_id == bundle.entity.entity_id
                for item in bundle.relationships
            )
        if mention.issuer_entity_id is not None:
            return any(
                item.kind is EntityRelationshipKind.ISSUER_OF
                and item.subject_entity_id == mention.issuer_entity_id
                and item.object_entity_id == bundle.entity.entity_id
                for item in bundle.relationships
            )
        return False

    def _candidate_has_strong_identifier(
        self,
        candidate: EntityResolutionCandidate,
        bundle: EntityReferenceBundle,
    ) -> bool:
        matched = set(candidate.matched_identifier_ids)
        return any(
            item.identifier_id in matched and item.scheme in self._policy.strong_identifier_schemes
            for item in bundle.identifiers
        )


__all__ = ["EntityResolutionService"]
