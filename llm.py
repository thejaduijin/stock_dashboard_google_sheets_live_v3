"""
llm.py
LLM-powered debate engine. One combined call per stock produces a six-seat
panel (Bull, Bear, Fundamentals, Technicals, News) + a Judge verdict.

Provider auto-detection priority:
  1. claude_code  - shells out to the `claude` CLI (uses the user's Claude
                     subscription, no API key, no per-call billing)
  2. anthropic    - ANTHROPIC_API_KEY via the Messages API
  3. openai       - OPENAI_API_KEY via Chat Completions

LLM_PROVIDER env var can force one of the three. If every LLM path fails (no
CLI, no keys, network error, bad JSON) this module raises LLMUnavailable and
the caller (app.py) falls back to scoring.evaluate_deterministic().

Grounding rule: every number the LLM cites must be traceable to the evidence
bundle. verify_grounding() does a best-effort numeric-overlap check and flags
anything suspicious - it does not block the response, it annotates it.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.request
import urllib.error

SYSTEM_PROMPT = """You are an equity research panel analyzing an Indian stock for a short-term \
trading dashboard. You are given a JSON "evidence bundle" with price, technicals, analyst, and \
news data. You MUST ONLY cite numbers that appear in the evidence bundle. If a number is not in \
the evidence, say "data unavailable" - never invent or estimate figures.

Panel seats: Bull, Bear, Fundamentalist, Technician, Newsdesk. Each gives a 0-100 conviction \
score and a point of at most 25 words, grounded only in the evidence provided.

Then the Judge weighs the debate and issues a verdict:
- BUY requires genuinely favorable risk/reward WITH confirmation (momentum and/or volume).
- WATCH if the case is promising but unconfirmed.
- AVOID if the picture is poor.

Respond with ONLY a single JSON object (no markdown fences, no prose outside the JSON) with this \
exact shape:
{
  "scores": {
    "bull": {"score": <0-100 int>, "reasons": ["<point>"]},
    "bear": {"score": <0-100 int>, "reasons": ["<point>"]},
    "technician": {"score": <0-100 int>, "reasons": ["<point>"]},
    "fundamentalist": {"score": <0-100 int>, "reasons": ["<point>"]},
    "newsdesk": {"score": <0-100 int>, "reasons": ["<point>"]}
  },
  "verdict": {
    "winner": "Bull" or "Bear",
    "verdict": "BUY" or "WATCH" or "AVOID",
    "confidence": <1-10 int>,
    "rationale": "<=2 lines>",
    "key_catalyst": "<short phrase>",
    "bull_score": <int, same as scores.bull.score>,
    "bear_score": <int, same as scores.bear.score>,
    "net": <int, bull_score - bear_score>
  }
}
"""


class LLMUnavailable(Exception):
    pass


def _extract_json(text):
    """Pull the first {...} JSON object out of a text blob."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMUnavailable("No JSON object found in LLM output")
    return json.loads(text[start:end + 1])


def detect_provider(forced=None):
    """Return the provider name to use, in priority order."""
    if forced in ("claude_code", "anthropic", "openai"):
        return forced
    if shutil.which("claude"):
        return "claude_code"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def _call_claude_code(prompt, model="haiku"):
    if not shutil.which("claude"):
        raise LLMUnavailable("claude CLI not on PATH")
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise LLMUnavailable(f"claude CLI failed: {e}")

    if result.returncode != 0:
        raise LLMUnavailable(f"claude CLI exit {result.returncode}: {result.stderr[:200]}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise LLMUnavailable("claude CLI did not return valid JSON envelope")

    if envelope.get("is_error"):
        raise LLMUnavailable(f"claude CLI error: {envelope.get('result')}")

    return envelope.get("result", "")


def _call_anthropic_api(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")

    body = json.dumps({
        "model": "claude-3-5-haiku-20241022",
        "max_tokens": 1200,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise LLMUnavailable(f"Anthropic API request failed: {e}")

    text_parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(text_parts)


def _call_openai_api(prompt):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMUnavailable("OPENAI_API_KEY not set")

    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise LLMUnavailable(f"OpenAI API request failed: {e}")

    return data["choices"][0]["message"]["content"]


def _evidence_numbers(evidence):
    """Collect every numeric value present in the evidence bundle (rounded str forms)."""
    nums = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, (int, float)):
            nums.add(str(round(v)))
            nums.add(str(round(v, 1)))
            nums.add(str(round(v, 2)))

    walk(evidence)
    return nums


def verify_grounding(result, evidence):
    """
    Best-effort check: scan every reason string + rationale for standalone
    numbers, and flag any that don't appear anywhere in the evidence bundle's
    numeric values. Annotates result['_ungrounded_flags'] rather than raising,
    since this is a heuristic, not a proof.
    """
    known = _evidence_numbers(evidence)
    flags = []

    def check_text(text, label):
        for m in re.findall(r"-?\d+\.?\d*", text or ""):
            if m and m not in known and len(m) > 1:
                flags.append(f"{label}: '{m}' not found in evidence")

    scores = result.get("scores", {})
    for agent, s in scores.items():
        for r in s.get("reasons", []):
            check_text(r, f"{agent}.reasons")
    verdict = result.get("verdict", {})
    check_text(verdict.get("rationale", ""), "verdict.rationale")
    check_text(verdict.get("key_catalyst", ""), "verdict.key_catalyst")

    result["_ungrounded_flags"] = flags[:5]
    return result


def evaluate_llm(evidence, provider=None):
    """Run the LLM debate for one stock's evidence bundle. Raises LLMUnavailable on any failure."""
    forced = provider or os.environ.get("LLM_PROVIDER")
    chosen = detect_provider(forced)
    if chosen is None:
        raise LLMUnavailable("No LLM provider available (no CLI, no API keys)")

    prompt = (
        f"{SYSTEM_PROMPT}\n\nEvidence bundle for {evidence.get('symbol')} "
        f"({evidence.get('name')}):\n{json.dumps(evidence, default=str)}\n\n"
        "Respond with only the JSON object described above."
    )

    if chosen == "claude_code":
        raw = _call_claude_code(prompt)
    elif chosen == "anthropic":
        raw = _call_anthropic_api(prompt)
    elif chosen == "openai":
        raw = _call_openai_api(prompt)
    else:
        raise LLMUnavailable(f"Unknown provider {chosen}")

    result = _extract_json(raw)

    if "scores" not in result or "verdict" not in result:
        raise LLMUnavailable("LLM JSON missing required keys")

    result = verify_grounding(result, evidence)
    result["_engine"] = f"llm:{chosen}"
    return result


def evaluate(evidence, provider=None):
    """
    Public interface: evidence -> {scores, verdict}. Tries the LLM first (if
    a provider is available), falls back to the deterministic engine on any
    failure. Callers should catch nothing - this never raises.
    """
    import scoring
    try:
        return evaluate_llm(evidence, provider=provider)
    except LLMUnavailable:
        result = scoring.evaluate_deterministic(evidence)
        result["_engine"] = "deterministic"
        return result
