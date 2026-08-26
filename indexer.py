import os
import sys
import sqlite3
import json

# ---------------------------------------------------------------------------
# Raspberry Pi 5 / jukebox-env path configuration
# ---------------------------------------------------------------------------
VENV_DIR  = "/home/frank/jukebox-env"          # virtual-environment root
MUSIC_DIR = "/home/frank/Music"                # album folders live here
DB_PATH   = os.path.join(VENV_DIR, "music_catalog.db")

print(f"[startup] Python executable : {sys.executable}")
print(f"[startup] Virtual-env dir   : {VENV_DIR}")
print(f"[startup] Music directory   : {MUSIC_DIR}")
print(f"[startup] Database path     : {DB_PATH}")

# Warn if we are NOT running inside the expected virtual environment
if not sys.executable.startswith(VENV_DIR):
    print(
        f"[warning] Python executable is NOT inside {VENV_DIR}.\n"
        f"          Activate the venv first:  source {VENV_DIR}/bin/activate"
    )

# ---------------------------------------------------------------------------
# Imports that require venv packages
# ---------------------------------------------------------------------------
print("[startup] Importing mutagen ...")
try:
    import mutagen
    from mutagen.easyid3 import EasyID3
    print("[startup] mutagen imported successfully.")
except ImportError as e:
    print(f"[error] Failed to import mutagen: {e}")
    sys.exit(1)

print("[startup] Importing fastembed / TextEmbedding ...")
try:
    from fastembed import TextEmbedding
    print("[startup] fastembed imported successfully.")
except ImportError as e:
    print(f"[error] Failed to import fastembed: {e}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Initialise the BGE-Small embedding model
# (lightweight, runs well on CPU & Raspberry Pi 5 with 8 GB RAM)
# ---------------------------------------------------------------------------
print("[startup] Loading embedding model BAAI/bge-small-en-v1.5 ...")
try:
    embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    print("[startup] Embedding model loaded successfully.")
except Exception as e:
    print(f"[error] Failed to load embedding model: {e}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Initialize SQLite database for storing metadata and vector embeddings."""
    print(f"[db] Connecting to database at: {db_path}")

    # Make sure the parent directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.isdir(db_dir):
        print(f"[db] Directory does not exist, creating: {db_dir}")
        os.makedirs(db_dir, exist_ok=True)

    try:
        conn = sqlite3.connect(db_path)
        print("[db] Connected.")
    except Exception as e:
        print(f"[error] Could not open database {db_path}: {e}")
        raise

    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            title TEXT,
            artist TEXT,
            album TEXT,
            album_number TEXT,
            genre TEXT,
            vector TEXT
        )
    """)
    conn.commit()
    print("[db] Table 'tracks' ready.")
    return conn


import re

def parse_album_folder_name(folder_name: str) -> tuple[str, str]:
    """
    Extract album_number and cleaned album_name from a folder name.
    If the folder name starts with a number (e.g. '01 Abbey Road', '042 - Thriller', '12. Dark Side'),
    returns (album_number, album_name).
    Otherwise returns ('', folder_name).
    """
    if not folder_name or folder_name == ".":
        return "", "Unknown Album"
    
    match = re.match(r'^\s*(\d+)\s*[-_.]?\s*(.*)$', folder_name)
    if match:
        num, name = match.groups()
        cleaned_name = name.strip() if name.strip() else f"Album {num}"
        return num, cleaned_name
    return "", folder_name


def get_track_metadata(path: str, music_dir: str = MUSIC_DIR, default_album_number: str = "") -> dict:
    """Safely extract track metadata across MP3, FLAC, M4A, OGG formats."""
    print(f"[meta]   Reading tags: {path}")
    
    # Determine folder name relative to music_dir (or enclosing folder name)
    parent_dir = os.path.basename(os.path.dirname(path))
    folder_alb_num, folder_alb_name = parse_album_folder_name(parent_dir)

    track_info = {
        "path": path,
        "title": "Unknown Title",
        "artist": "Unknown Artist",
        "album": folder_alb_name if folder_alb_name else "Unknown Album",
        "genre": "Unknown Genre",
        "album_number": folder_alb_num if folder_alb_num else default_album_number
    }

    try:
        if path.lower().endswith('.mp3'):
            audio = EasyID3(path)
        else:
            audio = mutagen.File(path, easy=True)

        if audio:
            track_info["title"]  = audio.get("title",  [track_info["title"]])[0]
            track_info["artist"] = audio.get("artist", [track_info["artist"]])[0]
            # Prioritize folder-based album name & number per user requirement, falling back to ID3 tag if folder is generic
            if folder_alb_name in ("Unknown Album", "") and "album" in audio:
                track_info["album"] = audio.get("album")[0]
            track_info["genre"]  = audio.get("genre",  [track_info["genre"]])[0]
            print(f"[meta]     -> title={track_info['title']!r} artist={track_info['artist']!r} album={track_info['album']!r} album_number={track_info['album_number']!r}")
        else:
            print(f"[meta]   No tag data found for {path}")

    except Exception as e:
        print(f"[warning] Could not read tags for {path}: {e}")

    return track_info


def save_track_to_db(conn: sqlite3.Connection, track_info: dict, vector: list):
    """Insert or update track metadata and vector embedding in SQLite."""
    print(f"[db]   Saving to DB: {track_info['title']} -- {track_info['artist']}")
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO tracks (path, title, artist, album, album_number, genre, vector)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                title=excluded.title,
                artist=excluded.artist,
                album=excluded.album,
                album_number=excluded.album_number,
                genre=excluded.genre,
                vector=excluded.vector
        """, (
            track_info["path"],
            track_info["title"],
            track_info["artist"],
            track_info["album"],
            track_info["album_number"],
            track_info["genre"],
            json.dumps(vector)   # Serialize vector list to JSON string for SQLite storage
        ))
        conn.commit()
        print(f"[db]   Saved.")
    except Exception as e:
        print(f"[error] Failed to save {track_info['path']} to DB: {e}")
        raise


