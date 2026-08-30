"""Compatibility entry point for the safe, app.env-owned chat smoke check."""

import asyncio

from deploy.model_smoke import _settings_from_app_env, smoke_chat

if __name__ == "__main__":
    asyncio.run(smoke_chat(_settings_from_app_env()))
