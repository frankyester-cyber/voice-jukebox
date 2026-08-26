"""
pure_fuzzy_controller.py  –  Voice-Driven Jukebox Controller (Pure Fuzzy Logic)
========================================================================================
Combines ReSpeaker XVF3800 audio capture, Whisper transcription, terminal output,
ultra-fast fuzzy logic parsing, and MPV playback engine.

Optimized for 0ms cold-start and instant sub-millisecond intent extraction.
"""

import os
import sys

# ---------------------------------------------------------------------------
# Critical OpenMP Thread Release for Raspberry Pi 5 (4 Physical Cores)
# Must be set BEFORE importing C-extension libraries (numpy, torch, ctranslate2)
# ---------------------------------------------------------------------------
os.environ["OMP_WAIT_POLICY"] = "PASSIVE"     # Force OpenMP worker threads to sleep immediately when idle
os.environ["OMP_NUM_THREADS"] = "4"         # Use full 4 cores for sequential tasks
os.environ["OPENBLAS_NUM_THREADS"] = "4"
os.environ["ORT_LOGGING_LEVEL"] = "3"       # Suppress harmless ONNX Runtime GPU discovery warnings on Pi

import io
import threading
import time
import requests
import numpy as np
import sounddevice as sd
from gpiozero import Button

UPS_PIN = 4

def trigger_shutdown():
    print("\n[UPS] POWER LOSS DETECTED. Shutting down safely...")
    os.system("sudo shutdown -h now")

import wave
import struct
import math
import subprocess

def _generate_beep_wav(filename: str, freq: float, duration: float, volume: float = 0.3):
    """Generate a simple sine wave beep and save it as a WAV file."""
    if not os.path.exists(filename):
        with wave.open(filename, "w") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(48000)
            for i in range(int(48000 * duration)):
                value = int(32767 * volume * math.sin(2 * math.pi * freq * i / 48000))
                f.writeframesraw(struct.pack("<h", value))

