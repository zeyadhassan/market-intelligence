"""Compatibility negotiation for OpenAI-style structured chat output."""

from __future__ import annotations

import json
from typing import Any, Literal

import openai
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from openai.types.chat.chat_completion_system_message_param import (
    ChatCompletionSystemMessageParam,
)
from openai.types.shared_params.response_format_json_object import ResponseFormatJSONObject
from openai.types.shared_params.response_format_json_schema import (
    JSONSchema,
    ResponseFormatJSONSchema,
)

from fi_intel.logging import get_logger, safe_console_error_message, safe_error_summary

StructuredOutputMode = Literal["auto", "json_schema", "json_object", "prompt_json"]
SelectedStructuredOutputMode = Literal["json_schema", "json_object", "prompt_json"]


def decode_structured_json(content: str) -> Any:
    """Decode one JSON value, tolerating only a single Markdown code fence."""

    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        first_newline = candidate.find("\n")
        if first_newline < 0:
            raise ValueError("fenced structured output has no JSON body")
        candidate = candidate[first_newline + 1 : -3].strip()
    return json.loads(candidate)


class StructuredOutputNegotiator:
    """Negotiate bounded structured-output modes for on-prem gateways.

    A number of on-prem OpenAI-compatible gateways implement ``json_object``
    but reject ``json_schema``; others support neither response-format field.
    ``auto`` tries schema, JSON object, then ordinary prompted JSON. The chosen
    mode is reused for the adapter lifetime, and application-side Pydantic
    validation remains mandatory in every mode.
    """

    def __init__(self, mode: StructuredOutputMode = "auto") -> None:
        self._configured_mode = mode
        self._selected_mode: SelectedStructuredOutputMode = (
            mode if mode != "auto" else "json_schema"
        )
        self._log = get_logger(component="model.structured-output")

    @property
    def selected_mode(self) -> SelectedStructuredOutputMode:
        return self._selected_mode

    async def create(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        schema_name: str,
        schema: dict[str, Any],
        temperature: float,
        reasoning_effort: Any,
    ) -> ChatCompletion:
        if self._selected_mode == "prompt_json":
            return await self._create_prompt_json(
                client,
                model=model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
        if self._selected_mode == "json_object":
            return await self._create_json_object_with_fallback(
                client,
                model=model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
        try:
            return await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format=ResponseFormatJSONSchema(
                    type="json_schema",
                    json_schema=JSONSchema(name=schema_name, schema=schema, strict=True),
                ),
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
        except (openai.BadRequestError, openai.UnprocessableEntityError) as exc:
            if self._configured_mode != "auto":
                raise
            self._selected_mode = "json_object"
            self._log.warning(
                "model.structured_output.fallback",
                model=model,
                schema_name=schema_name,
                rejected_mode="json_schema",
                selected_mode="json_object",
                status_code=getattr(exc, "status_code", None),
                error_type=type(exc).__name__,
                error_message=safe_console_error_message(exc),
                safe_error_summary=safe_error_summary(exc),
            )
            return await self._create_json_object_with_fallback(
                client,
                model=model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )

    async def _create_json_object_with_fallback(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        schema_name: str,
        schema: dict[str, Any],
        temperature: float,
        reasoning_effort: Any,
    ) -> ChatCompletion:
        try:
            return await self._create_json_object(
                client,
                model=model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
        except (openai.BadRequestError, openai.UnprocessableEntityError) as exc:
            if self._configured_mode != "auto":
                raise
            self._selected_mode = "prompt_json"
            self._log.warning(
                "model.structured_output.fallback",
                model=model,
                schema_name=schema_name,
                rejected_mode="json_object",
                selected_mode="prompt_json",
                status_code=getattr(exc, "status_code", None),
                error_type=type(exc).__name__,
                error_message=safe_console_error_message(exc),
                safe_error_summary=safe_error_summary(exc),
            )
            return await self._create_prompt_json(
                client,
                model=model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )

    @staticmethod
    async def _create_json_object(
        client: openai.AsyncOpenAI,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        schema_name: str,
        schema: dict[str, Any],
        temperature: float,
        reasoning_effort: Any,
    ) -> ChatCompletion:
        schema_instruction = StructuredOutputNegotiator._schema_instruction(
            schema_name, schema
        )
        return await client.chat.completions.create(
            model=model,
            messages=[schema_instruction, *messages],
            response_format=ResponseFormatJSONObject(type="json_object"),
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    async def _create_prompt_json(
        client: openai.AsyncOpenAI,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        schema_name: str,
        schema: dict[str, Any],
        temperature: float,
        reasoning_effort: Any,
    ) -> ChatCompletion:
        schema_instruction = StructuredOutputNegotiator._schema_instruction(
            schema_name, schema
        )
        return await client.chat.completions.create(
            model=model,
            messages=[schema_instruction, *messages],
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    def _schema_instruction(
        schema_name: str, schema: dict[str, Any]
    ) -> ChatCompletionSystemMessageParam:
        return ChatCompletionSystemMessageParam(
            role="system",
            content=(
                "Return only one JSON object matching this application-validated "
                f"schema ({schema_name}): "
                + json.dumps(schema, sort_keys=True, separators=(",", ":"))
            ),
        )


__all__ = [
    "SelectedStructuredOutputMode",
    "StructuredOutputMode",
    "StructuredOutputNegotiator",
    "decode_structured_json",
]
