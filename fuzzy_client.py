"""
fuzzy_client.py  –  Fast Local Jukebox Intent & Music Entity Matcher
====================================================================
Sub-10ms intent parser designed for Raspberry Pi 5.
Matches user query against local music library catalog (artists, albums, genres, tracks)
using fuzzy string similarity.
"""

import re
import difflib
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Default Fallback Command
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
# Standard Jukebox Vocabularies
# ---------------------------------------------------------------------------
STANDARD_GENRES = {
    "jazz", "rock", "pop", "classical", "blues", "hip hop", "rap", "country",
    "folk", "reggae", "metal", "punk", "electronic", "ambient", "indie", "soul"
}

SORT_KEYWORDS = {
    "album": "album",
    "title": "title",
    "track": "title",
    "song": "title",
    "artist": "artist",
    "genre": "genre"
}


class FuzzyJukeboxParser:
    """
    Fast, deterministic intent parser that matches user text against 
    a local catalog of artists, albums, genres, and track titles.
    """

    def __init__(
        self,
        artists: Optional[List[str]] = None,
        albums: Optional[List[str]] = None,
        genres: Optional[List[str]] = None,
        titles: Optional[List[str]] = None,
        playlists: Optional[List[str]] = None
    ):
        self.artists   = [a.lower() for a in (artists or ["The Beatles", "Miles Davis", "Pink Floyd", "Nirvana", "John Coltrane"])]
        self.albums    = [a.lower() for a in (albums or ["Abbey Road", "Kind of Blue", "Dark Side of the Moon", "Nevermind", "Blue Train"])]
        self.genres    = list(STANDARD_GENRES.union(set([g.lower() for g in genres] if genres else [])))
        self.titles    = [a.lower() for a in (titles or ["Come Together", "So What", "Time", "Smells Like Teen Spirit"])]
        self.playlists = [p.lower() for p in (playlists or [])]

    def update_catalog(
        self,
        artists: Optional[List[str]] = None,
        albums: Optional[List[str]] = None,
        genres: Optional[List[str]] = None,
        titles: Optional[List[str]] = None,
        playlists: Optional[List[str]] = None
    ):
        """Update known music entities from your jukebox SQLite DB / MP3 tags."""
        if artists is not None:   self.artists   = [a.lower() for a in artists if a]
        if albums is not None:    self.albums    = [a.lower() for a in albums if a]
        if genres is not None:    self.genres    = list(STANDARD_GENRES.union(set([g.lower() for g in genres if g])))
        if titles is not None:    self.titles    = [t.lower() for t in titles if t]
        if playlists is not None: self.playlists = [p.lower() for p in playlists if p]

    def parse(self, user_text: str, verbose: bool = True) -> dict:
        """
        Parse user query into structured command dict in < 0.005s.
        """
        raw_text = user_text
        t = user_text.lower().strip()

        cmd = {
            "action":     "play",
            "continuous": True,
            "filters":    {"genre": None, "artist": None, "album": None, "title": None},
            "sort_by":    None,
            "sort_order": None,
            "skip_by":    None,
            "raw_query":  raw_text
        }

        # ── 1. Stop / Pause Intent ──────────────────────────────────────────
        if any(w in t for w in ["stop", "pause", "halt", "turn off"]):
            cmd["action"] = "stop"
            cmd["continuous"] = False
            cmd["sort_order"] = None
            return cmd

        # ── 2. Skip Intent (Defaults skip_by to 'album') ────────────────────
        if any(w in t for w in ["skip", "next"]):
            cmd["action"] = "skip"
            cmd["continuous"] = False
            cmd["sort_order"] = None
            
            if "track" in t or "song" in t:
                cmd["skip_by"] = "track"
            elif "artist" in t:
                cmd["skip_by"] = "artist"
            else:
                cmd["skip_by"] = "album"  # Default skip behavior
            return cmd

        # ── 2.5 Playlist Intent ─────────────────────────────────────────────
        if "playlist" in t and self.playlists:
            import difflib
            # Extract whatever comes after 'playlist' (or just fuzzy match the whole string against playlists)
            target = t.replace("play", "").replace("playlist", "").strip()
            if not target: target = t
            matches = difflib.get_close_matches(target, self.playlists, n=1, cutoff=0.5)
            if matches:
                cmd["action"] = "playlist"
                cmd["playlist_name"] = matches[0]
                if verbose:
                    print(f"[fuzzy] Playlist match: '{matches[0]}'")
                return cmd

        # ── 3. Queue Intent ─────────────────────────────────────────────────
        if "queue" in t or "add to queue" in t:
            cmd["action"] = "queue"

        # ── 4. Sort Order & Mode ───────────────────────────────────────────
        if "shuffle" in t or "random" in t or "mix" in t:
            cmd["sort_order"] = "random"
        elif "descending" in t or "desc" in t:
            cmd["sort_order"] = "desc"
        elif "ascending" in t or "asc" in t or "sorted" in t:
            cmd["sort_order"] = "asc"

        # Extract sort key
        for kw, key in SORT_KEYWORDS.items():
            if f"by {kw}" in t or f"sort by {kw}" in t or f"sorted by {kw}" in t:
                cmd["sort_by"] = key
                break

        # ── 5. Entity Extraction (Genre, Artist, Album, Title) ──────────────
        clean_target = re.sub(r"\b(play|shuffle|queue|add|to|some|all|tracks|songs|song|track|music|by|artist|album|genre|sorted|asc|desc|ascending|descending|random)\b", "", t).strip()

        if clean_target:
            matched_entity, entity_type = self._find_best_match(clean_target)
            if matched_entity:
                cmd["filters"][entity_type] = matched_entity

        # Fallback sort order if none specified for play
        if cmd["action"] == "play" and cmd["sort_order"] is None:
            cmd["sort_order"] = "random" if "some" in t else None

        if verbose:
            print("\n" + "=" * 60)
            print("[fuzzy] ── Fast Intent Parser (<0.005s) ───────────────────")
            print(f"[fuzzy] Input   : {user_text!r}")
            print(f"[fuzzy] Action  : {cmd['action']}")
            print(f"[fuzzy] Filters : {cmd['filters']}")
            print(f"[fuzzy] Skip By : {cmd['skip_by']}")
            print("=" * 60)

        return cmd

    def _find_best_match(self, query_str: str) -> Tuple[Optional[str], Optional[str]]:
        """Match query_str against artists, albums, genres, and titles using fuzzy ratio."""
        best_match = None
        best_type = None

        candidates = [
            ("genre", self.genres),
            ("artist", self.artists),
            ("album", self.albums),
            ("title", self.titles),
        ]

        for entity_type, target_list in candidates:
            if not target_list:
                continue

            matches = difflib.get_close_matches(query_str, target_list, n=1, cutoff=0.7)
            if matches:
                best_match = matches[0]
                best_type = entity_type
                break

        return best_match, best_type


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
default_parser = FuzzyJukeboxParser()

def ask_fuzzy(user_text: str, verbose: bool = True) -> dict:
    """Convenience function matching ask_ollama signature."""
    return default_parser.parse(user_text, verbose=verbose)


# ---------------------------------------------------------------------------
# Quick Test Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import time

    test_inputs = [
        "play some jazz",
        "play beatls sorted by album",
        "shuffle rock",
        "stop",
        "skip",
        "skip track",
        "skip artist",
        "play Abbey Road",
        "play miles devis"  # Voice STT typo test
    ]

    print("Running speed & accuracy test on Pi 5 fuzzy parser...")
    start_time = time.perf_counter()
    for text in test_inputs:
        res = ask_fuzzy(text, verbose=True)
    total_time = (time.perf_counter() - start_time) * 1000

    print(f"\nCompleted {len(test_inputs)} queries in {total_time:.2f} ms total!")
    print(f"Average time per query: {total_time / len(test_inputs):.3f} ms")
