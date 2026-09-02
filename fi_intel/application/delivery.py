"""Deterministic digest assembly and restart-safe sandbox email delivery."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import smtplib
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict

from fi_intel.application.jobs import stable_digest
from fi_intel.config import Settings
from fi_intel.graph.signals import signal_authorization_scope
from fi_intel.logging import safe_error_summary
from fi_intel.results.manifest import ImmutableResultManifest


class DeliveryState(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    ACCEPTED = "accepted"
    OBSERVED_DELIVERED = "observed_delivered"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"
    SUPPRESSED = "suppressed"
    ACCEPTANCE_UNKNOWN = "acceptance_unknown"


class NotificationPreference(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_id: str
    destination_id: str
    timezone_name: str
    local_send_time: time
    frequency: str
    topic_ids: tuple[str, ...]
    include_nothing_new: bool
    link_only: bool
    unsubscribed: bool
    occurred_at: datetime


class DigestAssemblyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    due_preferences: int
    assembled: int
    suppressed: int


class DeliveryReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    claimed: int
    accepted: int
    suppressed: int
    retryable_failed: int
    permanent_failed: int
    acceptance_unknown: int


class DestinationCodec:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("email destination key must be a valid Fernet key") from exc

    def encrypt(self, destination: str) -> str:
        return self._fernet.encrypt(destination.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("email destination cannot be decrypted") from exc


@runtime_checkable
class DeliveryProvider(Protocol):
    async def send(
        self,
        *,
        destination: str,
        subject: str,
        text_body: str,
        html_body: str,
        idempotency_key: str,
    ) -> str: ...


class SandboxSmtpProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._allowlist = {
            item.strip().casefold()
            for item in settings.email_recipient_allowlist.split(",")
            if item.strip()
        }

    async def send(
        self,
        *,
        destination: str,
        subject: str,
        text_body: str,
        html_body: str,
        idempotency_key: str,
    ) -> str:
        if not self._settings.email_enabled:
            raise PermissionError("development email kill switch is disabled")
        if destination.casefold() not in self._allowlist:
            raise PermissionError("destination is not in the development allowlist")
        message = EmailMessage()
        message["From"] = self._settings.email_sender
        message["To"] = destination
        message["Subject"] = subject
        message["Message-ID"] = f"<{idempotency_key}@fi-intel.local>"
        message["X-Entity-Ref-ID"] = idempotency_key
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        def deliver() -> None:
            with smtplib.SMTP(
                self._settings.email_smtp_host,
                self._settings.email_smtp_port,
                timeout=15,
            ) as smtp:
                if self._settings.email_smtp_starttls:
                    smtp.starttls()
                smtp.send_message(message)

        await asyncio.to_thread(deliver)
        return message["Message-ID"] or idempotency_key


class PostgresNotificationService:
    """Preference, immutable digest, and delivery-attempt repository."""

    def __init__(
        self,
        settings: Settings,
        *,
        pool: asyncpg.Pool | None = None,
        provider: DeliveryProvider | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._owns_pool = pool is None
        if not settings.email_destination_key:
            self._codec: DestinationCodec | None = None
        else:
            self._codec = DestinationCodec(settings.email_destination_key)
        self._provider = provider or SandboxSmtpProvider(settings)

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._settings.postgres_dsn, min_size=1, max_size=4
            )
        return self._pool

    async def set_preference(
        self,
        *,
        principal_id: str,
        destination: str,
        timezone_name: str,
        local_send_time: time,
        frequency: str,
        topic_ids: tuple[str, ...],
        include_nothing_new: bool,
        link_only: bool,
        unsubscribed: bool = False,
        verified_at: datetime | None = None,
    ) -> NotificationPreference:
        if self._codec is None:
            raise ValueError("email destination encryption key is not configured")
        normalized = _normalize_email(destination)
        allowlist = {
            item.strip().casefold()
            for item in self._settings.email_recipient_allowlist.split(",")
            if item.strip()
        }
        if normalized.casefold() not in allowlist:
            raise PermissionError("destination is not in the development allowlist")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("notification timezone must be an IANA timezone") from exc
        if frequency not in {"daily", "weekdays", "paused"}:
            raise ValueError("unsupported notification frequency")
        if not topic_ids:
            raise ValueError("notification preference requires at least one topic")
        now = verified_at or datetime.now(UTC)
        fingerprint = hashlib.sha256(normalized.casefold().encode()).hexdigest()
        destination_id = stable_digest([principal_id, "email", fingerprint])
        transition_id = stable_digest(
            [
                destination_id,
                timezone_name,
                local_send_time.isoformat(),
                frequency,
                sorted(topic_ids),
                include_nothing_new,
                link_only,
                unsubscribed,
                now.isoformat(),
            ]
        )
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"notification:{principal_id}",
            )
            await connection.execute(
                """
                INSERT INTO notification_destination_v4 (
                    destination_id, principal_id, channel, destination_ciphertext,
                    destination_fingerprint, verified_at, active
                ) VALUES ($1,$2,'email',$3,$4,$5,TRUE)
                ON CONFLICT (destination_id) DO UPDATE SET
                    destination_ciphertext=EXCLUDED.destination_ciphertext,
                    verified_at=EXCLUDED.verified_at,
                    active=TRUE
                """,
                destination_id,
                principal_id,
                self._codec.encrypt(normalized),
                fingerprint,
                now,
            )
            await connection.execute(
                """
                INSERT INTO notification_preference_transition_v4 (
                    transition_id, principal_id, destination_id, timezone_name,
                    local_send_time, frequency, topic_ids, include_nothing_new,
                    link_only, unsubscribed, occurred_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                transition_id,
                principal_id,
                destination_id,
                timezone_name,
                local_send_time,
                frequency,
                list(sorted(set(topic_ids))),
                include_nothing_new,
                link_only,
                unsubscribed,
                now,
            )
        return NotificationPreference(
            principal_id=principal_id,
            destination_id=destination_id,
            timezone_name=timezone_name,
            local_send_time=local_send_time,
            frequency=frequency,
            topic_ids=tuple(sorted(set(topic_ids))),
            include_nothing_new=include_nothing_new,
            link_only=link_only,
            unsubscribed=unsubscribed,
            occurred_at=now,
        )

    async def assemble_due(self, *, now: datetime | None = None) -> DigestAssemblyReport:
        instant = now or datetime.now(UTC)
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (preference.principal_id)
                   preference.*, destination.destination_ciphertext,
                   destination.active AS destination_active
            FROM notification_preference_transition_v4 preference
            JOIN notification_destination_v4 destination USING (destination_id)
            ORDER BY preference.principal_id, preference.occurred_at DESC,
                     preference.transition_id DESC
            """
        )
        due = assembled = suppressed = 0
        for row in rows:
            preference = _preference_from_row(row)
            local = instant.astimezone(ZoneInfo(preference.timezone_name))
            if not _is_due(preference, local):
                continue
            due += 1
            created = await self._assemble(preference, local.date(), instant)
            if created:
                assembled += 1
            else:
                suppressed += 1
        return DigestAssemblyReport(due_preferences=due, assembled=assembled, suppressed=suppressed)

    async def _assemble(  # noqa: C901 - orchestration keeps one transaction boundary
        self,
        preference: NotificationPreference,
        business_date: date,
        now: datetime,
    ) -> bool:
        pool = await self._get_pool()
        access = await _load_access(pool, preference.principal_id)
        if access is None or preference.unsubscribed or preference.frequency == "paused":
            return False
        scope, active_topics = access
        selected_topics = tuple(
            topic_id for topic_id in preference.topic_ids if topic_id in active_topics
        )
        if not selected_topics:
            return False
        read_models = await pool.fetch(
            """
            SELECT * FROM daily_topic_read_model_v4
            WHERE authorization_scope=$1 AND business_date=$2
              AND topic_id = ANY($3::text[])
            ORDER BY topic_id
            """,
            scope,
            business_date,
            list(selected_topics),
        )
        if len(read_models) != len(selected_topics):
            return False
        eligible = {"new", "updated", "weakened", "contradicted", "resolved"}
        items: list[tuple[str, str, str]] = []
        all_complete = True
        for read in read_models:
            coverage = _json_object(read["coverage_summary"])
            all_complete &= bool(coverage.get("complete", False))
            lifecycle = _json_object(read["result_lifecycle"])
            for result_id in read["ordered_result_version_ids"]:
                state = str(lifecycle.get(str(result_id), "unchanged"))
                if state in eligible:
                    items.append((str(read["topic_id"]), str(result_id), state))
        if not items and not (preference.include_nothing_new and all_complete):
            return False
        result_rows = (
            await pool.fetch(
                """
            SELECT result_version_id, manifest FROM result_version_v3
            WHERE result_version_id = ANY($1::text[])
              AND manifest->>'authorization_scope'=$2
              AND publication_state='publish'
            """,
                [item[1] for item in items],
                scope,
            )
            if items
            else []
        )
        manifests = {
            str(row["result_version_id"]): ImmutableResultManifest.model_validate(
                _json_object(row["manifest"])
            )
            for row in result_rows
        }
        if len(manifests) != len(items):
            return False
        digest_version = stable_digest(
            [
                self._settings.email_template_version,
                [(topic, result_id, state) for topic, result_id, state in items],
                all_complete,
                preference.link_only,
                self._settings.api_host_port,
            ]
        )
        digest_id = stable_digest([preference.principal_id, business_date, scope, digest_version])
        subject, text_body, html_body = _render_digest(
            business_date,
            items,
            manifests,
            include_nothing_new=not items,
            link_only=preference.link_only,
            product_base_url=f"http://localhost:{self._settings.api_host_port}",
        )
        attempt_id = stable_digest([digest_id, "sandbox-smtp"])
        idempotency_key = (
            f"{preference.principal_id}:{business_date.isoformat()}:{scope}:{digest_version}"
        )
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO digest_v4 (
                    digest_id, principal_id, destination_id, local_business_date,
                    authorization_scope, digest_version, subject, text_body,
                    html_body, state, created_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'rendered',$10)
                ON CONFLICT DO NOTHING
                """,
                digest_id,
                preference.principal_id,
                preference.destination_id,
                business_date,
                scope,
                digest_version,
                subject,
                text_body,
                html_body,
                now,
            )
            for position, (topic_id, result_id, state) in enumerate(items, start=1):
                await connection.execute(
                    """
                    INSERT INTO digest_item_v4 (
                        digest_id, position, topic_id, result_version_id,
                        lifecycle_state, coverage_notice
                    ) VALUES ($1,$2,$3,$4,$5,NULL) ON CONFLICT DO NOTHING
                    """,
                    digest_id,
                    position,
                    topic_id,
                    result_id,
                    state,
                )
            if not items:
                for position, topic_id in enumerate(selected_topics, start=1):
                    await connection.execute(
                        """
                        INSERT INTO digest_item_v4 (
                            digest_id, position, topic_id, result_version_id,
                            lifecycle_state, coverage_notice
                        ) VALUES ($1,$2,$3,NULL,'unchanged',$4)
                        ON CONFLICT DO NOTHING
                        """,
                        digest_id,
                        position,
                        topic_id,
                        "Analysis complete - nothing new in the covered scope",
                    )
            await connection.execute(
                """
                INSERT INTO delivery_attempt_v4 (
                    attempt_id, digest_id, idempotency_key, provider, state,
                    attempt_count, next_attempt_at, updated_at
                ) VALUES ($1,$2,$3,'sandbox-smtp','queued',1,$4,$4)
                ON CONFLICT DO NOTHING
                """,
                attempt_id,
                digest_id,
                idempotency_key,
                now,
            )
            await _delivery_transition(
                connection, attempt_id, DeliveryState.QUEUED, now, "digest assembled"
            )
        return True

    async def deliver_once(self, *, worker_id: str) -> DeliveryReport:
        if self._codec is None and self._settings.email_enabled:
            raise ValueError("email destination encryption key is not configured")
        pool = await self._get_pool()
        now = datetime.now(UTC)
        unknown = await pool.fetchval(
            """
            WITH stale AS (
              UPDATE delivery_attempt_v4 SET state='acceptance_unknown',
                     lease_owner=NULL, lease_expires_at=NULL, updated_at=$1,
                     safe_error_summary='worker ended during provider acceptance window'
              WHERE state='sending' AND lease_expires_at <= $1
              RETURNING attempt_id
            ) SELECT count(*) FROM stale
            """,
            now,
        )
        row = await pool.fetchrow(
            """
            WITH candidate AS (
              SELECT attempt_id FROM delivery_attempt_v4
              WHERE state IN ('queued','retryable_failed')
                AND (next_attempt_at IS NULL OR next_attempt_at <= $1)
                AND (lease_expires_at IS NULL OR lease_expires_at <= $1)
                AND attempt_count < $4
              ORDER BY updated_at, attempt_id FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE delivery_attempt_v4 attempt
            SET lease_owner=$2, lease_expires_at=$3, updated_at=$1,
                attempt_count=attempt_count +
                    CASE WHEN attempt.state='retryable_failed' THEN 1 ELSE 0 END
            FROM candidate WHERE attempt.attempt_id=candidate.attempt_id
            RETURNING attempt.*
            """,
            now,
            worker_id,
            now + timedelta(seconds=self._settings.worker_lease_seconds),
            self._settings.email_max_attempts,
        )
        if row is None:
            return DeliveryReport(
                claimed=0,
                accepted=0,
                suppressed=0,
                retryable_failed=0,
                permanent_failed=0,
                acceptance_unknown=int(unknown or 0),
            )
        attempt_id = str(row["attempt_id"])
        digest = await pool.fetchrow(
            """
            SELECT digest.*, destination.destination_ciphertext
            FROM digest_v4 digest
            JOIN notification_destination_v4 destination USING (destination_id)
            WHERE digest.digest_id=$1
            """,
            row["digest_id"],
        )
        if digest is None:
            raise RuntimeError("delivery attempt has no immutable digest")
        principal_id = str(digest["principal_id"])
        accepted = suppressed = retryable_failed = permanent_failed = 0
        async with pool.acquire() as connection:
            await connection.execute(
                "SELECT pg_advisory_lock(hashtext($1))", f"notification:{principal_id}"
            )
            try:
                async with connection.transaction():
                    allowed = await _delivery_still_allowed(
                        connection, principal_id, str(digest["digest_id"])
                    )
                    if not allowed or not self._settings.email_enabled:
                        await _set_delivery_state(
                            connection,
                            attempt_id,
                            DeliveryState.SUPPRESSED,
                            now,
                            "authorization, preference, or kill-switch suppression",
                        )
                        suppressed = 1
                    else:
                        await _set_delivery_state(
                            connection,
                            attempt_id,
                            DeliveryState.SENDING,
                            now,
                            "provider call beginning",
                            retain_lease=True,
                        )
                if not suppressed:
                    if self._codec is None:  # pragma: no cover - kill-switch branch guards this
                        raise RuntimeError("email destination codec is unavailable")
                    try:
                        provider_reference = await self._provider.send(
                            destination=self._codec.decrypt(str(digest["destination_ciphertext"])),
                            subject=str(digest["subject"]),
                            text_body=str(digest["text_body"]),
                            html_body=str(digest["html_body"]),
                            idempotency_key=str(row["idempotency_key"]),
                        )
                    except Exception as exc:
                        retryable = (
                            not isinstance(exc, (PermissionError, ValueError))
                            and int(row["attempt_count"]) < self._settings.email_max_attempts
                        )
                        target = (
                            DeliveryState.RETRYABLE_FAILED
                            if retryable
                            else DeliveryState.PERMANENT_FAILED
                        )
                        async with connection.transaction():
                            await _set_delivery_state(
                                connection,
                                attempt_id,
                                target,
                                datetime.now(UTC),
                                safe_error_summary(exc),
                            )
                        retryable_failed = int(retryable)
                        permanent_failed = int(not retryable)
                    else:
                        async with connection.transaction():
                            await _set_delivery_state(
                                connection,
                                attempt_id,
                                DeliveryState.ACCEPTED,
                                datetime.now(UTC),
                                "sandbox provider accepted message",
                                provider_reference=provider_reference,
                            )
                        accepted = 1
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(hashtext($1))",
                    f"notification:{principal_id}",
                )
        return DeliveryReport(
            claimed=1,
            accepted=accepted,
            suppressed=suppressed,
            retryable_failed=retryable_failed,
            permanent_failed=permanent_failed,
            acceptance_unknown=int(unknown or 0),
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


def _normalize_email(value: str) -> str:
    display, address = parseaddr(value.strip())
    if display or not address or "@" not in address or address != value.strip():
        raise ValueError("destination must be one plain email address")
    return address


def _preference_from_row(row: Any) -> NotificationPreference:
    return NotificationPreference(
        principal_id=str(row["principal_id"]),
        destination_id=str(row["destination_id"]),
        timezone_name=str(row["timezone_name"]),
        local_send_time=row["local_send_time"],
        frequency=str(row["frequency"]),
        topic_ids=tuple(str(item) for item in row["topic_ids"]),
        include_nothing_new=bool(row["include_nothing_new"]),
        link_only=bool(row["link_only"]),
        unsubscribed=bool(row["unsubscribed"]),
        occurred_at=row["occurred_at"],
    )


def _is_due(preference: NotificationPreference, local: datetime) -> bool:
    if preference.unsubscribed or preference.frequency == "paused":
        return False
    if preference.frequency == "weekdays" and local.weekday() >= 5:
        return False
    return local.time().replace(tzinfo=None) >= preference.local_send_time


async def _load_access(pool: asyncpg.Pool, principal_id: str) -> tuple[str, frozenset[str]] | None:
    row = await pool.fetchrow(
        """
        SELECT principal_id, entitlement_group, barrier_side
        FROM principal_access
        WHERE principal_id=$1 AND active AND revoked_at IS NULL
          AND valid_from <= now() AND (valid_until IS NULL OR valid_until > now())
        ORDER BY valid_from DESC LIMIT 1
        """,
        principal_id,
    )
    if row is None:
        return None
    source_rows = await pool.fetch(
        """
        SELECT grant_row.source_id FROM entitlement_grant grant_row
        JOIN source_registry source USING (source_id)
        WHERE grant_row.entitlement_group=$1 AND source.licensed
          AND (source.barrier_side='public' OR $2='private')
        ORDER BY grant_row.source_id
        """,
        row["entitlement_group"],
        row["barrier_side"],
    )
    source_ids = tuple(str(item["source_id"]) for item in source_rows)
    scope = signal_authorization_scope(
        str(row["entitlement_group"]), str(row["barrier_side"]), source_ids
    )
    subscriptions = await pool.fetch(
        """
        SELECT DISTINCT ON (topic_id) topic_id, active
        FROM topic_subscription_transition_v3 WHERE principal_id=$1
        ORDER BY topic_id, occurred_at DESC, transition_id DESC
        """,
        principal_id,
    )
    topics = frozenset(str(item["topic_id"]) for item in subscriptions if item["active"])
    return scope, topics


async def _delivery_still_allowed(
    connection: asyncpg.Connection, principal_id: str, digest_id: str
) -> bool:
    preference = await connection.fetchrow(
        """
        SELECT preference.*, destination.active AS destination_active
        FROM notification_preference_transition_v4 preference
        JOIN notification_destination_v4 destination USING (destination_id)
        WHERE preference.principal_id=$1
        ORDER BY preference.occurred_at DESC, preference.transition_id DESC LIMIT 1
        """,
        principal_id,
    )
    if (
        preference is None
        or preference["unsubscribed"]
        or preference["frequency"] == "paused"
        or not preference["destination_active"]
    ):
        return False
    access = await connection.fetchrow(
        """
        SELECT principal_id, entitlement_group, barrier_side
        FROM principal_access WHERE principal_id=$1 AND active
          AND revoked_at IS NULL AND valid_from <= now()
          AND (valid_until IS NULL OR valid_until > now())
        ORDER BY valid_from DESC LIMIT 1
        """,
        principal_id,
    )
    if access is None:
        return False
    source_rows = await connection.fetch(
        """
        SELECT grant_row.source_id FROM entitlement_grant grant_row
        JOIN source_registry source USING (source_id)
        WHERE grant_row.entitlement_group=$1 AND source.licensed
          AND (source.barrier_side='public' OR $2='private')
        ORDER BY grant_row.source_id
        """,
        access["entitlement_group"],
        access["barrier_side"],
    )
    current_scope = signal_authorization_scope(
        str(access["entitlement_group"]),
        str(access["barrier_side"]),
        tuple(str(row["source_id"]) for row in source_rows),
    )
    digest_scope = await connection.fetchval(
        "SELECT authorization_scope FROM digest_v4 WHERE digest_id=$1",
        digest_id,
    )
    if digest_scope != current_scope:
        return False
    unsubscribed_topic = await connection.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM (SELECT DISTINCT topic_id FROM digest_item_v4 WHERE digest_id=$1) item
          LEFT JOIN LATERAL (
            SELECT active FROM topic_subscription_transition_v3 transition
            WHERE transition.principal_id=$2 AND transition.topic_id=item.topic_id
            ORDER BY occurred_at DESC, transition_id DESC LIMIT 1
          ) subscription ON TRUE
          WHERE COALESCE(subscription.active, FALSE)=FALSE
             OR NOT item.topic_id = ANY($3::text[])
        )
        """,
        digest_id,
        principal_id,
        list(preference["topic_ids"]),
    )
    if unsubscribed_topic:
        return False
    unauthorized = await connection.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM digest_item_v4 item
          JOIN result_version_v3 result USING (result_version_id)
          WHERE item.digest_id=$1 AND item.result_version_id IS NOT NULL
            AND NOT EXISTS (
              SELECT 1 FROM daily_topic_read_model_v4 read
              WHERE read.topic_id=item.topic_id
                AND read.authorization_scope=result.manifest->>'authorization_scope'
                AND item.result_version_id = ANY(read.ordered_result_version_ids)
            )
        )
        """,
        digest_id,
    )
    return not bool(unauthorized)


