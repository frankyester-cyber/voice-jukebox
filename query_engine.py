"""
query_engine.py  –  Jukebox SQLite Query Engine
================================================
Translates a parsed Ollama command dict into SQL queries against
music_catalog.db and returns a list of file paths to play.

Two query strategies are used in order:
  1. Metadata filter query  (genre / artist / album / title + sort)
  2. Vector similarity fallback  (if strategy 1 returns 0 results)
"""

import json
import math
import os
import random
import sqlite3

# ---------------------------------------------------------------------------
# Configuration  –  update DB_PATH to match your Pi5 setup
# ---------------------------------------------------------------------------
DB_PATH = "/home/frank/jukebox-env/music_catalog.db"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def query_tracks(command: dict, db_path: str = DB_PATH) -> list[dict]:
    """
    Convert a command dict into a list of track dicts.

    Parameters
    ----------
    command : dict
        Validated command dict produced by ollama_client.ask_ollama().
    db_path : str
        Path to the SQLite database.

    Returns
    -------
    list[dict]
        Ordered list of track dicts, each with keys:
        id, path, title, artist, album, album_number, genre
        Returns an empty list if nothing matches.
    """
    print("\n" + "=" * 60)
    print("[query] ── Query Engine ────────────────────────────────────")
    print(f"[query] Database : {db_path}")

    if not os.path.isfile(db_path):
        print(f"[query] ERROR: Database not found at {db_path}")
        print("[query]        Run indexer.py first to build the catalog.")
        return []

    if command.get("action") == "playlist":
        playlist_name = command.get("playlist_name", "")
        print(f"[query] Executing Playlist: {playlist_name}")
        playlist_dir = "/home/frank/Music/Playlists"
        
        target_file = None
        if os.path.isdir(playlist_dir):
            for f in os.listdir(playlist_dir):
                if os.path.splitext(f)[0].lower() == playlist_name.lower():
                    target_file = os.path.join(playlist_dir, f)
                    break
        
        if not target_file:
            print(f"[query] ERROR: Playlist file not found for '{playlist_name}'")
            return []
            
        print(f"[query] Reading playlist from {target_file}")
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        tracks = []
        
        try:
            with open(target_file, "r", encoding="utf-8") as pf:
                for line in pf:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    
                    cursor = conn.execute(
                        "SELECT id, path, title, artist, album, album_number, genre FROM tracks WHERE path LIKE ?", 
                        (f"%{line}%",)
                    )
                    rows = [dict(r) for r in cursor.fetchall()]
                    # Sort tracks within this album sequentially
                    def _sort_key_internal(t):
                        num_str = str(t.get("album_number", "") or "").strip()
                        num_val = float('inf')
                        if num_str:
                            import re
                            match = re.search(r'\d+', num_str)
                            if match: num_val = int(match.group())
                        return (num_val, t.get("path", ""))
                    rows.sort(key=_sort_key_internal)
                    tracks.extend(rows)
        except Exception as e:
            print(f"[query] ERROR reading playlist: {e}")
            
        conn.close()
        
        if tracks:
            print(f"[query] Found {len(tracks)} track(s) in playlist")
            _print_track_list(tracks)
        else:
            print("[query] No tracks found matching the folders in this playlist.")
            
        print("=" * 60)
        return tracks

    filters    = command.get("filters", {})
    sort_by    = command.get("sort_by")
    sort_order = command.get("sort_order")
    raw_query  = command.get("raw_query", "")

    print(f"[query] Filters    : {filters}")
    print(f"[query] Sort by    : {sort_by}  order={sort_order}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Strategy 1: metadata filter query ─────────────────────────────────
    tracks = _filter_query(conn, filters, sort_by, sort_order)

    # ── Strategy 2: vector similarity fallback ────────────────────────────
    if not tracks and raw_query.strip():
        print(f"[query] No metadata matches – trying vector similarity fallback ...")
        tracks = _vector_fallback(conn, raw_query)

    conn.close()



    if tracks:
        print(f"[query] Found {len(tracks)} track(s)")
        _print_track_list(tracks)
    else:
        print("[query] No tracks found for this command.")

    print("=" * 60)
    return tracks


# ---------------------------------------------------------------------------
# Strategy 1: metadata filter query
# ---------------------------------------------------------------------------

def _filter_query(
    conn: sqlite3.Connection,
    filters: dict,
    sort_by: str | None,
    sort_order: str | None
) -> list[dict]:
    """Build and execute a parameterised SQL query from the filter dict."""

    # ── Build WHERE clause ────────────────────────────────────────────────
    conditions = []
    params     = []

    filter_map = {
        "genre":        "genre",
        "artist":       "artist",
        "album":        "album",
        "album_number": "album_number",
        "title":        "title",
    }

    for key, col in filter_map.items():
        val = filters.get(key)
        if val:
            # LIKE search (case-insensitive) so partial names still match
            conditions.append(f"LOWER({col}) LIKE ?")
            params.append(f"%{val.lower()}%")
            print(f"[query]   Filter: {col} LIKE '%{val.lower()}%'")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # ── Build ORDER BY clause ─────────────────────────────────────────────
    order_clause = _build_order_clause(sort_by, sort_order)
    print(f"[query]   Order  : {order_clause}")

    # ── Execute ───────────────────────────────────────────────────────────
    sql = f"""
        SELECT id, path, title, artist, album, album_number, genre
        FROM   tracks
        {where_clause}
        {order_clause}
    """
    print(f"[query] SQL: {sql.strip()}")
    print(f"[query] Params: {params}")

    try:
        cursor = conn.execute(sql, params)
        rows   = cursor.fetchall()
        tracks = [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"[query] ERROR: SQL execution failed – {e}")
        return []

    # If sort_order is random, shuffle after fetch (SQLite RANDOM() is fine
    # but explicit Python shuffle gives us a logged shuffle)
    if sort_order == "random" and tracks:
        random.shuffle(tracks)
        print(f"[query]   Shuffled {len(tracks)} track(s)")
    elif sort_order != "random" and tracks:
        import re
        def _folder_sort_key(t):
            path = t.get("path", "")
            parts = path.split("/")
            folder_name = parts[-2] if len(parts) >= 2 else ""
            filename = parts[-1] if len(parts) >= 1 else ""
            
            # Extract leading number from folder name
            f_match = re.search(r'^(\d+)', folder_name.strip())
            folder_num = int(f_match.group(1)) if f_match else float('inf')
            
            # Extract leading number from filename (track number)
            t_match = re.search(r'^(\d+)', filename.strip())
            track_num = int(t_match.group(1)) if t_match else float('inf')
            
            return (folder_num, folder_name, track_num, filename)
            
        tracks.sort(key=_folder_sort_key)
        print(f"[query]   Sorted {len(tracks)} track(s) by folder number")

    return tracks


def _build_order_clause(sort_by: str | None, sort_order: str | None) -> str:
    """Return an ORDER BY SQL clause string (or default album_number, path order)."""
    valid_cols = {"title", "artist", "album", "album_number", "genre"}

    if sort_order == "random":
        return "ORDER BY RANDOM()"

    if sort_by and sort_by in valid_cols:
        direction = "DESC" if sort_order == "desc" else "ASC"
        if sort_by == "album_number":
            return f"ORDER BY CAST(album_number AS INTEGER) {direction}, album_number {direction}, path ASC"
        return f"ORDER BY {sort_by} {direction}, path ASC"

    # Default sort requirement: order by album, then album_number as integer, then path
    return "ORDER BY album ASC, CAST(album_number AS INTEGER) ASC, album_number ASC, path ASC"


# ---------------------------------------------------------------------------
# Strategy 2: vector similarity fallback
# ---------------------------------------------------------------------------

def _vector_fallback(conn: sqlite3.Connection, raw_query: str, top_k: int = 50) -> list[dict]:
    """
    Compute cosine similarity between *raw_query* embedding and stored
    track vectors, returning the top-k most similar tracks.

    Requires fastembed to be installed in the venv.
    """
    print(f"[query] Vector fallback for query: {raw_query!r}")

    try:
        from fastembed import TextEmbedding
        model      = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        query_vec  = list(model.embed([raw_query]))[0].tolist()
        print(f"[query]   Query vector length: {len(query_vec)}")
    except ImportError:
        print("[query] ERROR: fastembed not installed – cannot do vector fallback")
        return []
    except Exception as e:
        print(f"[query] ERROR: embedding failed – {e}")
        return []

    # Fetch all tracks with vectors
    try:
        cursor = conn.execute(
            "SELECT id, path, title, artist, album, album_number, genre, vector FROM tracks"
        )
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"[query] ERROR: Could not fetch tracks for vector search – {e}")
        return []

    scored = []
    for row in rows:
        try:
            track_vec = json.loads(row["vector"])
            score     = _cosine_similarity(query_vec, track_vec)
            scored.append((score, dict(row)))
        except Exception:
            continue   # skip malformed vectors

    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Only keep tracks that meet a minimum semantic similarity threshold (0.65)
    MIN_SCORE = 0.65
    top = [t for score, t in scored[:top_k] if score >= MIN_SCORE]

    if not top:
        print(f"[query]   Vector search found no tracks above {MIN_SCORE} threshold.")
        return []

    print(f"[query]   Vector search returned {len(top)} track(s) (top score: {scored[0][0]:.4f})")
    return top


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    dot  = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------

