from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from typing import Any, Dict, List, Optional

from openai import OpenAI

from pubmed_api import PubMedSearchParams, pubmed_search


SYSTEM_PROMPT = """You are a helpful assistant in a command-line chat.

If the user asks for PubMed search results (articles/papers/studies from PubMed), you MUST call the `pubmed_search` tool.

When returning PubMed results, return ONLY valid JSON (no markdown, no backticks, no extra commentary).
For other questions, answer normally.
"""


def _normalize_pubmed_date(s: Optional[str], *, kind: str) -> Optional[str]:
    """
    Accepts YYYY, YYYY/MM/DD, YYYY-MM-DD and returns YYYY/MM/DD (or None).
    kind: "start" or "end" for year-only defaults.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}", s):
        return f"{s}/01/01" if kind == "start" else f"{s}/12/31"
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return s


def tool_pubmed_search(args: Dict[str, Any]) -> Dict[str, Any]:
    terms = args.get("terms") or []
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ValueError("`terms` must be a list of strings")

    max_results = args.get("max_results", 10)
    pub_date_start = _normalize_pubmed_date(args.get("pub_date_start"), kind="start")
    pub_date_end = _normalize_pubmed_date(args.get("pub_date_end"), kind="end")

    try:
        return pubmed_search(
            PubMedSearchParams(
                terms=terms,
                max_results=int(max_results),
                pub_date_start=pub_date_start,
                pub_date_end=pub_date_end,
                retstart=0,
            )
        )
    except Exception as e:
        return {
            "error": "pubmed_search_failed",
            "message": str(e),
            "traceback": traceback.format_exc(limit=5),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatGPT-powered agent with a PubMed scraping tool.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument(
        "--one-shot",
        help="If set, sends one user message and exits (prints assistant response).",
        default=None,
    )
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("Missing OPENAI_API_KEY in environment.", file=sys.stderr)
        return 2

    client = OpenAI()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "pubmed_search",
                "description": "Fetch PubMed search results using NCBI E-utilities (free PubMed API) and return a JSON object.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Search terms/phrases, e.g. ['older', 'alzheimer', 'factor analysis']",
                        },
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 200, "default": 10},
                        "pub_date_start": {
                            "type": ["string", "null"],
                            "description": "Optional publication start date in YYYY or YYYY/MM/DD.",
                        },
                        "pub_date_end": {
                            "type": ["string", "null"],
                            "description": "Optional publication end date in YYYY or YYYY/MM/DD.",
                        },
                    },
                    "required": ["terms"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def run_turn(user_text: str) -> str:
        nonlocal messages
        messages.append({"role": "user", "content": user_text})

        resp = client.chat.completions.create(
            model=args.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        # Tool calling loop (support multiple calls)
        while getattr(msg, "tool_calls", None):
            messages.append(
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
                result = tool_pubmed_search(call_args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            resp = client.chat.completions.create(
                model=args.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            msg = resp.choices[0].message

        messages.append({"role": "assistant", "content": msg.content or ""})
        return msg.content or ""

    if args.one_shot is not None:
        print(run_turn(args.one_shot))
        return 0

    print("Chat agent ready. Type your message and press Enter. Type 'exit' to quit.")
    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break
        out = run_turn(user_text)
        print(out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

