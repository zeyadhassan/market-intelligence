"""Contracts for automatic local-product configuration and startup."""

from pathlib import Path

from deploy import bootstrap, product
from fi_intel.application.preflight import canonical_configuration_errors


def test_environment_round_trip_keeps_only_current_template_keys() -> None:
    template = "# heading\nFI_INTEL_ONE=old\nFI_INTEL_TWO=keep\n"

    rendered = product._render_environment(
        template,
        {"FI_INTEL_ONE": "new value", "FI_INTEL_TWO": "keep", "FI_INTEL_OLD": "gone"},
    )

    assert rendered == "# heading\nFI_INTEL_ONE=new value\nFI_INTEL_TWO=keep\n"
    assert "FI_INTEL_OLD" not in rendered


def test_first_run_copies_ready_configuration_without_prompts(tmp_path: Path) -> None:
    target = tmp_path / "app.env"
    template = tmp_path / "app.env.example"
    template.write_text(
        Path("deploy/app.env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )

    settings = product.configure_environment(target, template)

    assert target.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")
    assert canonical_configuration_errors(settings) == ()
    assert settings.embedding_model == "nvidia/llama-3.2-nv-embedqa-1b-v2"
    assert settings.embedding_dim == 2048
    assert settings.api_host_port == 8000
    assert product._product_url(settings) == "http://127.0.0.1:8000/"
    assert settings.source_http_proxy == "http://proxy2.cbq.com.qa:3128"
    assert settings.source_https_proxy == "http://proxy2.cbq.com.qa:3128"
    assert ".cbq.com.qa" in settings.source_no_proxy


def test_existing_configuration_is_upgraded_and_preserves_model_values(tmp_path: Path) -> None:
    target = tmp_path / "app.env"
    template = tmp_path / "app.env.example"
    template.write_text(
        Path("deploy/app.env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    target.write_text(
        template.read_text(encoding="utf-8").replace(
            "FI_INTEL_LLM_API_KEY=not-needed", "FI_INTEL_LLM_API_KEY=private-key"
        )
        + "FI_INTEL_OIDC_ISSUER=https://obsolete.example\n"
        + "FI_INTEL_MODEL_EVALUATION_DATASET_DIGEST="
        + "a" * 64
        + "\n",
        encoding="utf-8",
    )

    product.configure_environment(target, template)

    configured = target.read_text(encoding="utf-8")
    assert "FI_INTEL_LLM_API_KEY=private-key" in configured
    assert "FI_INTEL_SOURCE_HTTP_PROXY=http://proxy2.cbq.com.qa:3128" in configured
    assert "OIDC" not in configured
    assert "EVALUATION_DATASET" not in configured


def test_first_run_bootstrap_uses_template_proxy_before_private_env_exists(
    tmp_path: Path,
) -> None:
    template = tmp_path / "app.env.example"
    template.write_text(
        "FI_INTEL_SOURCE_HTTP_PROXY=http://proxy.example:3128\n"
        "FI_INTEL_SOURCE_HTTPS_PROXY=http://proxy.example:3128\n"
        "FI_INTEL_SOURCE_NO_PROXY=localhost,internal.example\n",
        encoding="utf-8",
    )

    environment = bootstrap._bootstrap_environment(
        tmp_path / "missing.env",
        template,
        base={"PATH": "existing-path"},
    )

    assert environment["HTTP_PROXY"] == "http://proxy.example:3128"
    assert environment["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert environment["NO_PROXY"] == "localhost,internal.example"
