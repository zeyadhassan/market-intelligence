"""Compatibility entry point for the safe, app.env-owned Ollama smoke check."""

import asyncio

from deploy.model_smoke import _settings_from_app_env, smoke_embedding

if __name__ == "__main__":
    asyncio.run(smoke_embedding(_settings_from_app_env()))
