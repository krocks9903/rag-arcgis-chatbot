"""Standalone smoke test for the Anthropic API.

Run from the backend/ directory (or anywhere) with the project venv:

    backend\\venv\\Scripts\\python.exe backend\\scripts\\test_anthropic.py

Loads ANTHROPIC_API_KEY from backend/.env, sends one minimal request to
claude-haiku-4-5-20251001, and prints latency/token/cost diagnostics.
"""

import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import os

# backend/ (parent of this script's directory) holds pricing.py — the single
# shared source of per-model pricing, also imported by llm_provider.py, so
# the two never drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pricing import estimate_cost_usd  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 64


def main() -> int:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            f"ERROR: ANTHROPIC_API_KEY is not set. Add it to {env_path} "
            "(e.g. ANTHROPIC_API_KEY=sk-ant-...) and re-run this script.",
            file=sys.stderr,
        )
        return 1

    # Import after the key check so a missing `anthropic` package doesn't
    # mask a missing-key error with an unrelated ImportError.
    try:
        import anthropic
    except ImportError:
        print(
            "ERROR: the 'anthropic' package is not installed in this "
            "interpreter. Install it with:\n"
            "  backend\\venv\\Scripts\\python.exe -m pip install anthropic",
            file=sys.stderr,
        )
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    start = time.perf_counter()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": "Say 'ok' and nothing else."}],
        )
    except anthropic.AuthenticationError:
        print(
            "ERROR: Authentication failed (401). The API key was rejected — "
            "verify ANTHROPIC_API_KEY in your .env is correct, active, and "
            "not revoked in the Anthropic Console.",
            file=sys.stderr,
        )
        return 1
    except anthropic.RateLimitError as exc:
        retry_after = exc.response.headers.get("retry-after", "unknown")
        print(
            f"ERROR: Rate limited (429). Retry after {retry_after} seconds, "
            "or check your organization's rate limits at "
            "https://platform.claude.com/settings/limits.",
            file=sys.stderr,
        )
        return 1
    except anthropic.APIConnectionError as exc:
        print(
            f"ERROR: Could not reach the Anthropic API ({exc}). Check your "
            "network connection, proxy settings, and DNS resolution for "
            "api.anthropic.com, then retry.",
            file=sys.stderr,
        )
        return 1
    except anthropic.APIStatusError as exc:
        print(
            f"ERROR: API request failed with status {exc.status_code}: "
            f"{exc.message}",
            file=sys.stderr,
        )
        return 1

    latency_ms = (time.perf_counter() - start) * 1000

    text = "".join(block.text for block in response.content if block.type == "text")
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = estimate_cost_usd(MODEL, input_tokens, output_tokens)

    print(f"Response text : {text!r}")
    print(f"Input tokens  : {input_tokens}")
    print(f"Output tokens : {output_tokens}")
    print(f"Latency       : {latency_ms:.1f} ms")
    print(f"Estimated cost: ${cost:.6f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
