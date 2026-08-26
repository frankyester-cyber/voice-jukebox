"""
voice_command_listener.py  –  Voice Command Listener (Robust Terminal Output)
=============================================================================
Captures audio input from a ReSpeaker XVF3800 USB microphone array,
transcribes the spoken audio using OpenAI's Whisper model (faster-whisper),
prints the recognized text, and passes it to ask_ollama() in `ollama_client.py`.

Requirements:
    pip install sounddevice numpy faster-whisper requests
"""

import sys
import os
import io
import time
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from gpiozero import Button

PTT_PIN = 17

# Reconfigure stdout/stderr encoding safely in-place without re-wrapping or closing streams
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Import Ollama integration from ollama_client.py
try:
    from ollama_client import ask_ollama
except ImportError:
    from jukebox_app.ollama_client import ask_ollama

# ---------------------------------------------------------------------------
# Audio Configuration (ReSpeaker XVF3800 Defaults)
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
CHANNELS = 1              # 1 channel (Mono output / processed mic stream)

SILENCE_THRESHOLD = 0.015 # RMS amplitude threshold to detect voice activity
SILENCE_DURATION = 1.5    # Seconds of silence after speech before triggering transcription
MIN_SPEECH_DURATION = 0.5  # Ignore audio snippets shorter than this length (seconds)


def reset_usb_device_physically():
    import os, fcntl, subprocess
    USBDEVFS_RESET = 21780
    print("[Hardware] Attempting physical USB bus reset for ReSpeaker...")
    try:
        result = subprocess.check_output("lsusb | grep 2886:001a", shell=True, text=True)
        if result:
            parts = result.strip().split()
            bus = int(parts[1])
            dev = int(parts[3].strip(':'))
            path = f"/dev/bus/usb/{bus:03d}/{dev:03d}"
            print(f"[Hardware] Found ReSpeaker at {path}, issuing USBDEVFS_RESET...")
            fd = os.open(path, os.O_WRONLY)
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
            os.close(fd)
            print("[Hardware] USB reset successful. Waiting for re-enumeration...")
    except Exception as e:
        print(f"[Hardware] USB reset skipped or failed: {e}")

def find_respeaker_device() -> int | None:
    """Find the sounddevice index for the ReSpeaker XVF3800 audio interface."""
    import time
    
    # Unconditionally kick the USB hardware on boot to fix Pi 4 power-state bugs
    reset_usb_device_physically()
    time.sleep(2)
    
    max_retries = 15
    for attempt in range(max_retries):
        # Force PortAudio to physically rescan the USB bus for newly booted devices
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
            
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            name = dev.get("name", "")
            if ("respeaker" in name.lower() or "xvf3800" in name.lower() or "xvf" in name.lower()) and dev.get("max_input_channels", 0) > 0:
                print(f"[Audio] Found ReSpeaker XVF3800 device at index {idx}: {name}")
                return idx
        
        print(f"[Audio] ReSpeaker not found yet (attempt {attempt+1}/{max_retries}). Waiting for USB enumeration...")
        time.sleep(1)
        
    print("[Audio] ReSpeaker XVF3800 not explicitly found by name. Defaulting to system default input device.")
    return None


class VoiceListener:
    def __init__(self, model_size: str = "tiny.en", device_idx: int | None = None):
        """
        Initialize the Whisper speech recognition model and audio recorder.
        """
        print(f"[Whisper] Loading faster-whisper model '{model_size}'...")
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.device_idx = device_idx if device_idx is not None else find_respeaker_device()
        
        # Setup GPIO Push-to-Talk Button
        try:
            self.ptt_button = Button(PTT_PIN, bounce_time=0.05)
            print(f"[Hardware] PTT Button initialized on GPIO {PTT_PIN}")
        except Exception as e:
            print(f"[Hardware] Warning: Could not initialize PTT Button on GPIO {PTT_PIN}: {e}")
            self.ptt_button = None

    def record_and_transcribe_ptt(self) -> str:
        """
        Listen using Push-to-Talk (press Enter to start recording, press Enter to stop).
        """
        if self.ptt_button:
            print(f"\n[Voice] Waiting for PTT Button (GPIO {PTT_PIN}) to be PRESSED...")
            self.ptt_button.wait_for_press()
            print("[Voice] [RECORDING] Release PTT Button to STOP recording.")
        else:
            input("\n[Voice] Press ENTER to START recording...")
            print("[Voice] [RECORDING] Press ENTER to STOP recording.")

        audio_chunks = []

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"[Audio Status Warning]: {status}", file=sys.stderr)
            audio_chunks.append(indata.copy())

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, device=self.device_idx, dtype="float32", callback=audio_callback):
            if self.ptt_button:
                self.ptt_button.wait_for_release()
            else:
                input()

        print("[Voice] [STOPPED] Stopped recording.")

        if not audio_chunks:
            return ""

        audio_data = np.concatenate(audio_chunks, axis=0)[:, 0]
        duration = len(audio_data) / SAMPLE_RATE

        if duration < MIN_SPEECH_DURATION:
            print("[Voice] Audio segment too short. Ignoring.")
            return ""

        return self.transcribe_audio(audio_data)

    def transcribe_audio(self, audio_data: np.ndarray) -> str:
        """Transcribe floating-point 16kHz mono audio array to text using Whisper."""
        print("[Whisper] Transcribing audio...")
        segments, _ = self.model.transcribe(
            audio_data, 
            beam_size=1, 
            language="en", 
            vad_filter=True, 
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        text = " ".join([segment.text for segment in segments]).strip()
        return text


def main():
    device_index = find_respeaker_device()
    listener = VoiceListener(model_size="tiny.en", device_idx=device_index)

    print("\n" + "=" * 60)
    print(" ReSpeaker XVF3800 + Whisper + Ollama Jukebox Controller")
    print(" Mode: Push-to-Talk (Press Enter to start/stop)")
    print("=" * 60)
    print("Press Ctrl+C to exit.\n")

    try:
        while True:
            # 1. Listen & Transcribe with Push-To-Talk
            user_text = listener.record_and_transcribe_ptt()

            if not user_text:
                continue

            # 2. Print Transcribed Text
            print(f"\n[Transcribed Voice Input]: \"{user_text}\"")

            # 3. Feed Text into ollama_client.py
            command = ask_ollama(user_text)

            # 4. Show processed output command
            print(f"[Resulting Action]: {command}")

    except KeyboardInterrupt:
        print("\n[Voice Controller] Exiting program...")
        sys.exit(0)


if __name__ == "__main__":
    main()