async def _set_delivery_state(
    connection: asyncpg.Connection,
    attempt_id: str,
    state: DeliveryState,
    occurred_at: datetime,
    detail: str,
    *,
    provider_reference: str | None = None,
    retain_lease: bool = False,
) -> None:
    await connection.execute(
        """
        UPDATE delivery_attempt_v4
        SET state=$2, provider_reference=COALESCE($3,provider_reference),
            safe_error_summary=CASE WHEN $2 IN ('retryable_failed','permanent_failed')
                                    THEN $4 ELSE NULL END,
            next_attempt_at=CASE WHEN $2='retryable_failed' THEN $5 ELSE next_attempt_at END,
            lease_owner=CASE WHEN $6 THEN lease_owner ELSE NULL END,
            lease_expires_at=CASE WHEN $6 THEN lease_expires_at ELSE NULL END,
            updated_at=$7
        WHERE attempt_id=$1
        """,
        attempt_id,
        state.value,
        provider_reference,
        detail[:500],
        occurred_at + timedelta(seconds=60),
        retain_lease,
        occurred_at,
    )
    await _delivery_transition(
        connection, attempt_id, state, occurred_at, detail, provider_reference
    )


async def _delivery_transition(
    connection: asyncpg.Connection,
    attempt_id: str,
    state: DeliveryState,
    occurred_at: datetime,
    detail: str,
    provider_reference: str | None = None,
) -> None:
    transition_id = stable_digest(
        [attempt_id, state.value, occurred_at.isoformat(), provider_reference or ""]
    )
    await connection.execute(
        """
        INSERT INTO delivery_transition_v4 (
            transition_id, attempt_id, state, provider_reference,
            safe_detail, occurred_at
        ) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
        """,
        transition_id,
        attempt_id,
        state.value,
        provider_reference,
        detail[:500],
        occurred_at,
    )


