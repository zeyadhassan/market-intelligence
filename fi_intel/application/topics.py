"""Governed analyst topic definitions shared by every execution mode."""

from dataclasses import dataclass

import asyncpg
from pydantic import BaseModel, ConfigDict

from fi_intel.application.runtime_resources import PostgresPoolProvider


@dataclass(frozen=True, slots=True)
class TopicDefinition:
    topic_id: str
    label: str
    description: str
    patterns: frozenset[str]


TOPICS = (
    TopicDefinition(
        topic_id="upcoming-maturities",
        label="Upcoming maturities",
        description="Funding needs where complete coverage found no announced refinancing.",
        patterns=frozenset({"maturity_wall_no_refi", "at1_call_approaching_no_refi"}),
    ),
    TopicDefinition(
        topic_id="ratings-capital-pressure",
        label="Rating and capital pressure",
        description="Rating deterioration combined with a material capital movement.",
        patterns=frozenset({"negative_rating_action_with_capital_decline"}),
    ),
)
TOPICS_BY_ID = {topic.topic_id: topic for topic in TOPICS}
TOPIC_BY_PATTERN = {pattern: topic.topic_id for topic in TOPICS for pattern in topic.patterns}


class GovernedTopic(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic_id: str
    version: str
    label: str
    description: str
    owner: str
    patterns: frozenset[str]
    required_source_ids: frozenset[str]
    freshness_seconds: int
    detector_policy_version: str
    retrieval_policy_version: str
    lifecycle_policy_version: str
    display_order: int


class PostgresTopicCatalog:
    """Read the active versioned product catalog from PostgreSQL."""

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
                else await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)
            )
        return self._pool

    async def active(self) -> tuple[GovernedTopic, ...]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (topic_id) * FROM analysis_topic_v4
            WHERE active
            ORDER BY topic_id, created_at DESC, version DESC
            """
        )
        topics = tuple(_governed_topic(row) for row in rows)
        return tuple(sorted(topics, key=lambda item: (item.display_order, item.topic_id)))

    async def require(self, topic_id: str) -> GovernedTopic:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT * FROM analysis_topic_v4
            WHERE topic_id=$1 AND active
            ORDER BY created_at DESC, version DESC LIMIT 1
            """,
            topic_id,
        )
        if row is None:
            raise KeyError(topic_id)
        return _governed_topic(row)

    async def require_many(self, topic_ids: tuple[str, ...]) -> dict[str, GovernedTopic]:
        topics = {topic.topic_id: topic for topic in await self.active()}
        missing = set(topic_ids) - topics.keys()
        if missing:
            raise KeyError(f"inactive or unknown topics: {sorted(missing)}")
        return {topic_id: topics[topic_id] for topic_id in topic_ids}

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


def _governed_topic(row: asyncpg.Record) -> GovernedTopic:
    return GovernedTopic(
        topic_id=str(row["topic_id"]),
        version=str(row["version"]),
        label=str(row["display_name"]),
        description=str(row["description"]),
        owner=str(row["owner"]),
        patterns=frozenset(str(item) for item in row["pattern_names"]),
        required_source_ids=frozenset(str(item) for item in row["required_source_ids"]),
        freshness_seconds=int(row["freshness_seconds"]),
        detector_policy_version=str(row["detector_policy_version"]),
        retrieval_policy_version=str(row["retrieval_policy_version"]),
        lifecycle_policy_version=str(row["lifecycle_policy_version"]),
        display_order=int(row["display_order"]),
    )


__all__ = [
    "GovernedTopic",
    "PostgresTopicCatalog",
    "TOPICS",
    "TOPICS_BY_ID",
    "TOPIC_BY_PATTERN",
    "TopicDefinition",
]
