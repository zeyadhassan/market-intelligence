"""Structured extraction through an OpenAI-compatible completions API.

Responses use a strict JSON schema. Adapter-local wire models represent
fixed offsets as separate fields and defer numeric bounds to domain-model
validation. Usage is recorded before the wire response is converted.
"""

import json
import time
from datetime import UTC, datetime
from typing import cast

import openai
from openai.lib._pydantic import to_strict_json_schema
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from openai.types.chat.chat_completion_system_message_param import (
    ChatCompletionSystemMessageParam,
)
from openai.types.chat.chat_completion_user_message_param import ChatCompletionUserMessageParam
from openai.types.shared.reasoning_effort import ReasoningEffort
from openai.types.shared_params.response_format_json_schema import (
    JSONSchema,
    ResponseFormatJSONSchema,
)
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.config import Settings
from fi_intel.governance.model_registry import ModelArtifact, ModelComponent
from fi_intel.governance.model_transport import build_llm_client
from fi_intel.governance.model_usage import ModelCallEvent, ModelUsageLog, estimate_cost_usd
from fi_intel.ingest.extract import (
    PROMPT_VERSION,
    ClaimProperties,
    ExtractionRequest,
    ExtractionResponse,
    RawClaim,
    RawEntityMention,
)
from fi_intel.logging import get_logger
from fi_intel.ontology.vocab import EdgeType, NodeType


class _WireMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: NodeType
    name: str
    key: str | None = None