def _render_digest(
    business_date: date,
    items: list[tuple[str, str, str]],
    manifests: dict[str, ImmutableResultManifest],
    *,
    include_nothing_new: bool,
    link_only: bool,
    product_base_url: str = "http://localhost:8000",
) -> tuple[str, str, str]:
    subject = f"FI Intelligence daily digest - {business_date.isoformat()}"
    if include_nothing_new:
        text_body = "Analysis complete - nothing new in the covered scope."
        html_body = "<p>Analysis complete &mdash; nothing new in the covered scope.</p>"
        return subject, text_body, html_body
    text_parts = [subject, ""]
    html_parts = ["<!doctype html><html><body>", f"<h1>{html.escape(subject)}</h1>"]
    for topic_id, result_id, lifecycle in items:
        manifest = manifests[result_id]
        opportunity = manifest.opportunity
        url = f"{product_base_url.rstrip('/')}/stage-one?topic={topic_id}&result={result_id}"
        text_parts.extend(
            [
                f"[{lifecycle.upper()}] {opportunity.title}",
                ("Open the governed result: " + url) if link_only else opportunity.summary,
                f"Evidence: {len(manifest.evidence)} immutable excerpts",
                url,
                "",
            ]
        )
        html_parts.append(
            '<section style="margin:0 0 20px">'
            f"<p><strong>{html.escape(lifecycle.upper())}</strong></p>"
            f"<h2>{html.escape(opportunity.title)}</h2>"
        )
        if not link_only:
            html_parts.append(f"<p>{html.escape(opportunity.summary)}</p>")
        html_parts.append(
            f'<p><a href="{html.escape(url, quote=True)}">Open governed result</a> '
            f"({len(manifest.evidence)} immutable evidence excerpts)</p></section>"
        )
    html_parts.append("</body></html>")
    return subject, "\n".join(text_parts), "".join(html_parts)


def _json_object(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("stored JSON value must be an object")
    return {str(key): item for key, item in decoded.items()}


__all__ = [
    "DeliveryProvider",
    "DeliveryReport",
    "DeliveryState",
    "DestinationCodec",
    "DigestAssemblyReport",
    "NotificationPreference",
    "PostgresNotificationService",
    "SandboxSmtpProvider",
]