def generate_search_text(info: dict) -> str:
    """Build the string used for semantic vector embedding."""
    text = (
        f"{info['title']} by {info['artist']}, "
        f"album {info['album']} (Album #{info['album_number']}), "
        f"genre {info['genre']}"
    )
    print(f"[embed]  Search text: {text!r}")
    return text


# ---------------------------------------------------------------------------
# Main indexing routine
# ---------------------------------------------------------------------------

def index_library(
    music_dir: str = MUSIC_DIR,
    db_path: str = DB_PATH,
    prompt_missing: bool = False,
    shift_existing_albums: bool = True
):
    """Scan music directory, extract metadata, generate embeddings, and persist to SQLite DB."""
    print(f"\n{'='*60}")
    print(f"[index] Starting library indexing")
    print(f"[index]   Music directory       : {music_dir}")
    print(f"[index]   Database path         : {db_path}")
    print(f"[index]   Shift existing albums : {shift_existing_albums}")
    print(f"{'='*60}\n")

    # Sanity-check the music directory
    if not os.path.isdir(music_dir):
        print(f"[error] Music directory not found: {music_dir}")
        print("[error] Check that the path exists and is mounted.")
        return

    conn = init_db(db_path)

    # First pass: check for any new folders with album numbers and shift existing higher albums if requested
    if shift_existing_albums:
        print("[index] Pre-scan: Checking for album number insertions...")
        new_album_numbers = set()
        for root, dirs, files in os.walk(music_dir):
            audio_files = [f for f in files if f.lower().endswith(('.mp3', '.flac', '.m4a', '.ogg', '.wav'))]
            if audio_files:
                parent_dir = os.path.basename(root)
                num, name = parse_album_folder_name(parent_dir)
                if num and num.isdigit():
                    new_album_numbers.add((int(num), name, num))

        # Check existing DB for conflicts where a newly indexed folder has a number <= existing max
        cursor = conn.execute("SELECT DISTINCT album, album_number FROM tracks WHERE album_number IS NOT NULL AND album_number != ''")
        existing_albums = cursor.fetchall()
        existing_map = {}
        for alb, num_str in existing_albums:
            num_clean = str(num_str).lstrip('0') or str(num_str)
            if num_clean.isdigit():
                existing_map[alb] = (int(num_clean), str(num_str))

        for target_val, target_name, target_num_str in sorted(new_album_numbers, key=lambda x: x[0]):
            # If target_name is not already in DB with this number, shift any existing albums >= target_val
            if target_name not in existing_map or existing_map[target_name][0] != target_val:
                to_shift = []
                for alb, (val, num_str) in existing_map.items():
                    if alb != target_name and val >= target_val:
                        to_shift.append((val, alb, num_str))
                
                to_shift.sort(key=lambda x: x[0], reverse=True)
                for val, alb, num_str in to_shift:
                    new_val = val + 1
                    width = len(num_str) if num_str.isdigit() else 0
                    new_num_str = f"{new_val:0{width}d}" if width > 1 else str(new_val)
                    conn.execute("UPDATE tracks SET album_number = ? WHERE album = ?", (new_num_str, alb))
                    existing_map[alb] = (new_val, new_num_str)
                    print(f"[index]   Shifted existing album '{alb}' from #{num_str} to #{new_num_str}")
                conn.commit()

    total_found   = 0
    total_indexed = 0
    total_errors  = 0

    print(f"[index] Walking directory tree under {music_dir} ...")
    for root, dirs, files in os.walk(music_dir):
        rel_root = os.path.relpath(root, music_dir)
        print(f"\n[index] -> Entering folder: {rel_root}")

        audio_files = [f for f in files if f.lower().endswith(('.mp3', '.flac', '.m4a', '.ogg', '.wav'))]
        if not audio_files:
            print(f"[index]   (no supported audio files found here)")
            continue

        print(f"[index]   Found {len(audio_files)} audio file(s)")

        for file in audio_files:
            total_found += 1
            path = os.path.join(root, file)
            print(f"\n[index] Processing ({total_found}): {file}")

            # --- Metadata extraction ---
            try:
                info = get_track_metadata(path, music_dir=music_dir)
            except Exception as e:
                print(f"[error] Metadata extraction failed for {path}: {e}")
                total_errors += 1
                continue

            # --- Optional interactive prompt for missing album info ---
            if prompt_missing and info["album"] == "Unknown Album":
                print(f"\n[prompt] Missing album info for: {file}")
                user_album = input("  Enter album name (blank = Unknown): ").strip()
                if user_album:
                    info["album"] = user_album

                user_alb_num = input("  Enter album catalog/release number (blank = N/A): ").strip()
                if user_alb_num:
                    info["album_number"] = user_alb_num

            # --- Generate embedding ---
            print(f"[embed]  Generating embedding ...")
            try:
                search_text = generate_search_text(info)
                vector = list(embedding_model.embed([search_text]))[0].tolist()
                print(f"[embed]  Vector length: {len(vector)}")
            except Exception as e:
                print(f"[error] Embedding failed for {path}: {e}")
                total_errors += 1
                continue

            # --- Persist to database ---
            try:
                save_track_to_db(conn, info, vector)
                total_indexed += 1
                print(f"[index] Indexed: {info['title']} -- {info['artist']}")
            except Exception as e:
                print(f"[error] DB save failed for {path}: {e}")
                total_errors += 1

    conn.close()
    print(f"\n{'='*60}")
    print(f"[index] Indexing complete!")
    print(f"[index]   Files found   : {total_found}")
    print(f"[index]   Successfully  : {total_indexed}")
    print(f"[index]   Errors        : {total_errors}")
    print(f"[index]   Database      : {db_path}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    index_library(
        music_dir=MUSIC_DIR,
        db_path=DB_PATH,
        prompt_missing=False   # Set True to interactively fill missing album info
    )