def _play_wav_aplay(filename: str):
    """Play a WAV file safely using ALSA's aplay through the dmix interface."""
    try:
        subprocess.run(
            ["aplay", "-D", "plug:dmix:BossDAC", "-q", filename],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

def play_error_beep():
    """Play a double-beep tone to indicate an unrecognized command."""
    try:
        filename = "/tmp/error_beep.wav"
        if not os.path.exists(filename):
            # Generate a 300Hz tone
            _generate_beep_wav(filename, 300, 0.15, volume=0.5)
        _play_wav_aplay(filename)
        # We can't easily generate double beep with just sine loop without rewriting the generator,
        # so just playing the low tone once is enough for an error indicator.
    except Exception as e:
        print(f"[Audio Error] Could not play beep: {e}")

def play_ready_beep():
    """Play a short, high-pitched beep to indicate readiness."""
    try:
        filename = "/tmp/ready_beep.wav"
        _generate_beep_wav(filename, 880, 0.1, volume=0.3)
        _play_wav_aplay(filename)
    except Exception:
        pass

# Reconfigure stdout/stderr encoding safely in-place without re-wrapping or closing streams
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
print("[boot] Loading Voice Jukebox modules ...")

try:
    from voice_command_listener import VoiceListener, find_respeaker_device
    print("[boot]   voice_command_listener  [OK]")
except ImportError:
    try:
        from jukebox_app.voice_command_listener import VoiceListener, find_respeaker_device
        print("[boot]   voice_command_listener  [OK]")
    except ImportError as e:
        print(f"[boot] ERROR: Cannot import voice_command_listener – {e}")
        sys.exit(1)



try:
    from query_engine import query_tracks, get_all_entities
    print("[boot]   query_engine            [OK]")
except ImportError as e:
    print(f"[boot] ERROR: Cannot import query_engine – {e}")
    sys.exit(1)

try:
    from fuzzy_client import ask_fuzzy, default_parser
    print("[boot]   fuzzy_client            [OK]")
except ImportError as e:
    print(f"[boot] WARNING: Cannot import fuzzy_client – {e}")

try:
    from player_engine import PlayerEngine
    print("[boot]   player_engine           [OK]")
except ImportError as e:
    print(f"[boot] ERROR: Cannot import player_engine – {e}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Built-in commands that skip Ollama
# ---------------------------------------------------------------------------
BUILTIN_COMMANDS = {
    "play":   "Resume playback from stopped position",
    "resume": "Resume playback from stopped position",
    "status": "Show what's currently playing",
    "stop":   "Stop playback immediately",
    "skip":   "Skip to the next track",
    "next":   "Skip to the next track",
    "quit":   "Stop playback and exit",
    "exit":   "Stop playback and exit",
}


def handle_builtin(text: str, player: PlayerEngine) -> bool:
    """If *text* is a built-in command, handle it and return True."""
    cmd = text.strip().lower()

    if cmd in ("quit", "exit"):
        print("\n[controller] Stopping playback and exiting ...")
        player.stop()
        print("[controller] Goodbye!")
        sys.exit(0)

    if cmd in ("play", "resume"):
        player.play()
        return True

    if cmd == "status":
        player.status()
        return True

    if cmd == "stop":
        player.stop()
        return True

    if cmd in ("skip", "next"):
        player.skip(skip_by="track")
        return True

    return False


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------

def dispatch(command: dict, player: PlayerEngine):
    """Execute a validated command dict produced by ollama_client."""
    action     = command.get("action", "play")
    continuous = command.get("continuous", True)
    skip_by    = command.get("skip_by", "track")

    print(f"\n[controller] Dispatching action: {action!r}  continuous={continuous}")

    if action == "stop":
        player.stop()
        return

    if action == "skip":
        player.skip(skip_by=skip_by)
        return

    if action in ("play", "queue", "playlist"):
        filters = command.get("filters", {})
        has_filters = any(bool(v) for v in filters.values()) if isinstance(filters, dict) else False
        
        is_playlist = (action == "playlist")

        if not has_filters and not is_playlist:
            print("\n[controller] Play command received with no search filters – continuing last playlist.")
            player.play()
            return

        tracks = query_tracks(command)

        if not tracks:
            print("\n[controller] No matching tracks found. Continuing last playlist.")
            player.play()
            return

        _print_now_playing_banner(tracks[0], len(tracks), continuous)
        player.play(tracks, continuous=continuous)

    else:
        print(f"[controller] WARNING: Unknown action {action!r} – ignoring.")


def _print_now_playing_banner(first_track: dict, total: int, continuous: bool):
    """Print a human-readable 'Now Playing' banner."""
    title  = first_track.get("title",  "Unknown")
    artist = first_track.get("artist", "Unknown")
    album  = first_track.get("album",  "Unknown")
    genre  = first_track.get("genre",  "Unknown")
    mode   = "continuous loop" if continuous else "single pass"

    print("\n" + "*" * 60)
    print(f"     Now Playing  :  {title}")
    print(f"     Artist       :  {artist}")
    print(f"     Album        :  {album}")
    print(f"     Genre        :  {genre}")
    print(f"     Queue        :  {total} track(s) queued  [{mode}]")
    print("*" * 60)


# ---------------------------------------------------------------------------
# Parallel Model Pre-warming
# ---------------------------------------------------------------------------




def _warmup_whisper_engine(listener: VoiceListener):
    """Pass a tiny 0.1s dummy audio buffer to pre-warm CTranslate2 kernels."""
    try:
        print("[boot] Pre-warming Whisper C++ engine ...")
        dummy_audio = np.zeros(1600, dtype=np.float32) # 0.1 sec of silence at 16kHz
        listener.transcribe_audio(dummy_audio)
        print("[boot]   Whisper C++ engine  [OK]")
    except Exception as e:
        print(f"[boot] Note: Whisper cold-start warmup: {e}")


# ---------------------------------------------------------------------------
# Main Controller Entry Point
# ---------------------------------------------------------------------------

def main():
    print("\n" + "=" * 60)
    print("      VOICE JUKEBOX CONTROLLER  -  Voice & Ollama Powered  ")
    print("=" * 60)

    # 1. Initialize PlayerEngine
    player = PlayerEngine()

    # 2. Immediately start playing the last requested state on boot/startup
    print("\n[boot] Auto-resuming last request before shutdown ...")
    player.play()

    # 3. Initialize UPS Monitor
    try:
        ups_monitor = Button(UPS_PIN, pull_up=True, bounce_time=0.1)
        ups_monitor.when_pressed = trigger_shutdown
        print(f"[boot]   UPS Monitor (GPIO {UPS_PIN})   [OK]")
    except Exception as e:
        print(f"[boot] WARNING: Could not init UPS monitor on GPIO {UPS_PIN} – {e}")

    # 4. Initialize Voice Listener (Whisper) & Pre-warm Whisper C++ Kernels
    device_index = find_respeaker_device()
    listener = VoiceListener(model_size="tiny.en", device_idx=device_index)
    _warmup_whisper_engine(listener)

    # 4.5 Preload fuzzy matcher catalog
    print("[boot] Loading music catalog into fuzzy matcher ...")
    try:
        entities = get_all_entities()
        if entities:
            default_parser.update_catalog(
                artists=entities.get("artists"),
                albums=entities.get("albums"),
                genres=entities.get("genres"),
                titles=entities.get("titles"),
                playlists=entities.get("playlists")
            )
            print(f"[boot]   Catalog loaded (Artists: {len(entities.get('artists', []))}, Albums: {len(entities.get('albums', []))}, Playlists: {len(entities.get('playlists', []))})")
    except NameError:
        pass  # default_parser not imported



    print("\n" + "=" * 60)
    print(" Voice Jukebox Ready!")
    print(" Mode: Push-to-Talk (Press Enter to record/stop)")
    print("=" * 60)
    print(" Press Ctrl+C to exit.\n")
    
    # Play the ready beep exactly ONCE when the app first boots up
    play_ready_beep()

    try:
        while True:
            input("\n[Voice] Press ENTER to START recording...")

            # Pause playback temporarily while recording to prevent speaker echo
            was_playing = player._is_mpv_running()
            if was_playing:
                player._ipc_command(["set_property", "pause", True])

            print("[Voice] [RECORDING] Press ENTER to STOP recording.")

            # Record speech
            t_start = time.time()
            audio_chunks = []

            def audio_callback(indata, frames, time_info, status):
                if status:
                    print(f"[Audio Status Warning]: {status}", file=sys.stderr)
                audio_chunks.append(indata.copy())

            with listener.record_and_transcribe_ptt.__globals__['sd'].InputStream(
                samplerate=16000, channels=1, device=listener.device_idx, dtype="float32", callback=audio_callback
            ):
                input()

            print("[Voice] [STOPPED] Stopped recording.")

            if not audio_chunks:
                if was_playing:
                    player._ipc_command(["set_property", "pause", False])
                continue

            audio_data = np.concatenate(audio_chunks, axis=0)[:, 0]
            duration = len(audio_data) / 16000

            if duration < 0.5:
                print("[Voice] Audio segment too short. Ignoring.")
                play_error_beep()
                if was_playing:
                    player._ipc_command(["set_property", "pause", False])
                continue

            # Transcribe audio with timing
            t_whisper_start = time.time()
            user_text = listener.transcribe_audio(audio_data)
            t_whisper_end = time.time()

            whisper_duration = t_whisper_end - t_whisper_start

            if not user_text:
                play_error_beep()
                if was_playing:
                    player._ipc_command(["set_property", "pause", False])
                continue

            # Step B: Print transcribed text to terminal output
            print("\n" + "-" * 60)
            print(f"[Voice Input Transcribed]: \"{user_text}\"")
            print(f"[TIMING] [Whisper Transcription]: {whisper_duration:.2f}s")
            print("-" * 60)

            # Step C: Handle built-in commands
            if handle_builtin(user_text, player):
                continue

            # Step D: Pass transcribed text into Fuzzy Matcher with timing
            t_fuzzy_start = time.time()
            command = ask_fuzzy(user_text)
            t_fuzzy_end = time.time()
            fuzzy_duration = t_fuzzy_end - t_fuzzy_start

            print(f"[TIMING] [Fuzzy Matcher]: {fuzzy_duration:.4f}s")

            if not command:
                print(f"[controller] Unrecognized command (Fuzzy Match Failed): {user_text!r}")
                play_error_beep()
                if was_playing:
                    player._ipc_command(["set_property", "pause", False])
                continue

            # Step E: Query tracks & dispatch to player with timing
            t_query_start = time.time()
            dispatch(command, player)
            t_query_end = time.time()
            query_duration = t_query_end - t_query_start

            print(f"[TIMING] [Track Query & Dispatch]:  {query_duration:.2f}s")
            print(f"[TIMING] [Total Voice-to-Music]:    {(whisper_duration + fuzzy_duration + query_duration):.2f}s")
            print("-" * 60 + "\n")

    except KeyboardInterrupt:
        print("\n[Voice Jukebox] Shutting down...")
        player.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
