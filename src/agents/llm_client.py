"""Unified LLM client with Anthropic + Gemini support and automatic fallback."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Response objects that mirror the Anthropic SDK interface so agents
# don't need any changes.
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class LLMResponse:
    content: list[TextBlock | ToolUseBlock]
    stop_reason: str  # "end_turn" or "tool_use"


# ---------------------------------------------------------------------------
# Gemini adapter — translates Anthropic-style calls to Gemini API
# ---------------------------------------------------------------------------


def _anthropic_tools_to_gemini(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to Gemini function declarations."""
    declarations = []
    for tool in tools:
        schema = tool.get("input_schema", {})
        declarations.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": schema,
        })
    return declarations


def _anthropic_messages_to_gemini(
    messages: list[dict], system: str | None = None
) -> list[dict]:
    """Convert Anthropic-style messages to Gemini contents format."""
    contents = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                contents.append({
                    "role": "user",
                    "parts": [{"text": content}],
                })
            elif isinstance(content, list):
                # Could be tool_results
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        parts.append({
                            "function_response": {
                                "name": item.get("tool_use_id", "unknown"),
                                "response": {"result": item.get("content", "")},
                            }
                        })
                    elif isinstance(item, dict) and "text" in item:
                        parts.append({"text": item["text"]})
                    else:
                        parts.append({"text": str(item)})
                if parts:
                    contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            if isinstance(content, str):
                contents.append({
                    "role": "model",
                    "parts": [{"text": content}],
                })
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if hasattr(block, "type"):
                        if block.type == "text":
                            parts.append({"text": block.text})
                        elif block.type == "tool_use":
                            parts.append({
                                "function_call": {
                                    "name": block.name,
                                    "args": block.input,
                                }
                            })
                    elif isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append({"text": block["text"]})
                        elif block.get("type") == "tool_use":
                            parts.append({
                                "function_call": {
                                    "name": block["name"],
                                    "args": block.get("input", {}),
                                }
                            })
                if parts:
                    contents.append({"role": "model", "parts": parts})

    return contents


def _fix_tool_result_names(messages: list[dict], contents: list[dict]) -> list[dict]:
    """Gemini function_response needs the function name, not the tool_use_id.
    Walk the messages to build an id->name map and patch the contents."""
    id_to_name: dict[str, str] = {}
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if hasattr(block, "type") and block.type == "tool_use":
                id_to_name[block.id] = block.name
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                id_to_name[block.get("id", "")] = block["name"]

    for entry in contents:
        for part in entry.get("parts", []):
            fr = part.get("function_response")
            if fr and fr.get("name", "unknown") in id_to_name:
                fr["name"] = id_to_name[fr["name"]]
            elif fr and fr.get("name") == "unknown":
                # Try to match by position — fallback
                pass
    return contents


