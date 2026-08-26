"""
player_engine.py  –  Jukebox MPV Player Engine
===============================================
Controls mpv via its JSON IPC socket (/tmp/mpv-jukebox.sock).

Supported operations:
  play(tracks, continuous)      –  immediately replace queue and start playing
  stop()                        –  stop playback and kill mpv
  skip(skip_by)                 –  skip track / rest of album / rest of artist
  status()                      –  return current track info and playback state
"""

import json
import os
import socket
import subprocess
import tempfile
import threading
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MPV_SOCKET   = "/tmp/mpv-jukebox.sock"
MPV_LOG_LEVEL = "error"   # set to "info" for more mpv output during debugging


# ---------------------------------------------------------------------------
# PlayerEngine class
# ---------------------------------------------------------------------------

STATE_FILE = "/home/frank/jukebox-env/playback_state.json"

class PlayerEngine:

    def __init__(self):
        self._mpv_proc      = None     # mpv subprocess
        self._playlist      = []       # list of track dicts currently queued
        self._current_index = 0        # index into self._playlist
        self._continuous    = True     # loop through playlist?
        self._lock          = threading.Lock()
        self._monitor_thread = None

        print("[player] PlayerEngine initialised.")
        print(f"[player] IPC socket : {MPV_SOCKET}")

        # Restore saved state from disk if available
        self._load_state()

    # ── Public API ─────────────────────────────────────────────────────────

    def play(self, tracks: list[dict] = None, continuous: bool = True):
        """
        If *tracks* provided: immediately stop current playback, load *tracks*, and start playing.
        If *tracks* is None/empty: resume playback from saved state or paused position.
        """
        print("\n" + "=" * 60)
        print("[player] ── Player Engine: PLAY / RESUME ───────────────────")

        # If no new tracks provided, attempt to resume previous state/paused track
        if not tracks:
            if not self._playlist:
                print("[player] No previous tracks in memory/state to resume.")
                print("=" * 60)
                return
            
            if self._is_mpv_running():
                # If mpv is running and paused, unpause
                self._ipc_command(["set_property", "pause", False])
                print("[player] Unpaused current playback.")
                print("=" * 60)
                return
            else:
                # Re-launch mpv with saved playlist and jump to saved position
                tracks = self._playlist
                start_index = self._current_index
                start_time = getattr(self, "_saved_time_pos", 0.0)
                print(f"[player] Resuming playlist from track #{start_index + 1} at {start_time:.1f}s")

                m3u_path = self._write_m3u(tracks)
                if m3u_path:
                    self._start_mpv(m3u_path, self._continuous, start_index=start_index, start_time=start_time)
                print("=" * 60)
                return

        self._continuous    = continuous
        self._playlist      = tracks
        self._current_index = 0
        self._saved_time_pos = 0.0

        print(f"[player] Tracks in queue : {len(tracks)}")
        print(f"[player] Continuous mode : {continuous}")

        # Stop any existing mpv instance first
        self._stop_mpv()

        # Write a temporary M3U playlist file
        m3u_path = self._write_m3u(tracks)
        if not m3u_path:
            print("[player] ERROR: Failed to create playlist file.")
            print("=" * 60)
            return

        # Start fresh mpv (loop-playlist=inf ensures continuous repeat of query)
        self._start_mpv(m3u_path, continuous)
        self._save_state()
        print("=" * 60)

    def stop(self):
        """Stop playback immediately, save current position, and shut down mpv."""
        print("\n" + "=" * 60)
        print("[player] ── Player Engine: STOP ────────────────────────────")
        self._save_current_position()
        self._stop_mpv()
        print("[player] Playback stopped and position saved.")
        print("=" * 60)

    def skip(self, skip_by: str = "track"):
        """
        Skip forward in the current playlist.

        Parameters
        ----------
        skip_by : str
            "track"  – advance one track (default)
            "album"  – skip all remaining tracks on the current album
            "artist" – skip all remaining tracks by the current artist
        """
        print("\n" + "=" * 60)
        print(f"[player] ── Player Engine: SKIP ({skip_by.upper()}) ──────────────────")

        if not self._is_mpv_running():
            print("[player] mpv is not running – nothing to skip.")
            print("=" * 60)
            return

        if skip_by == "track":
            self._skip_track()
        elif skip_by == "album":
            self._skip_album()
        elif skip_by == "artist":
            self._skip_artist()
        else:
            print(f"[player] Unknown skip_by={skip_by!r}, defaulting to track skip.")
            self._skip_track()

        # Unpause playback automatically in case it was paused during skip
        self._ipc_command(["set_property", "pause", False])

        print("=" * 60)

    # ── Skip helpers ───────────────────────────────────────────────────────

    def _skip_track(self):
        """Advance one track via mpv IPC playlist-next."""
        print("[player] Skip mode: TRACK – advancing one track")
        response = self._ipc_command(["playlist-next", "force"])
        if response:
            print(f"[player] Skip track sent. IPC response: {response}")
        else:
            print("[player] Skip track command sent (no IPC response).")

    def _skip_album(self):
        """
        Skip all remaining tracks on the same album as the current track.
        Jumps directly to the first track of the next album in the playlist.
        """
        print("[player] Skip mode: ALBUM – finding end of current album")

        current_pos, current_track = self._get_current_position()
        if current_track is None:
            print("[player] Cannot determine current track – falling back to track skip.")
            self._skip_track()
            return

        current_album = current_track.get("album", "").strip().lower()
        print(f"[player]   Current album : {current_track.get('album')!r}  (pos {current_pos})")

        # Walk forward from current_pos to find first track with a different album
        target_pos = None
        for i in range(current_pos + 1, len(self._playlist)):
            t = self._playlist[i]
            if t.get("album", "").strip().lower() != current_album:
                target_pos = i
                print(f"[player]   Next album starts at pos {i}: {t.get('album')!r} – {t.get('title')!r}")
                break

        if target_pos is None:
            if self._playlist and self._playlist[0].get("album", "").strip().lower() == current_album:
                print("[player]   Playlist only has this album! Falling back to track skip.")
                self._skip_track()
            else:
                print("[player]   No next album found – this is the last album. Looping to beginning.")
                self._jump_to(0)
        else:
            self._jump_to(target_pos)

    def _skip_artist(self):
        """
        Skip all remaining tracks by the same artist as the current track.
        Jumps directly to the first track by a different artist.
        """
        print("[player] Skip mode: ARTIST – finding end of current artist")

        current_pos, current_track = self._get_current_position()
        if current_track is None:
            print("[player] Cannot determine current track – falling back to track skip.")
            self._skip_track()
            return

        current_artist = current_track.get("artist", "").strip().lower()
        print(f"[player]   Current artist: {current_track.get('artist')!r}  (pos {current_pos})")

        # Walk forward to find first track by a different artist
        target_pos = None
        for i in range(current_pos + 1, len(self._playlist)):
            t = self._playlist[i]
            if t.get("artist", "").strip().lower() != current_artist:
                target_pos = i
                print(f"[player]   Next artist starts at pos {i}: {t.get('artist')!r} – {t.get('title')!r}")
                break

        if target_pos is None:
            if self._playlist and self._playlist[0].get("artist", "").strip().lower() == current_artist:
                print("[player]   Playlist only has this artist! Falling back to track skip.")
                self._skip_track()
            else:
                print("[player]   No next artist found – playlist only has this artist. Looping to beginning.")
                self._jump_to(0)
        else:
            self._jump_to(target_pos)

    def _get_current_position(self) -> tuple[int, dict | None]:
        """
        Ask mpv for the current playlist position and return
        (playlist_index, track_dict) using our local self._playlist.
        Returns (0, None) if the position cannot be determined.
        """
        for attempt in range(3):
            pos_resp = self._ipc_command(["get_property", "playlist-pos"], timeout=1.5)
            if pos_resp and pos_resp.get("error") == "success":
                pos = pos_resp.get("data", 0)
                if 0 <= pos < len(self._playlist):
                    track = self._playlist[pos]
                    print(f"[player]   Playlist position: {pos} / {len(self._playlist) - 1}")
                    print(f"[player]   Current track    : {track.get('title')!r} by {track.get('artist')!r}")
                    return pos, track
            time.sleep(0.2)
        
        print("[player] _get_current_position: IPC returned no valid position after retries.")
        return 0, None

    def _jump_to(self, index: int):
        """Tell mpv to jump to a specific 0-based playlist position."""
        print(f"[player] Jumping to playlist position {index}")
        response = self._ipc_command(["set_property", "playlist-pos", index])
        if response:
            print(f"[player] Jump IPC response: {response}")
        else:
            print("[player] Jump command sent (no IPC response).")

    def status(self) -> dict:
        """
        Return the current playback status as a dict.

        Returns
        -------
        dict with keys:
          running       – bool, is mpv alive?
          current_path  – str, path of currently playing file (or None)
          current_title – str, track title (or None)
          playlist_pos  – int, 0-based position in playlist
          playlist_len  – int, total tracks in playlist
          continuous    – bool
        """
        print("\n[player] ── Status ─────────────────────────────────────────")

        if not self._is_mpv_running():
            info = {
                "running": False, "current_path": None, "current_title": None,
                "playlist_pos": 0, "playlist_len": 0, "continuous": self._continuous
            }
            print(f"[player] Status: {info}")
            return info

        # Ask mpv for current file and position
        path_resp  = self._ipc_command(["get_property", "path"])
        pos_resp   = self._ipc_command(["get_property", "playlist-pos"])
        count_resp = self._ipc_command(["get_property", "playlist-count"])

        current_path = path_resp.get("data") if path_resp else None
        playlist_pos = pos_resp.get("data", 0) if pos_resp else 0
        playlist_len = count_resp.get("data", 0) if count_resp else 0

        # Match path to our local track dict for title
        current_title = None
        if current_path:
            for t in self._playlist:
                if t["path"] == current_path:
                    current_title = t.get("title", os.path.basename(current_path))
                    break

        info = {
            "running":       True,
            "current_path":  current_path,
            "current_title": current_title,
            "playlist_pos":  playlist_pos,
            "playlist_len":  playlist_len,
            "continuous":    self._continuous,
        }
        print(f"[player] Status:")
        print(f"[player]   Now playing  : {current_title!r}")
        print(f"[player]   Path         : {current_path}")
        print(f"[player]   Position     : {playlist_pos + 1} / {playlist_len}")
        print(f"[player]   Continuous   : {self._continuous}")
        return info

    # ── State persistence helpers ──────────────────────────────────────────

    def _save_current_position(self):
        """Ask mpv for current playlist position and time offset, and save to state."""
        if not self._is_mpv_running():
            return

        pos_resp = self._ipc_command(["get_property", "playlist-pos"])
        time_resp = self._ipc_command(["get_property", "time-pos"])

        if pos_resp and pos_resp.get("error") == "success":
            self._current_index = pos_resp.get("data", 0)
        if time_resp and time_resp.get("error") == "success":
            self._saved_time_pos = time_resp.get("data", 0.0)

        self._save_state()

    def _save_state(self):
        """Persist current playlist and position to STATE_FILE."""
        try:
            state_dir = os.path.dirname(STATE_FILE)
            if state_dir and not os.path.exists(state_dir):
                os.makedirs(state_dir, exist_ok=True)

            data = {
                "playlist": self._playlist,
                "current_index": self._current_index,
                "saved_time_pos": getattr(self, "_saved_time_pos", 0.0),
                "continuous": self._continuous
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"[player] Saved playback state to {STATE_FILE}")
        except Exception as e:
            print(f"[player] WARNING: Could not save playback state: {e}")

    def _load_state(self):
        """Load playback state from STATE_FILE if it exists."""
        if not os.path.exists(STATE_FILE):
            return

        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._playlist = data.get("playlist", [])
            self._current_index = data.get("current_index", 0)
            self._saved_time_pos = data.get("saved_time_pos", 0.0)
            self._continuous = data.get("continuous", True)
            print(f"[player] Loaded saved state: {len(self._playlist)} tracks, track index={self._current_index}, time={self._saved_time_pos:.1f}s")
        except Exception as e:
            print(f"[player] WARNING: Could not load saved state: {e}")

    # ── MPV process management ─────────────────────────────────────────────

    def _start_mpv(self, m3u_path: str, continuous: bool, start_index: int = 0, start_time: float = 0.0):
        """Launch a new mpv process in idle+IPC mode with the given playlist."""
        loop_flag = "inf" if continuous else "no"

        cmd = [
            "mpv",
            f"--playlist={m3u_path}",
            "--ao=alsa",
            "--audio-device=alsa/dmix:CARD=BossDAC,DEV=0",
            "--alsa-buffer-time=800000",
            "--audio-samplerate=48000",
            "--audio-format=s32",
            "--no-video",
            f"--input-ipc-server={MPV_SOCKET}",
            f"--loop-playlist={loop_flag}",
            f"--msg-level=all={MPV_LOG_LEVEL}",
            "--really-quiet",
        ]
        if start_index > 0:
            cmd.append(f"--playlist-start={start_index}")
        if start_time > 0:
            cmd.append(f"--start={start_time:.2f}")

        print(f"[player] Launching mpv ...")
        print(f"[player]   Command   : {' '.join(cmd)}")
        print(f"[player]   Playlist  : {m3u_path}")
        print(f"[player]   Loop      : {loop_flag}")
        print(f"[player]   Start At  : Track #{start_index + 1}, {start_time:.1f}s")

        try:
            self._mpv_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,    # capture stderr for debugging
            )
            print(f"[player] mpv started  (PID {self._mpv_proc.pid})")
        except FileNotFoundError:
            print("[player] ERROR: 'mpv' not found on PATH.")
            print("[player]        Install with:  sudo apt install mpv")
            self._mpv_proc = None
            return

        # Wait for the IPC socket to appear (up to 3 s)
        self._wait_for_socket()

        # Start background monitor thread
        self._monitor_thread = threading.Thread(
            target=self._monitor_mpv, daemon=True
        )
        self._monitor_thread.start()
        print("[player] Monitor thread started.")

    def _stop_mpv(self):
        """Terminate any running mpv process and clean up the socket."""
        if self._mpv_proc and self._mpv_proc.poll() is None:
            print(f"[player] Terminating existing mpv (PID {self._mpv_proc.pid}) ...")
            try:
                self._ipc_command(["quit"])
                time.sleep(0.3)
            except Exception:
                pass
            if self._mpv_proc.poll() is None:
                self._mpv_proc.terminate()
                self._mpv_proc.wait(timeout=3)
            print("[player] mpv terminated.")
        self._mpv_proc = None

        if os.path.exists(MPV_SOCKET):
            os.remove(MPV_SOCKET)
            print(f"[player] Removed IPC socket: {MPV_SOCKET}")

    def _is_mpv_running(self) -> bool:
        """Return True if the mpv process is alive and the socket exists."""
        proc_alive   = (self._mpv_proc is not None and
                        self._mpv_proc.poll() is None)
        socket_alive = os.path.exists(MPV_SOCKET)
        return proc_alive and socket_alive

    def _wait_for_socket(self, timeout: float = 5.0):
        """Block until the IPC socket file appears or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(MPV_SOCKET):
                print(f"[player] IPC socket ready: {MPV_SOCKET}")
                return
            time.sleep(0.1)
        print(f"[player] WARNING: IPC socket did not appear within {timeout}s")

    def _monitor_mpv(self):
        """Background thread that logs when mpv exits naturally."""
        proc = self._mpv_proc
        if proc:
            proc.wait()
            stderr_output = proc.stderr.read().decode(errors="replace").strip()
            print(f"\n[player] mpv exited (return code {proc.returncode})")
            if stderr_output:
                print(f"[player] mpv stderr:\n{stderr_output}")

    # ── IPC communication ──────────────────────────────────────────────────

    def _ipc_command(self, command: list, timeout: float = 3.0) -> dict | None:
        """
        Send a JSON command to mpv via the Unix IPC socket.

        Parameters
        ----------
        command : list
            mpv command as a list, e.g. ["playlist-next", "force"]

        Returns
        -------
        dict | None
            Parsed JSON response from mpv, or None on error.
        """
        if not os.path.exists(MPV_SOCKET):
            print(f"[player] IPC: socket not found at {MPV_SOCKET}")
            return None

        payload = json.dumps({"command": command}) + "\n"
        print(f"[player] IPC send: {payload.strip()}")

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(MPV_SOCKET)
            sock.sendall(payload.encode())

            # Read response and parse lines until a valid response is found
            data = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    
                    if b"\n" in data:
                        # Try to parse all complete lines
                        lines = data.decode(errors="replace").splitlines(keepends=True)
                        new_data = b""
                        
                        for line in lines:
                            if not line.endswith("\n"):
                                new_data += line.encode()
                                continue
                            
                            line = line.strip()
                            if line:
                                try:
                                    resp = json.loads(line)
                                    if "event" in resp and "error" not in resp:
                                        continue
                                    print(f"[player] IPC recv: {resp}")
                                    sock.close()
                                    return resp
                                except json.JSONDecodeError:
                                    continue
                                    
                        data = new_data
                        
                except socket.timeout:
                    break
            sock.close()

        except ConnectionRefusedError:
            print("[player] IPC: Connection refused – mpv may still be starting.")
        except Exception as e:
            print(f"[player] IPC error: {type(e).__name__}: {e}")

        return None

    # ── Playlist helper ────────────────────────────────────────────────────

    def _write_m3u(self, tracks: list[dict]) -> str | None:
        """Write a temporary M3U playlist file and return its path."""
        try:
            fd, path = tempfile.mkstemp(suffix=".m3u", prefix="jukebox_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in tracks:
                    title  = t.get("title",  "Unknown")
                    artist = t.get("artist", "Unknown")
                    f.write(f"#EXTINF:-1,{artist} - {title}\n")
                    f.write(t["path"] + "\n")
            print(f"[player] M3U playlist written: {path}  ({len(tracks)} tracks)")
            return path
        except Exception as e:
            print(f"[player] ERROR: Could not write M3U file: {e}")
            return None


# ---------------------------------------------------------------------------
# Standalone test (requires mpv and at least one track in your Music folder)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Dummy tracks for IPC smoke-test
    test_tracks = [
        {
            "path":   "/home/frank/Music/test.mp3",   # change to a real file
            "title":  "Test Track",
            "artist": "Test Artist",
            "genre":  "Test Genre",
            "album":  "Test Album",
            "album_number": "1"
        }
    ]

    player = PlayerEngine()
    print("\n--- Test: play ---")
    player.play(test_tracks, continuous=False)
    time.sleep(3)

    print("\n--- Test: status ---")
    player.status()
    time.sleep(2)

    print("\n--- Test: stop ---")
    player.stop()