def _print_track_list(tracks: list[dict]):
    """Print a concise summary displaying only the albums and their numbers."""
    from collections import OrderedDict
    albums = OrderedDict()
    for t in tracks:
        alb_num = t.get("album_number", "N/A") or "N/A"
        alb_name = t.get("album", "Unknown Album")
        albums[(alb_num, alb_name)] = True

    print(f"[query] Playlist Summary ({len(tracks)} total track(s) across {len(albums)} album(s)):")
    for alb_num, alb_name in albums.keys():
        print(f"[query]   💿 Album #{alb_num}: {alb_name}")


# ---------------------------------------------------------------------------
# Catalog Loading for Fuzzy Matcher
# ---------------------------------------------------------------------------

def get_all_entities(db_path: str = DB_PATH) -> dict:
    """Fetch all distinct artists, albums, genres, and titles from the database."""
    if not os.path.isfile(db_path):
        return {}
    
    conn = sqlite3.connect(db_path)
    try:
        artists = [r[0] for r in conn.execute("SELECT DISTINCT artist FROM tracks WHERE artist IS NOT NULL AND artist != ''").fetchall()]
        albums = [r[0] for r in conn.execute("SELECT DISTINCT album FROM tracks WHERE album IS NOT NULL AND album != ''").fetchall()]
        genres = [r[0] for r in conn.execute("SELECT DISTINCT genre FROM tracks WHERE genre IS NOT NULL AND genre != ''").fetchall()]
        titles = [r[0] for r in conn.execute("SELECT DISTINCT title FROM tracks WHERE title IS NOT NULL AND title != ''").fetchall()]
    except sqlite3.Error as e:
        print(f"[query] ERROR: Failed to fetch entities - {e}")
        return {}
    finally:
        conn.close()

    playlists = []
    playlist_dir = "/home/frank/Music/Playlists"
    if os.path.isdir(playlist_dir):
        for f in os.listdir(playlist_dir):
            if f.endswith(".m3u") or f.endswith(".txt"):
                playlists.append(os.path.splitext(f)[0])
        
    return {
        "artists": artists,
        "albums": albums,
        "genres": genres,
        "titles": titles,
        "playlists": playlists
    }


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulated command dicts for testing
    test_commands = [
        {
            "action": "play",
            "continuous": True,
            "filters": {"genre": "jazz", "artist": None, "album": None, "title": None},
            "sort_by": None,
            "sort_order": "random",
            "raw_query": "play some jazz"
        },
        {
            "action": "play",
            "continuous": True,
            "filters": {"genre": None, "artist": "Beatles", "album": None, "title": None},
            "sort_by": "album",
            "sort_order": "asc",
            "raw_query": "play Beatles sorted by album"
        },
        {
            "action": "play",
            "continuous": True,
            "filters": {"genre": None, "artist": None, "album": None, "title": None},
            "sort_by": None,
            "sort_order": "random",
            "raw_query": "play something upbeat and energetic"
        },
    ]

    for cmd in test_commands:
        results = query_tracks(cmd)
        print(f"\n→ Returned {len(results)} track(s)\n")
