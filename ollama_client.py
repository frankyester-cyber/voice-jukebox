"""
ollama_client.py  –  Jukebox Ollama Interface
==============================================
Sends user text to a local Ollama model and returns a structured JSON
command dict that the jukebox controller can act on.

Structured JSON schema returned:
{
  "action":     "play" | "stop" | "skip" | "queue",
  "continuous": true | false,
  "filters": {
    "genre":   "<string>" | null,
    "artist":  "<string>" | null,
    "album":   "<string>" | null,
    "title":   "<string>" | null
  },
  "sort_by":    "title" | "artist" | "album" | "album_number" | "genre" | null,
  "sort_order": "asc" | "desc" | "random" | null,
  "skip_by":    "track" | "album" | "artist" | null,
  "raw_query":  "<original user text>"
}
"""

import json
import re
import requests

try:
    from fuzzy_client import ask_fuzzy
    HAS_FUZZY_CLIENT = True
except ImportError:
    HAS_FUZZY_CLIENT = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OLLAMA_HOST  = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:1.5b-instruct"   # change here if you switch models

# ---------------------------------------------------------------------------
# System prompt  –  kept concise so small models don't get confused
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a music player controller. Extract the user's intent and return ONLY a JSON object.

JSON schema:
{
  "action": "play" | "stop" | "skip" | "queue",
  "continuous": true | false,
  "filters": {
    "genre": string or null,
    "artist": string or null,
    "album": string or null,
    "title": string or null
  },
  "sort_by": "title" | "artist" | "album" | "album_number" | "genre" | null,
  "sort_order": "asc" | "desc" | "random" | null,
  "skip_by": "track" | "album" | "artist" | null,
  "raw_query": string
}

Rules:
- action "play"  → start playing music
- action "stop"  → stop all playback
- action "skip"  → skip forward in the queue
- action "queue" → add tracks to queue without stopping current track
- action "unknown" → return this if the input is conversational, out of domain, or does not clearly request music playback or control.
- continuous: true means keep advancing through tracks automatically
- filters: set any mentioned artist / genre / album / title; null otherwise. 
  - IMPORTANT: You may ONLY extract a genre if it is exactly one of these: ["classical", "rock", "irish", "christmas", "religious"]. If the user mentions any other genre, ignore it and set genre to null.
- sort_by + sort_order: extract if mentioned; use "random" order when user says shuffle/random
- skip_by: what to skip over (default is "album")
  - "track"  → skip one track
  - "album"  → skip all remaining tracks on the current album (default when user says "skip")
  - "artist" → skip all remaining tracks by the current artist
- raw_query: copy the user's original text exactly

Examples:

Input: "play some jazz"
Output: {"action":"play","continuous":true,"filters":{"genre":"jazz","artist":null,"album":null,"title":null},"sort_by":null,"sort_order":"random","skip_by":null,"raw_query":"play some jazz"}

Input: "play Beatles sorted by album"
Output: {"action":"play","continuous":true,"filters":{"genre":null,"artist":"Beatles","album":null,"title":null},"sort_by":"album","sort_order":"asc","skip_by":null,"raw_query":"play Beatles sorted by album"}

Input: "shuffle all rock tracks"
Output: {"action":"play","continuous":true,"filters":{"genre":"rock","artist":null,"album":null,"title":null},"sort_by":null,"sort_order":"random","skip_by":null,"raw_query":"shuffle all rock tracks"}

Input: "stop"
Output: {"action":"stop","continuous":false,"filters":{"genre":null,"artist":null,"album":null,"title":null},"sort_by":null,"sort_order":null,"skip_by":null,"raw_query":"stop"}

Input: "skip"
Output: {"action":"skip","continuous":false,"filters":{"genre":null,"artist":null,"album":null,"title":null},"sort_by":null,"sort_order":null,"skip_by":"album","raw_query":"skip"}

Input: "skip this track"
Output: {"action":"skip","continuous":false,"filters":{"genre":null,"artist":null,"album":null,"title":null},"sort_by":null,"sort_order":null,"skip_by":"track","raw_query":"skip this track"}

Input: "skip the rest of this artist"
Output: {"action":"skip","continuous":false,"filters":{"genre":null,"artist":null,"album":null,"title":null},"sort_by":null,"sort_order":null,"skip_by":"artist","raw_query":"skip the rest of this artist"}

Input: "thank you george"
Output: {"action":"unknown","continuous":false,"filters":{"genre":null,"artist":null,"album":null,"title":null},"sort_by":null,"sort_order":null,"skip_by":null,"raw_query":"thank you george"}

Input: "hello how are you"
Output: {"action":"unknown","continuous":false,"filters":{"genre":null,"artist":null,"album":null,"title":null},"sort_by":null,"sort_order":null,"skip_by":null,"raw_query":"hello how are you"}