class _WireClaimProperties(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    direction: str | None = None
    outlook: str | None = None
    rating_type: str | None = None
    previous_rating: str | None = None
    new_rating: str | None = None
    metric: str | None = None
    value: float | None = None
    prior: float | None = None
    unit: str | None = None
    role: str | None = None
    programme: str | None = None
    limit_usd_bn: float | None = None
    amount_usd_mn: float | None = None
    currency: str | None = None
    status: str | None = None
    marketed: bool | None = None
    maturity_date: str | None = None
    first_call_date: str | None = None
    instrument_class: str | None = Field(default=None, alias="class")


class _WireClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: EdgeType
    subject: _WireMention
    object: _WireMention
    valid_from: datetime
    confidence: float
    snippet_start: int
    snippet_end: int
    snippet_text: str
    properties: _WireClaimProperties = Field(default_factory=_WireClaimProperties)


class _WireExtractionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[_WireClaim]


# Use the SDK transform to preserve its strict-output schema conventions.
_SCHEMA = to_strict_json_schema(_WireExtractionOut)


class OpenAICompatibleStructuredExtractor:
    """Structured extractor backed by an OpenAI-compatible server."""

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        model: str,
        temperature: float,
        reasoning_effort: str | None,
        usage_log: ModelUsageLog,
        run_id: str,
        artifact: ModelArtifact | None = None,
    ) -> None:
        if artifact is not None and (
            artifact.component is not ModelComponent.EXTRACTION or artifact.model_id != model
        ):
            raise ValueError("extraction artifact does not match the configured serving model")
        self._client = client
        self._model = model
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._usage_log = usage_log
        self._run_id = run_id
        self._artifact = artifact
        self._log = get_logger(component="ingest.extractors.openai_compatible")

    @property
    def model_version(self) -> str:
        if self._artifact is None:
            return self._model
        return (
            f"release:{self._artifact.release_id}:"
            f"{self._artifact.artifact_digest}:{self._artifact.model_id}"
        )

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        # .create(), not the .parse() convenience method: .parse() would
        # validate the response into _WireExtractionOut *before* returning,
        # so a model that (despite the strict schema) emits something that
        # fails validation would raise before this method ever got a
        # chance to record usage. Calling .create() directly and parsing
        # the JSON ourselves afterward means spend is recorded for every
        # completed HTTP call, unconditionally.
        started = time.monotonic()
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=request.system_instruction),
            ChatCompletionUserMessageParam(
                role="user",
                content=f"<document>\n{request.document_text}\n</document>",
            ),
        ]
        response_format = ResponseFormatJSONSchema(
            type="json_schema",
            json_schema=JSONSchema(name="extraction_result", schema=_SCHEMA, strict=True),
        )
        # The serving stack defines and validates supported effort values.
        effort = (
            cast(ReasoningEffort, self._reasoning_effort)
            if self._reasoning_effort is not None
            else openai.omit
        )
        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format=response_format,
                temperature=self._temperature,
                reasoning_effort=effort,
            )
        except Exception as exc:
            await self._usage_log.record(
                ModelCallEvent(
                    run_id=self._run_id,
                    component="extract",
                    model=self._model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    latency_ms=(time.monotonic() - started) * 1_000.0,
                    subject_id=request.doc_id,
                    recorded_at=datetime.now(UTC),
                    status=("timed_out" if isinstance(exc, openai.APITimeoutError) else "failed"),
                    error_type=type(exc).__name__,
                    release_id=self._artifact.release_id if self._artifact else None,
                    artifact_digest=(self._artifact.artifact_digest if self._artifact else None),
                    prompt_version=PROMPT_VERSION,
                    schema_version="extraction-response-v1",
                )
            )
            self._log.warning("extract.api_error", doc_id=request.doc_id, model=self._model)
            raise
        latency_ms = (time.monotonic() - started) * 1000.0

        usage = completion.usage
        content = completion.choices[0].message.content
        refusal = completion.choices[0].message.refusal
        wire: _WireExtractionOut | None = None
        parse_error: Exception | None = None
        if content is not None:
            try:
                wire = _WireExtractionOut.model_validate(json.loads(content))
            except Exception as exc:
                parse_error = exc
        await self._usage_log.record(
            ModelCallEvent(
                run_id=self._run_id,
                component="extract",
                model=self._model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                cost_usd=estimate_cost_usd(
                    self._model,
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                ),
                latency_ms=latency_ms,
                subject_id=request.doc_id,
                recorded_at=datetime.now(UTC),
                status=(
                    "refused" if content is None else "malformed" if parse_error else "succeeded"
                ),
                error_type=type(parse_error).__name__ if parse_error else None,
                release_id=self._artifact.release_id if self._artifact else None,
                artifact_digest=(self._artifact.artifact_digest if self._artifact else None),
                prompt_version=PROMPT_VERSION,
                schema_version="extraction-response-v1",
            )
        )

        if content is None:
            msg = f"extraction response for {request.doc_id!r} had no content" + (
                f" (refusal: {refusal})" if refusal else ""
            )
            raise ValueError(msg)
        if parse_error is not None:
            raise parse_error
        if wire is None:
            raise RuntimeError("extraction response parsing produced no typed result")

        # Reassembling into the real RawClaim re-applies its actual
        # constraints (confidence bounds) that the wire schema above could
        # not express to the server directly — see module docstring.
        # _WireMention is field-identical to RawEntityMention but a
        # distinct class, so Pydantic's model-type check won't accept one
        # where the other is expected — convert explicitly.
        claims = [
            RawClaim(
                predicate=c.predicate,
                subject=RawEntityMention(
                    node_type=c.subject.node_type, name=c.subject.name, key=c.subject.key
                ),
                object=RawEntityMention(
                    node_type=c.object.node_type, name=c.object.name, key=c.object.key
                ),
                valid_from=c.valid_from,
                confidence=c.confidence,
                snippet_offset=(c.snippet_start, c.snippet_end),
                snippet_text=c.snippet_text,
                properties=ClaimProperties.model_validate(c.properties.model_dump(by_alias=True)),
            )
            for c in wire.claims
        ]
        return ExtractionResponse(claims=claims)


def build_structured_extractor(
    settings: Settings,
    usage_log: ModelUsageLog,
    run_id: str,
    artifact: ModelArtifact | None = None,
) -> OpenAICompatibleStructuredExtractor:
    """Build the configured extractor, raising when no endpoint is set."""
    if settings.analysis_mode in {"shadow", "pilot", "production"} and artifact is None:
        raise RuntimeError("governed analysis requires a registry-routed extraction release")
    if not settings.llm_base_url:
        msg = (
            "FI_INTEL_LLM_BASE_URL is not set; cannot construct a real "
            "StructuredExtractor. Point it at your OpenAI-compatible "
            "on-prem endpoint, or wire a stub explicitly for tests."
        )
        raise RuntimeError(msg)
    client = build_llm_client(settings)
    return OpenAICompatibleStructuredExtractor(
        client=client,
        model=settings.extraction_model,
        temperature=settings.extraction_temperature,
        reasoning_effort=settings.extraction_reasoning_effort,
        usage_log=usage_log,
        run_id=run_id,
        artifact=artifact,
    )
