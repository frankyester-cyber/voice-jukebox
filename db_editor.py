"""
db_editor.py – Web-based Database Editor for Music Catalog
=========================================================
Allows viewing, reviewing, searching, and editing music_catalog.db by hand
to correct errors in track titles, artists, albums, album numbers, and genres.

Runs a local web server (Flask or HTTP server) accessible in your web browser.
"""

import os
import sys
import json
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Default paths (can be overridden via environment variables or command args)
DB_PATH = os.environ.get("JUKEBOX_DB_PATH", "/home/frank/jukebox-env/music_catalog.db")
if not os.path.isabs(DB_PATH) and not os.path.exists(DB_PATH):
    # Fallback to local scratch directory if running locally
    local_db = os.path.join(os.path.dirname(__file__), "music_catalog.db")
    if os.path.exists(local_db) or not os.path.exists(os.path.dirname(DB_PATH)):
        DB_PATH = local_db

PORT = 8080

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_if_needed():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = get_db_connection()
    conn.execute("""
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
    conn.close()

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jukebox Music Catalog Editor</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --accent: #38bdf8;
            --accent-hover: #0284c7;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --border: #334155;
            --success: #22c55e;
            --danger: #ef4444;
        }

        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }

        h1 {
            margin: 0;
            font-size: 1.8rem;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .badge {
            font-size: 0.8rem;
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent);
            padding: 4px 10px;
            border-radius: 12px;
            border: 1px solid rgba(56, 189, 248, 0.3);
        }

        .controls {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            background: var(--card-bg);
            padding: 16px;
            border-radius: 12px;
            border: 1px solid var(--border);
            flex-wrap: wrap;
        }

        input[type="text"], select {
            background: #0f172a;
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 0.95rem;
            outline: none;
        }

        input[type="text"]:focus, select:focus {
            border-color: var(--accent);
        }

        .search-input {
            flex-grow: 1;
            min-width: 250px;
        }

        button {
            background: var(--accent);
            color: #0f172a;
            font-weight: 600;
            border: none;
            padding: 10px 18px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        button:hover {
            background: var(--accent-hover);
            color: #fff;
        }

        .table-container {
            background: var(--card-bg);
            border-radius: 12px;
            border: 1px solid var(--border);
            overflow-x: auto;
            box-shadow: 0 10px 25px -5px rgba(0,0,0,0.3);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th {
            background: #111827;
            color: var(--text-sub);
            font-weight: 600;
            padding: 14px 16px;
            border-bottom: 1px solid var(--border);
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }

        td {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            font-size: 0.9rem;
        }

        tr:hover {
            background: rgba(255,255,255,0.02);
        }

        .editable-cell {
            background: #0f172a;
            border: 1px solid transparent;
            color: var(--text-main);
            padding: 6px 10px;
            border-radius: 6px;
            width: 90%;
            transition: border-color 0.2s;
        }

        .editable-cell:focus {
            border-color: var(--accent);
            background: #1e293b;
        }

        .num-input {
            width: 70px;
            text-align: center;
        }

        .path-cell {
            max-width: 250px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: var(--text-sub);
            font-size: 0.8rem;
            font-family: monospace;
        }

        .btn-save {
            background: #16a34a;
            color: white;
            padding: 6px 12px;
            font-size: 0.85rem;
        }

        .btn-save:hover {
            background: #15803d;
        }

        .status-toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: var(--success);
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            display: none;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            font-weight: 500;
        }

        .batch-panel {
            background: rgba(30, 41, 59, 0.7);
            border: 1px dashed var(--accent);
            padding: 14px 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            display: flex;
            gap: 12px;
            align-items: center;
        }

        .batch-panel label {
            font-size: 0.9rem;
            color: var(--text-sub);
        }

        footer {
            margin-top: 30px;
            text-align: center;
            color: var(--text-sub);
            font-size: 0.85rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>
                🎵 Jukebox Database Editor
                <span class="badge" id="dbPathBadge">Connecting...</span>
            </h1>
            <button onclick="loadTracks()">🔄 Refresh List</button>
        </header>

        <div class="controls">
            <input type="text" id="searchInput" class="search-input" placeholder="Search title, artist, album, genre, or number..." onkeyup="filterTable()">
            <select id="filterField" onchange="filterTable()">
                <option value="all">All Fields</option>
                <option value="album">Album</option>
                <option value="album_number">Album Number</option>
                <option value="artist">Artist</option>
                <option value="title">Title</option>
            </select>
        </div>

        <div class="batch-panel">
            <label>⚡ <strong>Batch Update Album:</strong></label>
            <input type="text" id="targetAlbum" placeholder="Target Album Name or #">
            <input type="text" id="newAlbumNum" class="num-input" placeholder="New #">
            <input type="text" id="newAlbumName" placeholder="New Album Name">
            <input type="text" id="newArtist" placeholder="New Artist Name">
            <input type="text" id="newGenre" placeholder="New Genre">
            <button onclick="applyBatchAlbumUpdate()">Apply Batch Update</button>
        </div>

        <div class="batch-panel" style="border-color: #f59e0b;">
            <label>📌 <strong>Insert Album & Shift Numbers:</strong></label>
            <input type="text" id="insertAlbumNum" class="num-input" placeholder="Insert #">
            <input type="text" id="insertAlbumName" placeholder="New Album Name">
            <input type="text" id="insertArtist" placeholder="Artist Name">
            <input type="text" id="insertTitle" placeholder="Track Title (Optional)">
            <input type="text" id="insertGenre" placeholder="Genre (Optional)">
            <button onclick="insertAndShiftAlbum()" style="background: #f59e0b; color: #0f172a;">Insert & Shift Albums</button>
        </div>

        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 50px;">ID</th>
                        <th style="width: 80px;">Alb #</th>
                        <th>Album Name</th>
                        <th>Track Title</th>
                        <th>Artist</th>
                        <th>Genre</th>
                        <th>Path</th>
                        <th style="width: 80px;">Action</th>
                    </tr>
                </thead>
                <tbody id="trackTableBody">
                    <tr><td colspan="8" style="text-align: center; padding: 40px;">Loading catalog tracks...</td></tr>
                </tbody>
            </table>
        </div>

        <footer>
            Jukebox Catalog Manual Database Manager • Running on SQLite
        </footer>
    </div>

    <div id="toast" class="status-toast">Changes saved successfully!</div>

    <script>
        let allTracks = [];

        async function loadTracks() {
            try {
                const response = await fetch('/api/tracks');
                const data = await response.json();
                allTracks = data.tracks;
                document.getElementById('dbPathBadge').innerText = data.db_path;
                renderTable(allTracks);
            } catch (err) {
                console.error("Error loading tracks:", err);
                alert("Failed to load tracks from database.");
            }
        }

        function renderTable(tracks) {
            const tbody = document.getElementById('trackTableBody');
            if (tracks.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; padding: 40px; color: var(--text-sub);">No tracks found in database.</td></tr>`;
                return;
            }

            tbody.innerHTML = tracks.map(t => `
                <tr id="row-${t.id}">
                    <td>${t.id}</td>
                    <td>
                        <input type="text" class="editable-cell num-input" id="num-${t.id}" value="${t.album_number || ''}">
                    </td>
                    <td>
                        <input type="text" class="editable-cell" id="album-${t.id}" value="${escapeHtml(t.album || '')}">
                    </td>
                    <td>
                        <input type="text" class="editable-cell" id="title-${t.id}" value="${escapeHtml(t.title || '')}">
                    </td>
                    <td>
                        <input type="text" class="editable-cell" id="artist-${t.id}" value="${escapeHtml(t.artist || '')}">
                    </td>
                    <td>
                        <input type="text" class="editable-cell" id="genre-${t.id}" value="${escapeHtml(t.genre || '')}">
                    </td>
                    <td class="path-cell" title="${escapeHtml(t.path)}">${escapeHtml(t.path)}</td>
                    <td>
                        <button class="btn-save" onclick="saveTrack(${t.id})">Save</button>
                    </td>
                </tr>
            `).join('');
        }

        function escapeHtml(str) {
            return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        function filterTable() {
            const rawQuery = document.getElementById('searchInput').value.trim();
            const field = document.getElementById('filterField').value;

            // Extract numeric digits if query is e.g. "6", "06", "alb 6", "album 6", "alb #6"
            const numMatch = rawQuery.match(/^(?:alb(?:um)?\s*#?\s*)?(\d+)$/i);
            const searchNum = numMatch ? numMatch[1] : null;
            const queryLower = rawQuery.toLowerCase();

            const filtered = allTracks.filter(t => {
                if (!rawQuery) return true;

                const albNumStr = (t.album_number || '').toString().trim();
                const albNumClean = albNumStr.replace(/^0+/, ''); // e.g. "06" -> "6"

                // Specific match for numeric / album number queries
                if (searchNum) {
                    const searchNumClean = searchNum.replace(/^0+/, '');
                    if (albNumStr === searchNum || albNumClean === searchNumClean) {
                        return true;
                    }
                }

                if (field === 'all') {
                    return albNumStr.toLowerCase().includes(queryLower) ||
                           (t.album || '').toLowerCase().includes(queryLower) ||
                           (t.title || '').toLowerCase().includes(queryLower) ||
                           (t.artist || '').toLowerCase().includes(queryLower) ||
                           (t.genre || '').toLowerCase().includes(queryLower);
                } else if (field === 'album_number') {
                    if (searchNum) {
                        return albNumStr === searchNum || albNumClean === searchNum.replace(/^0+/, '');
                    }
                    return albNumStr.toLowerCase().includes(queryLower);
                } else {
                    return (t[field] || '').toLowerCase().includes(queryLower);
                }
            });

            // Auto-populate Target Album in the batch panel if filtered tracks all belong to the same album or match the query
            if (filtered.length > 0) {
                const uniqueAlbums = [...new Set(filtered.map(t => t.album).filter(Boolean))];
                if (uniqueAlbums.length === 1) {
                    document.getElementById('targetAlbum').value = uniqueAlbums[0];
                }
            }

            renderTable(filtered);
        }

        async function saveTrack(id) {
            const payload = {
                id: id,
                album_number: document.getElementById(`num-${id}`).value.trim(),
                album: document.getElementById(`album-${id}`).value.trim(),
                title: document.getElementById(`title-${id}`).value.trim(),
                artist: document.getElementById(`artist-${id}`).value.trim(),
                genre: document.getElementById(`genre-${id}`).value.trim()
            };

            try {
                const response = await fetch('/api/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const res = await response.json();
                if (res.success) {
                    showToast("Track updated successfully!");
                    // update local cache
                    const idx = allTracks.findIndex(t => t.id === id);
                    if (idx !== -1) {
                        Object.assign(allTracks[idx], payload);
                    }
                } else {
                    alert("Error: " + res.error);
                }
            } catch (err) {
                alert("Failed to save track: " + err);
            }
        }

        async function applyBatchAlbumUpdate() {
            const targetAlbum = document.getElementById('targetAlbum').value.trim();
            const newAlbumNum = document.getElementById('newAlbumNum').value.trim();
            const newAlbumName = document.getElementById('newAlbumName').value.trim();
            const newArtist = document.getElementById('newArtist').value.trim();
            const newGenre = document.getElementById('newGenre').value.trim();

            if (!targetAlbum) {
                alert("Please enter the Target Album Name to batch edit.");
                return;
            }

            if (!newAlbumNum && !newAlbumName && !newArtist && !newGenre) {
                alert("Please enter at least one field to update (Album #, Album Name, Artist, or Genre).");
                return;
            }

            if (!confirm(`Are you sure you want to update all tracks in album '${targetAlbum}'?`)) {
                return;
            }

            try {
                const response = await fetch('/api/batch_update_album', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target_album: targetAlbum,
                        new_album_number: newAlbumNum,
                        new_album_name: newAlbumName,
                        new_artist: newArtist,
                        new_genre: newGenre
                    })
                });
                const res = await response.json();
                if (res.success) {
                    showToast(`Updated ${res.updated_count} track(s)!`);
                    loadTracks();
                } else {
                    alert("Error: " + res.error);
                }
            } catch (err) {
                alert("Batch update failed: " + err);
            }
        }

        async function insertAndShiftAlbum() {
            const insertNum = document.getElementById('insertAlbumNum').value.trim();
            const albumName = document.getElementById('insertAlbumName').value.trim();
            const artist = document.getElementById('insertArtist').value.trim();
            const title = document.getElementById('insertTitle').value.trim() || 'Track 1';
            const genre = document.getElementById('insertGenre').value.trim() || 'Unknown Genre';

            if (!insertNum || isNaN(parseInt(insertNum))) {
                alert("Please enter a valid numeric Album Number to insert at (e.g. 5 or 05).");
                return;
            }

            if (!albumName) {
                alert("Please enter the New Album Name.");
                return;
            }

            if (!confirm(`Are you sure you want to insert '${albumName}' at position #${insertNum}? All existing albums numbered ${insertNum} and above will be shifted up by 1.`)) {
                return;
            }

            try {
                const response = await fetch('/api/insert_album', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        insert_number: insertNum,
                        album_name: albumName,
                        artist: artist,
                        title: title,
                        genre: genre
                    })
                });
                const res = await response.json();
                if (res.success) {
                    showToast(`Album inserted! Shifted ${res.shifted_albums_count} album(s).`);
                    // Clear inputs
                    document.getElementById('insertAlbumNum').value = '';
                    document.getElementById('insertAlbumName').value = '';
                    document.getElementById('insertArtist').value = '';
                    document.getElementById('insertTitle').value = '';
                    document.getElementById('insertGenre').value = '';
                    loadTracks();
                } else {
                    alert("Error: " + res.error);
                }
            } catch (err) {
                alert("Insert album failed: " + err);
            }
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            if (toast) {
                toast.innerText = msg;
                toast.style.display = 'block';
                setTimeout(() => { toast.style.display = 'none'; }, 3000);
            }
        }

        // Initial fetch
        loadTracks();
    </script>
</body>
</html>
"""

class DBEditorHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/tracks":
            try:
                conn = get_db_connection()
                cursor = conn.execute("SELECT id, path, title, artist, album, album_number, genre FROM tracks ORDER BY album_number ASC, album ASC, id ASC")
                rows = [dict(r) for r in cursor.fetchall()]
                conn.close()

                response_data = {
                    "db_path": DB_PATH,
                    "tracks": rows
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = {}

        if parsed.path == "/api/update":
            track_id = data.get("id")
            album_number = data.get("album_number", "")
            album = data.get("album", "")
            title = data.get("title", "")
            artist = data.get("artist", "")
            genre = data.get("genre", "")

            try:
                conn = get_db_connection()
                conn.execute("""
                    UPDATE tracks
                    SET album_number = ?, album = ?, title = ?, artist = ?, genre = ?
                    WHERE id = ?
                """, (album_number, album, title, artist, genre, track_id))
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))

        elif parsed.path == "/api/batch_update_album":
            target_album = data.get("target_album", "")
            new_album_number = data.get("new_album_number", "")
            new_album_name = data.get("new_album_name", "")
            new_artist = data.get("new_artist", "")
            new_genre = data.get("new_genre", "")

            try:
                conn = get_db_connection()
                updates = []
                params = []

                if new_album_number:
                    updates.append("album_number = ?")
                    params.append(new_album_number)
                if new_album_name:
                    updates.append("album = ?")
                    params.append(new_album_name)
                if new_artist:
                    updates.append("artist = ?")
                    params.append(new_artist)
                if new_genre:
                    updates.append("genre = ?")
                    params.append(new_genre)

                if not updates:
                    raise ValueError("No new values specified for batch update.")

                target_clean = target_album.lstrip('0') if target_album.lstrip('0') else target_album
                params.extend([target_album, target_album, target_clean])
                sql = f"UPDATE tracks SET {', '.join(updates)} WHERE LOWER(album) = LOWER(?) OR LOWER(album_number) = LOWER(?) OR LTRIM(album_number, '0') = ?"

                cursor = conn.execute(sql, params)
                updated_count = cursor.rowcount
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "updated_count": updated_count}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))

        elif parsed.path == "/api/insert_album":
            insert_number_raw = data.get("insert_number", "").strip()
            album_name = data.get("album_name", "").strip()
            artist = data.get("artist", "").strip() or "Unknown Artist"
            title = data.get("title", "").strip() or "Track 1"
            genre = data.get("genre", "").strip() or "Unknown Genre"

            try:
                insert_val = int(insert_number_raw)
                conn = get_db_connection()

                # Get all distinct albums and their numbers
                cursor = conn.execute("SELECT DISTINCT album, album_number FROM tracks WHERE album_number IS NOT NULL AND album_number != ''")
                distinct_albums = cursor.fetchall()

                # Collect albums with numeric album_number >= insert_val
                to_shift = []
                for alb, num_str in distinct_albums:
                    num_clean = str(num_str).lstrip('0') or str(num_str)
                    if num_clean.isdigit():
                        val = int(num_clean)
                        if val >= insert_val:
                            to_shift.append((val, alb, str(num_str)))

                # Sort descending to prevent collisions during SQL updates
                to_shift.sort(key=lambda x: x[0], reverse=True)

                shifted_count = 0
                for val, alb, num_str in to_shift:
                    new_val = val + 1
                    width = len(num_str) if num_str.isdigit() else 0
                    new_num_str = f"{new_val:0{width}d}" if width > 1 else str(new_val)
                    cursor = conn.execute("UPDATE tracks SET album_number = ? WHERE album = ? AND album_number = ?", (new_num_str, alb, num_str))
                    shifted_count += cursor.rowcount

                # Form new album number string matching requested format width
                req_width = len(insert_number_raw) if insert_number_raw.isdigit() else 0
                new_insert_num_str = f"{insert_val:0{req_width}d}" if req_width > 1 else str(insert_val)

                # Insert placeholder entry for the new album into the database catalog
                dummy_path = f"/virtual/insert_{insert_val}_{album_name.replace(' ', '_')}.mp3"
                conn.execute("""
                    INSERT INTO tracks (path, title, artist, album, album_number, genre)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        title=excluded.title,
                        artist=excluded.artist,
                        album=excluded.album,
                        album_number=excluded.album_number,
                        genre=excluded.genre
                """, (dummy_path, title, artist, album_name, new_insert_num_str, genre))

                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "shifted_albums_count": len(to_shift),
                    "new_album_number": new_insert_num_str
                }).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def run_server(port=PORT):
    init_db_if_needed()
    server_address = ('', port)
    httpd = HTTPServer(server_address, DBEditorHandler)
    print(f"\n[db_editor] Database Editor web server active!")
    print(f"[db_editor]   Database : {DB_PATH}")
    print(f"[db_editor]   URL      : http://localhost:{port}")
    print(f"[db_editor] Press Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[db_editor] Stopping web server.")
        httpd.server_close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        DB_PATH = sys.argv[1]
    run_server()