def _convert_schema_types(schema: dict) -> dict:
    """Recursively convert JSON Schema 'type' strings to Gemini uppercase format."""
    result = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            result[key] = value.upper()
        elif isinstance(value, dict):
            result[key] = _convert_schema_types(value)
        elif isinstance(value, list):
            result[key] = [
                _convert_schema_types(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


async def _call_gemini(
    api_key: str,
    model: str,
    max_tokens: int,
    system: str | None,
    tools: list[dict] | None,
    messages: list[dict],
) -> LLMResponse:
    """Call Gemini API and return an Anthropic-compatible LLMResponse."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    # Build Gemini-format tools
    gemini_tools = None
    if tools:
        declarations = []
        for tool in tools:
            schema = tool.get("input_schema", {})
            converted = _convert_schema_types(schema)
            declarations.append(types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=converted if converted else None,
            ))
        gemini_tools = [types.Tool(function_declarations=declarations)]

    # Convert messages
    contents = _anthropic_messages_to_gemini(messages, system)
    contents = _fix_tool_result_names(messages, contents)

    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        system_instruction=system if system else None,
        tools=gemini_tools,
    )

    response = await client.aio.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    # Parse response into Anthropic-compatible format
    result_blocks: list[TextBlock | ToolUseBlock] = []
    has_tool_calls = False

    if response.candidates:
        candidate = response.candidates[0]
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if part.function_call and part.function_call.name:
                    has_tool_calls = True
                    args = dict(part.function_call.args) if part.function_call.args else {}
                    result_blocks.append(ToolUseBlock(
                        id=f"toolu_{uuid.uuid4().hex[:24]}",
                        name=part.function_call.name,
                        input=args,
                    ))
                elif part.text:
                    result_blocks.append(TextBlock(text=part.text))

    if not result_blocks:
        result_blocks.append(TextBlock(text="No response generated."))

    return LLMResponse(
        content=result_blocks,
        stop_reason="tool_use" if has_tool_calls else "end_turn",
    )


# ---------------------------------------------------------------------------
# Messages namespace — mimics anthropic_client.messages.create()
# ---------------------------------------------------------------------------


class _MessagesNamespace:
    """Mimics anthropic.AsyncAnthropic().messages with .create()."""

    def __init__(self, provider: str, api_key: str, fallback_provider: str | None = None, fallback_api_key: str | None = None):
        self._provider = provider
        self._api_key = api_key
        self._fallback_provider = fallback_provider
        self._fallback_api_key = fallback_api_key

    async def create(
        self,
        *,
        model: str,
        max_tokens: int = 2048,
        system: str | None = None,
        tools: list[dict] | None = None,
        messages: list[dict] | None = None,
    ) -> LLMResponse:
        """Call the primary provider, fallback on failure."""
        messages = messages or []
        try:
            return await self._call_provider(
                self._provider, self._api_key, model, max_tokens, system, tools, messages
            )
        except Exception as primary_err:
            if self._fallback_provider and self._fallback_api_key:
                logger.warning(
                    "llm_primary_failed_using_fallback",
                    primary=self._provider,
                    fallback=self._fallback_provider,
                    error=str(primary_err),
                )
                fallback_model = _default_model_for(self._fallback_provider)
                return await self._call_provider(
                    self._fallback_provider,
                    self._fallback_api_key,
                    fallback_model,
                    max_tokens,
                    system,
                    tools,
                    messages,
                )
            raise

    async def _call_provider(
        self,
        provider: str,
        api_key: str,
        model: str,
        max_tokens: int,
        system: str | None,
        tools: list[dict] | None,
        messages: list[dict],
    ) -> LLMResponse:
        if provider == "anthropic":
            return await self._call_anthropic(api_key, model, max_tokens, system, tools, messages)
        elif provider == "gemini":
            return await _call_gemini(api_key, model, max_tokens, system, tools, messages)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    async def _call_anthropic(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        system: str | None,
        tools: list[dict] | None,
        messages: list[dict],
    ) -> LLMResponse:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = await client.messages.create(**kwargs)

        # Already in the right format — wrap in our dataclasses for consistency
        blocks: list[TextBlock | ToolUseBlock] = []
        for block in response.content:
            if block.type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                blocks.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return LLMResponse(content=blocks, stop_reason=response.stop_reason)


def _default_model_for(provider: str) -> str:
    if provider == "anthropic":
        return "claude-sonnet-4-20250514"
    elif provider == "gemini":
        return "gemini-2.0-flash"
    return "unknown"


# ---------------------------------------------------------------------------
# Public LLM client — drop-in replacement for anthropic.AsyncAnthropic
# ---------------------------------------------------------------------------


class LLMClient:
    """Unified LLM client. Use like: client.messages.create(model=..., ...)

    Supports automatic fallback: if primary provider fails, tries fallback.
    """

    def __init__(
        self,
        provider: str = "anthropic",
        api_key: str = "",
        fallback_provider: str | None = None,
        fallback_api_key: str | None = None,
    ):
        self.provider = provider
        self.messages = _MessagesNamespace(
            provider=provider,
            api_key=api_key,
            fallback_provider=fallback_provider,
            fallback_api_key=fallback_api_key,
        )

    @classmethod
    def from_config(cls, config) -> "LLMClient":
        """Build LLMClient from OpsLensConfig."""
        primary = config.LLM_PROVIDER
        fallback = config.LLM_FALLBACK_PROVIDER if config.LLM_FALLBACK_PROVIDER else None

        if primary == "anthropic":
            api_key = config.ANTHROPIC_API_KEY
        elif primary == "gemini":
            api_key = config.GEMINI_API_KEY
        else:
            api_key = config.ANTHROPIC_API_KEY

        fallback_key = None
        if fallback == "anthropic":
            fallback_key = config.ANTHROPIC_API_KEY
        elif fallback == "gemini":
            fallback_key = config.GEMINI_API_KEY

        return cls(
            provider=primary,
            api_key=api_key,
            fallback_provider=fallback,
            fallback_api_key=fallback_key,
        )
