"""Transport-level tests for the OpenAI-compatible extraction adapter."""

import json

import httpx2
import openai
import pytest
from pydantic import ValidationError

from fi_intel.governance.model_usage import InMemoryModelUsageLog
from fi_intel.ingest.extract import EXTRACTOR_VERSION, PROMPT_VERSION, ExtractionRequest
from fi_intel.ingest.extractors.openai_compatible_extractor import (
    OpenAICompatibleStructuredExtractor,
)


def _client_returning(content: str, model: str = "gpt-oss-120b") -> tuple:
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content, "refusal": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }
        return httpx2.Response(200, json=body)

    transport = httpx2.MockTransport(handler)
    client = openai.AsyncOpenAI(
        base_url="http://localhost:9999/v1",
        api_key="not-needed",
        http_client=httpx2.AsyncClient(transport=transport),
    )
    return client, captured


_VALID_CLAIM = {
    "predicate": "ISSUES",
    "subject": {"node_type": "Organization", "name": "Gulf Meridian Bank", "key": None},
    "object": {"node_type": "Instrument", "name": "USD 500 million sukuk", "key": None},
    "valid_from": "2024-01-15T00:00:00Z",
    "confidence": 0.9,
    "snippet_start": 0,
    "snippet_end": 9,
    "snippet_text": "Gulf Meri",
    "properties": {"class": "senior", "currency": "USD", "amount_usd_mn": 500.0},
}

_REQUEST = ExtractionRequest(
    prompt_version=PROMPT_VERSION,
    extractor_version=EXTRACTOR_VERSION,
    system_instruction="Extract only typed claims.",
    document_text='{"body":"Gulf Meridian issued a sukuk."}',
    prompt="Extract claims from: Gulf Meridian issued a sukuk.",
    doc_id="SW-2024-0007",
)


async def test_extract_parses_claims_and_records_usage() -> None:
    client, captured = _client_returning(json.dumps({"claims": [_VALID_CLAIM]}))
    usage_log = InMemoryModelUsageLog()
    extractor = OpenAICompatibleStructuredExtractor(
        client,
        model="gpt-oss-120b",
        temperature=0.0,
        reasoning_effort="medium",
        usage_log=usage_log,
        run_id="run-1",
    )

    response = await extractor.extract(_REQUEST)

    assert len(response.claims) == 1
    claim = response.claims[0]
    assert str(claim.predicate) == "ISSUES"
    assert claim.snippet_offset == (0, 9)
    assert claim.confidence == 0.9
    assert str(claim.properties.instrument_class) == "senior"

    # The request actually sent the document prompt and a schema-locked
    # structured-output config, not free text.
    (request,) = captured
    sent = json.loads(request.content)
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][0]["content"] == _REQUEST.system_instruction
    assert sent["messages"][1]["role"] == "user"
    assert _REQUEST.document_text in sent["messages"][1]["content"]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["temperature"] == 0.0
    assert sent["reasoning_effort"] == "medium"

    # Cost/latency accounting happened.
    (event,) = usage_log.events
    assert event.component == "extract"
    assert event.run_id == "run-1"
    assert event.subject_id == "SW-2024-0007"
    assert event.input_tokens == 120
    assert event.output_tokens == 40


async def test_reasoning_effort_omitted_when_unset() -> None:
    client, captured = _client_returning(json.dumps({"claims": []}))
    extractor = OpenAICompatibleStructuredExtractor(
        client,
        model="gpt-oss-120b",
        temperature=0.0,
        reasoning_effort=None,
        usage_log=InMemoryModelUsageLog(),
        run_id="run-1",
    )
    await extractor.extract(_REQUEST)
    (request,) = captured
    assert "reasoning_effort" not in json.loads(request.content)


async def test_extract_with_no_claims_returns_empty_response() -> None:
    client, _ = _client_returning(json.dumps({"claims": []}))
    extractor = OpenAICompatibleStructuredExtractor(
        client, "gpt-oss-120b", 0.0, None, InMemoryModelUsageLog(), "run-1"
    )
    response = await extractor.extract(_REQUEST)
    assert response.claims == []


async def test_out_of_vocabulary_predicate_raises_validation_error_after_recording_usage() -> None:
    """A model that (despite the schema lock) emits an invented predicate
    fails our own defensive parsing — ExtractionPipeline already knows how
    to route a ValidationError to proposed_type (tests/test_extract.py);
    this proves the adapter surfaces that same error rather than swallowing
    or mis-parsing it, and that spend is still recorded even when it does."""
    bad_claim = {**_VALID_CLAIM, "predicate": "SECRETLY_OWNS"}
    client, _ = _client_returning(json.dumps({"claims": [bad_claim]}))
    usage_log = InMemoryModelUsageLog()
    extractor = OpenAICompatibleStructuredExtractor(
        client, "gpt-oss-120b", 0.0, None, usage_log, "run-1"
    )

    with pytest.raises(ValidationError):
        await extractor.extract(_REQUEST)

    # Usage was recorded before the (post-response) parsing step failed.
    assert len(usage_log.events) == 1
    assert usage_log.events[0].status == "malformed"
    assert usage_log.events[0].prompt_version == PROMPT_VERSION
