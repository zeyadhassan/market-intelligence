"""Transport-level tests for the OpenAI-compatible reasoning adapter."""

import json

import httpx2
import openai

from fi_intel.agents.opportunity_research import (
    RESEARCH_INSTRUCTION,
    RESEARCH_PROMPT_VERSION,
    ResearchRequest,
)
from fi_intel.agents.reasoning.openai_compatible_reasoning import OpenAICompatibleReasoningModel
from fi_intel.governance.model_usage import InMemoryModelUsageLog


def _client_returning(payload: dict, model: str = "gpt-oss-120b") -> tuple:
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
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(payload),
                        "refusal": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 300, "completion_tokens": 80, "total_tokens": 380},
        }
        return httpx2.Response(200, json=body)

    transport = httpx2.MockTransport(handler)
    client = openai.AsyncOpenAI(
        base_url="http://localhost:9999/v1",
        api_key="not-needed",
        http_client=httpx2.AsyncClient(transport=transport),
    )
    return client, captured


_REQUEST = ResearchRequest(
    prompt_version=RESEARCH_PROMPT_VERSION,
    signal_pattern="board_approved_issuance_programme",
    entity_name="Gulf Meridian Bank Q.P.S.C.",
    signal_evidence={"programme": "USD 2bn EMTN"},
    profile_predicates=["BOARD_APPROVED", "HAS_PROGRAMME"],
    evidence_excerpts=["Gulf Meridian's board approved a $2bn EMTN programme."],
    instruction=RESEARCH_INSTRUCTION,
)


async def test_research_parses_response_and_records_usage() -> None:
    payload = {
        "title": "EMTN programme signals upcoming issuance",
        "status": "supported",
        "falsifier": "Programme lapses without any mandate within two quarters.",
        "claims": [
            {
                "text": "Board-approved programme indicates readiness to issue.",
                "claim_type": "thesis",
                "evidence_indices": [0],
                "confidence": 0.9,
                "uncertainty": "Timing remains uncertain.",
            }
        ],
        "insufficient_evidence": False,
    }
    client, captured = _client_returning(payload)
    usage_log = InMemoryModelUsageLog()
    model = OpenAICompatibleReasoningModel(
        client,
        model="gpt-oss-120b",
        temperature=0.2,
        reasoning_effort="high",
        usage_log=usage_log,
        run_id="run-1",
    )

    response = await model.research(_REQUEST)

    assert response.title == payload["title"]
    assert response.claims[0].evidence_indices == [0]
    assert response.insufficient_evidence is False

    (request,) = captured
    sent = json.loads(request.content)
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][0]["content"] == RESEARCH_INSTRUCTION
    sent_text = sent["messages"][1]["content"]
    assert "Gulf Meridian Bank Q.P.S.C." in sent_text
    assert "[0] Gulf Meridian's board approved" in sent_text
    assert '"programme": "USD 2bn EMTN"' in sent_text
    assert "HAS_PROGRAMME" in sent_text
    assert sent["reasoning_effort"] == "high"
    assert sent["temperature"] == 0.2

    (event,) = usage_log.events
    assert event.component == "research"
    assert event.input_tokens == 300


async def test_research_with_no_evidence_still_produces_a_prompt() -> None:
    """OpportunityResearcher never calls the model with zero evidence (see
    test_insufficient_evidence_signal_returns_nothing in test_agent.py) —
    but the adapter itself must not crash if it ever were asked to, since
    that's a property of the caller, not something this class should
    assume."""
    empty_request = ResearchRequest(
        prompt_version=RESEARCH_PROMPT_VERSION,
        signal_pattern="p",
        entity_name="e",
        evidence_excerpts=[],
        instruction=RESEARCH_INSTRUCTION,
    )
    payload = {
        "title": "t",
        "status": "insufficient_evidence",
        "falsifier": "f",
        "claims": [],
        "insufficient_evidence": True,
    }
    client, captured = _client_returning(payload)
    model = OpenAICompatibleReasoningModel(
        client, "gpt-oss-120b", 0.2, None, InMemoryModelUsageLog(), "run-1"
    )

    response = await model.research(empty_request)

    assert response.insufficient_evidence is True
    (request,) = captured
    sent_text = json.loads(request.content)["messages"][1]["content"]
    assert "no evidence excerpts were retrieved" in sent_text
    assert "reasoning_effort" not in json.loads(request.content)
