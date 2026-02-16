from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from django.conf import settings
from openai import OpenAI

from .pubmed_api import PubMedSearchParams, pubmed_search


SYSTEM_PROMPT = """You are a helpful assistant.

If the user asks for PubMed search results (articles/papers/studies from PubMed), you MUST call the `pubmed_search` tool.

When returning PubMed results, return ONLY valid JSON (no markdown, no backticks, no extra commentary).
For other questions, answer normally.
"""


def _get_client() -> OpenAI:
    if not getattr(settings, "OPENAI_API_KEY", ""):
        raise RuntimeError("Missing OPENAI_API_KEY in backend environment.")
    kwargs = {"api_key": settings.OPENAI_API_KEY}
    if getattr(settings, "DISABLE_SSL_VERIFY", False):
        kwargs["http_client"] = httpx.Client(verify=False)
    return OpenAI(**kwargs)


def chat_with_tools(
    *,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> str:
    """
    Runs one assistant turn with tool calling. Returns assistant content.
    The caller is responsible for storing conversation history.
    """
    client = _get_client()
    model = model or getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    tool_def = [
        {
            "type": "function",
            "function": {
                "name": "pubmed_search",
                "description": "Fetch PubMed search results using NCBI E-utilities (free PubMed API) and return a JSON object.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "terms": {"type": "array", "items": {"type": "string"}},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 200, "default": 10},
                        "pub_date_start": {"type": ["string", "null"], "description": "YYYY or YYYY/MM/DD"},
                        "pub_date_end": {"type": ["string", "null"], "description": "YYYY or YYYY/MM/DD"},
                    },
                    "required": ["terms"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    full_messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    resp = client.chat.completions.create(
        model=model,
        messages=full_messages,
        tools=tool_def,
        tool_choice="auto",
    )
    msg = resp.choices[0].message

    # Tool calling loop
    while getattr(msg, "tool_calls", None):
        full_messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            }
        )
        for tc in msg.tool_calls:
            if tc.function.name != "pubmed_search":
                raise RuntimeError(f"Unknown tool: {tc.function.name}")
            call_args = json.loads(tc.function.arguments or "{}")
            params = PubMedSearchParams(
                terms=call_args.get("terms") or [],
                max_results=int(call_args.get("max_results", 10)),
                pub_date_start=call_args.get("pub_date_start"),
                pub_date_end=call_args.get("pub_date_end"),
            )
            result = pubmed_search(params)
            full_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        resp = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=tool_def,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

    return msg.content or ""

