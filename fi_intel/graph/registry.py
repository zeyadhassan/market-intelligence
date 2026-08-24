"""Pattern registry: run detectors, write Signal nodes, explain them.

Deterministic (no LLM). Each pattern is independently toggleable via the
`enabled` set. Signals are recorded as :Signal nodes linked to the entity
and evidence so `explain` can return the exact subgraph and documents.
"""

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fi_intel.graph.client import GraphClient
from fi_intel.graph.queries import ALL_PATTERNS, Pattern
from fi_intel.logging import get_logger

DEFAULT_WINDOW_DAYS = 395  # ~13 months: covers sukuk maturity + AT1 call from early 2024

class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_id: str
    pattern: str
    entity_key: str
    entity_name: str
    priority: int
    fired_at: datetime
    as_of: datetime
    evidence: dict[str, str]


class PatternRegistry:
    def __init__(self, client: GraphClient, patterns: tuple[Pattern, ...] = ALL_PATTERNS) -> None:
        self._client = client
        self._patterns = {p.name: p for p in patterns}
        self._log = get_logger(component="graph.patterns")

    def pattern_names(self) -> list[str]:
        return sorted(self._patterns)

    async def run(
        self,
        as_of: datetime,
        enabled: set[str] | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> list[Signal]:
        """Run enabled patterns at as_of; persist and return the signals."""
        active = self._patterns if enabled is None else {
            k: v for k, v in self._patterns.items() if k in enabled
        }
        signals: list[Signal] = []
        async with self._client._driver.session() as session:  # noqa: SLF001
            for pattern in active.values():
                result = await session.run(
                    pattern.cypher,
                    {"as_of": as_of.isoformat(), "window_days": window_days},
                )
                rows = [r.data() async for r in result]
                for row in rows:
                    evidence = {
                        k: str(v) for k, v in row.items()
                        if k not in ("entity_key", "entity_name")
                    }
                    signal_id = f"{pattern.name}:{row['entity_key']}:{as_of.date().isoformat()}"
                    signal = Signal(
                        signal_id=signal_id,
                        pattern=pattern.name,
                        entity_key=row["entity_key"],
                        entity_name=row["entity_name"],
                        priority=pattern.priority,
                        fired_at=as_of,
                        as_of=as_of,
                        evidence=evidence,
                    )
                    await session.run(
                        """
                        MERGE (s:Signal {signal_id: $signal_id})
                        ON CREATE SET
                            s.pattern = $pattern,
                            s.pattern_version = $pversion,
                            s.entity_key = $entity_key,
                            s.entity_name = $entity_name,
                            s.priority = $priority,
                            s.as_of = datetime($as_of),
                            s.evidence_json = $evidence_json
                        """,
                        signal_id=signal_id,
                        pattern=pattern.name,
                        pversion=pattern.version,
                        entity_key=row["entity_key"],
                        entity_name=row["entity_name"],
                        priority=pattern.priority,
                        as_of=as_of.isoformat(),
                        evidence_json=json.dumps(evidence),
                    )
                    signals.append(signal)
                self._log.info(
                    "pattern.ran",
                    pattern=pattern.name,
                    fired=len(rows),
                    as_of=str(as_of.date()),
                )
        signals.sort(key=lambda s: s.priority, reverse=True)
        return signals

    async def explain(self, signal_id: str) -> Signal | None:
        """Return the stored signal with its evidence (subgraph + docs)."""
        async with self._client._driver.session() as session:  # noqa: SLF001
            result = await session.run(
                "MATCH (s:Signal {signal_id: $id}) RETURN s", {"id": signal_id}
            )
            record = await result.single()
            if record is None:
                return None
            node = record["s"]
            as_of = node["as_of"]
            as_of_dt = as_of.to_native() if hasattr(as_of, "to_native") else as_of
            return Signal(
                signal_id=node["signal_id"],
                pattern=node["pattern"],
                entity_key=node["entity_key"],
                entity_name=node["entity_name"],
                priority=node["priority"],
                fired_at=as_of_dt,
                as_of=as_of_dt,
                evidence=json.loads(node["evidence_json"]),
            )
