"""Stage 1 subscription page and service-free product-loop contracts."""

import pytest
from fastapi.testclient import TestClient

from fi_intel.demo.runner import run_poc_demo
from fi_intel.demo.stage_one_app import DEMO_TOKEN, create_stage_one_demo_app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEMO_TOKEN}"}


@pytest.mark.asyncio
async def test_packaged_poc_analysis_passes_before_the_page_uses_it() -> None:
    artifacts = await run_poc_demo()

    assert artifacts.report.evaluation.passed is True
    assert artifacts.report.evaluation.citation_failure_count == 0
    assert artifacts.report.brief.items


def test_stage_one_page_is_local_simple_and_explicitly_synthetic() -> None:
    client = TestClient(create_stage_one_demo_app())

    page = client.get("/")
    css = client.get("/stage-one/assets/stage-one.css")
    javascript = client.get("/stage-one/assets/stage-one.js")

    assert page.status_code == 200
    assert "Choose what you want to follow" in page.text
    assert "Synthetic fixture" in page.text
    assert "Today's opportunities" in page.text
    assert "http://" not in page.text and "https://" not in page.text
    assert "grid-template-columns" in css.text and "@media" in css.text
    assert "/v1/topics" in javascript.text
    assert "/v1/operations/dashboard" in javascript.text
    assert "/subscription" in javascript.text
    assert "/evaluation" in javascript.text
    assert "Useful" in javascript.text and "Too old" in javascript.text
    assert "stage-one-demo" in javascript.text
    assert "window.sessionStorage" not in javascript.text
    assert page.headers["content-security-policy"].startswith("default-src 'self'")


def test_stage_one_control_room_exposes_runtime_contract() -> None:
    client = TestClient(create_stage_one_demo_app())

    response = client.get("/v1/operations/dashboard", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "fixture"
    assert payload["stages"][0]["stage"] == "fixture"
    assert payload["events"][0]["operation"] == "fixture analysis"
    assert "queue" in payload and "models" in payload and "workers" in payload


def test_stage_one_app_exposes_no_legacy_result_or_brief_path() -> None:
    client = TestClient(create_stage_one_demo_app())

    assert client.get("/workbench").status_code == 404
    assert client.get("/v1/signals", headers=_headers()).status_code == 404
    assert client.post("/v1/briefs", headers=_headers(), json={}).status_code == 404


def test_topic_subscription_analysis_and_evaluation_loop() -> None:
    client = TestClient(create_stage_one_demo_app())

    assert client.get("/v1/topics").status_code == 401
    topics = client.get("/v1/topics", headers=_headers())
    assert topics.status_code == 200
    assert [item["topic_id"] for item in topics.json()] == [
        "upcoming-maturities",
        "ratings-capital-pressure",
    ]
    assert not any(item["subscribed"] for item in topics.json())

    denied = client.get("/v1/topics/upcoming-maturities/results", headers=_headers())
    assert denied.status_code == 403

    subscribed = client.put(
        "/v1/topics/upcoming-maturities/subscription",
        headers=_headers(),
        json={"active": True},
    )
    results = client.get("/v1/topics/upcoming-maturities/results", headers=_headers())

    assert subscribed.status_code == 200
    assert subscribed.json()["active"] is True
    assert results.status_code == 200
    payload = results.json()
    assert payload["analysis_status"] == "complete"
    assert payload["coverage_state"] == "complete"
    assert payload["mode"] == "fixture"
    assert "Synthetic deterministic fixture" in payload["scope_notice"]
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["title"] == (
        "The observed USD 500 million maturity supports immediate DCM coverage."
    )
    assert result["evidence"]
    assert result["latest_evaluation"] is None

    evaluated = client.post(
        f"/v1/results/{result['result_id']}/evaluation",
        headers=_headers(),
        json={"verdict": "useful", "note": "Timely for the coverage team."},
    )
    refreshed = client.get("/v1/topics/upcoming-maturities/results", headers=_headers())

    assert evaluated.status_code == 201
    assert evaluated.json()["verdict"] == "useful"
    assert refreshed.json()["results"][0]["latest_evaluation"] == "useful"


def test_topic_result_projection_does_not_mix_other_patterns() -> None:
    client = TestClient(create_stage_one_demo_app())
    client.put(
        "/v1/topics/ratings-capital-pressure/subscription",
        headers=_headers(),
        json={"active": True},
    )

    response = client.get("/v1/topics/ratings-capital-pressure/results", headers=_headers())

    assert response.status_code == 200
    assert response.json()["coverage_state"] == "complete"
    assert response.json()["message"] == "1 fresh opportunity found"
    assert len(response.json()["results"]) == 1
    assert "cet1" in response.json()["results"][0]["title"].casefold()