Output ONLY the JSON object. No explanation, no markdown, no extra text.\
"""

# ---------------------------------------------------------------------------
# Default / fallback command used when parsing fails
# ---------------------------------------------------------------------------
DEFAULT_COMMAND = {
    "action":     "play",
    "continuous": True,
    "filters":    {"genre": None, "artist": None, "album": None, "title": None},
    "sort_by":    None,
    "sort_order": "random",
    "skip_by":    None,
    "raw_query":  ""
}

# ---------------------------------------------------------------------------
# Valid values for each field  –  used in validation
# ---------------------------------------------------------------------------
VALID_ACTIONS     = {"play", "stop", "skip", "queue"}
VALID_SORT_BY     = {"title", "artist", "album", "album_number", "genre", None}
VALID_SORT_ORDER  = {"asc", "desc", "random", None}
VALID_SKIP_BY     = {"track", "album", "artist", None}
VALID_FILTER_KEYS = {"genre", "artist", "album", "title"}


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def ask_ollama(user_text: str, verbose: bool = True) -> dict:
    """
    Send *user_text* to the local Ollama model and return a validated
    command dict.  Falls back to DEFAULT_COMMAND on any error.

    Parameters
    ----------
    user_text : str
        Raw text typed (or spoken) by the user.
    verbose : bool
        Print debug information to stdout.

    Returns
    -------
    dict
        Validated command dict matching the schema above.
    """
    print("\n" + "=" * 60)
    print("[ollama] ── Ollama Client ──────────────────────────────────")
    print(f"[ollama] Model   : {OLLAMA_MODEL}")
    print(f"[ollama] Host    : {OLLAMA_HOST}")
    print(f"[ollama] Input   : {user_text!r}")

    # ── 0. Fast Fuzzy Catalog Matcher (<0.005s response time) ─────────────
    if HAS_FUZZY_CLIENT:
        fuzzy_cmd = ask_fuzzy(user_text, verbose=False)
        # If fuzzy matcher found a specific action or entity filter, return instantly
        if fuzzy_cmd and (fuzzy_cmd["action"] in ("stop", "skip", "queue") or any(fuzzy_cmd["filters"].values())):
            if verbose:
                print("[ollama] Match  : Instant Fuzzy Catalog Matcher triggered (<0.005s)")
            print("[ollama] Final parsed command:")
            _pretty_print_command(fuzzy_cmd)
            print("=" * 60)
            return fuzzy_cmd

    quick_cmd = _quick_parse(user_text)
    if quick_cmd:
        if verbose:
            print("[ollama] Match  : Instant regex rule triggered (bypassed Ollama)")
        print("[ollama] Final parsed command:")
        _pretty_print_command(quick_cmd)
        print("=" * 60)
        return quick_cmd
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": user_text,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "format": "json",          # forces Ollama to return valid JSON
        "keep_alive": -1,          # Keeps model loaded in RAM indefinitely (-1)
        "options": {
            "temperature": 0.0,    # deterministic output for small models
            "num_predict": 256     # more than enough for our schema
        }
    }

    if verbose:
        print("[ollama] Sending request to Ollama API ...")

    # ── 2. Call Ollama REST API ───────────────────────────────────────────
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=120
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"[ollama] ERROR: Cannot connect to Ollama at {OLLAMA_HOST}")
        print("[ollama]        Is Ollama running?  Try:  ollama serve")
        return _fallback(user_text, "connection error")
    except requests.exceptions.Timeout:
        print("[ollama] ERROR: Request timed out after 30 s")
        return _fallback(user_text, "timeout")
    except requests.exceptions.HTTPError as e:
        print(f"[ollama] ERROR: HTTP {response.status_code} – {e}")
        return _fallback(user_text, f"HTTP {response.status_code}")

    # ── 3. Extract raw response text ──────────────────────────────────────
    try:
        result     = response.json()
        raw_text   = result.get("response", "").strip()
        eval_count = result.get("eval_count", "?")
        total_dur  = result.get("total_duration", 0) / 1e9  # ns → seconds
    except Exception as e:
        print(f"[ollama] ERROR: Could not decode Ollama response body: {e}")
        return _fallback(user_text, "bad response body")

    if verbose:
        print(f"[ollama] Raw response ({eval_count} tokens, {total_dur:.2f}s):")
        print(f"[ollama]   {raw_text}")

    # ── 4. Parse JSON ─────────────────────────────────────────────────────
    try:
        cmd = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"[ollama] ERROR: JSON parse failed – {e}")
        print(f"[ollama]        Offending text: {raw_text!r}")
        return _fallback(user_text, "JSON parse error")

    # ── 5. Validate & sanitise ────────────────────────────────────────────
    cmd = _validate(cmd, user_text, verbose)

    print("[ollama] Final parsed command:")
    _pretty_print_command(cmd)
    print("=" * 60)

    return cmd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quick_parse(user_text: str) -> dict | None:
    """Fast regex matching to bypass Ollama for high-frequency phrases (<0.001s)."""
    t = user_text.lower().strip()

    # 1. Stop
    if t in ("stop", "pause", "halt"):
        return {
            "action": "stop",
            "continuous": False,
            "filters": {"genre": None, "artist": None, "album": None, "title": None},
            "sort_by": None,
            "sort_order": None,
            "skip_by": None,
            "raw_query": user_text
        }

    # 2. Skip (defaults skip_by to 'album')
    m_skip = re.match(r"^(skip|next)(\s+(this|the\s+rest\s+of\s+this)?\s*(album|artist|track)?)?$", t)
    if m_skip:
        unit = m_skip.group(4)
        skip_by = unit if unit in ("album", "artist", "track") else "album"
        return {
            "action": "skip",
            "continuous": False,
            "filters": {"genre": None, "artist": None, "album": None, "title": None},
            "sort_by": None,
            "sort_order": None,
            "skip_by": skip_by,
            "raw_query": user_text
        }

    # 3. Simple play/shuffle genre (e.g., "play some jazz", "shuffle rock")
    m_play = re.match(r"^(play|shuffle)\s+(some\s+)?([a-z0-9\s]+)$", t)
    if m_play and not any(k in t for k in ("by", "sorted", "album", "artist", "track")):
        verb, _, target = m_play.groups()
        target = target.strip()
        sort_order = "random" if verb == "shuffle" or "some" in t else None
        return {
            "action": "play",
            "continuous": True,
            "filters": {"genre": target, "artist": None, "album": None, "title": None},
            "sort_by": None,
            "sort_order": sort_order,
            "skip_by": None,
            "raw_query": user_text
        }

    return None

def _validate(cmd: dict, user_text: str, verbose: bool) -> dict:
    """Validate and sanitise a parsed command dict in-place."""
    issues = []

    # action
    if cmd.get("action") not in VALID_ACTIONS:
        issues.append(f"invalid action {cmd.get('action')!r} → defaulting to 'play'")
        cmd["action"] = "play"

    # continuous
    if not isinstance(cmd.get("continuous"), bool):
        cmd["continuous"] = True

    # filters
    if not isinstance(cmd.get("filters"), dict):
        cmd["filters"] = {k: None for k in VALID_FILTER_KEYS}
    else:
        for key in VALID_FILTER_KEYS:
            val = cmd["filters"].get(key)
            if val == "" or val == "null":
                cmd["filters"][key] = None
            elif val is not None:
                cmd["filters"][key] = str(val)

    # sort_by
    if cmd.get("sort_by") not in VALID_SORT_BY:
        issues.append(f"invalid sort_by {cmd.get('sort_by')!r} → None")
        cmd["sort_by"] = None

    # sort_order
    if cmd.get("sort_order") not in VALID_SORT_ORDER:
        issues.append(f"invalid sort_order {cmd.get('sort_order')!r} → None")
        cmd["sort_order"] = None

    # skip_by – default to "album" when action is skip and skip_by is missing or invalid
    if cmd.get("skip_by") not in VALID_SKIP_BY:
        issues.append(f"invalid skip_by {cmd.get('skip_by')!r} → 'album'")
        cmd["skip_by"] = "album"
    if cmd.get("action") == "skip" and cmd.get("skip_by") is None:
        cmd["skip_by"] = "album"

    # raw_query – always preserve original user text
    cmd["raw_query"] = user_text

    if issues and verbose:
        for issue in issues:
            print(f"[ollama] VALIDATION: {issue}")

    return cmd


# ---------------------------------------------------------------------------
# Default fallback command generator
# ---------------------------------------------------------------------------

def _fallback(user_text: str, reason: str) -> dict:
    """Return a safe default command and log the reason."""
    print(f"[ollama] Using fallback command (reason: {reason})")
    cmd = dict(DEFAULT_COMMAND)
    cmd["filters"] = dict(DEFAULT_COMMAND["filters"])
    cmd["raw_query"] = user_text
    return cmd


def _pretty_print_command(cmd: dict):
    """Print the command dict in a readable multi-line format."""
    print(f"[ollama]   action     : {cmd['action']}")
    print(f"[ollama]   continuous : {cmd['continuous']}")
    print(f"[ollama]   filters    : genre={cmd['filters'].get('genre')!r}  "
          f"artist={cmd['filters'].get('artist')!r}  "
          f"album={cmd['filters'].get('album')!r}  "
          f"title={cmd['filters'].get('title')!r}")
    print(f"[ollama]   sort_by    : {cmd['sort_by']}")
    print(f"[ollama]   sort_order : {cmd['sort_order']}")
    print(f"[ollama]   skip_by    : {cmd.get('skip_by')}")
    print(f"[ollama]   raw_query  : {cmd['raw_query']!r}")


# ---------------------------------------------------------------------------
# Quick standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_inputs = [
        "play some jazz",
        "play Beatles sorted by album ascending",
        "shuffle all rock tracks",
        "stop",
        "skip",
        "skip this album",
        "skip the rest of this artist",
        "play everything by Miles Davis",
    ]
    for text in test_inputs:
        result = ask_ollama(text)
        print()
