#!/bin/bash
# setup_pi.sh - Automated recovery script for Voice Jukebox Controller

echo "=========================================="
echo " Starting Jukebox System Setup/Recovery   "
echo "=========================================="

# 1. System Dependencies
echo "[1/7] Installing system dependencies (vlc, mpv, i2c-tools)..."
sudo apt-get update
sudo apt-get install -y vlc mpv python3-pip python3-venv i2c-tools curl

# 2. Configure Hardware (I2C)
echo "[2/7] Enabling I2C bus..."
sudo raspi-config nonint do_i2c 0

# 3. Setup Python Virtual Environment
echo "[3/7] Setting up Python Virtual Environment..."
cd ~
if [ ! -d "jukebox-env" ]; then
    python3 -m venv jukebox-env
fi
source ~/jukebox-env/bin/activate

# 4. Install Python Packages
echo "[4/7] Installing Python packages..."
pip install pygame pydub SpeechRecognition pyaudio gpiod pi-ina219 smbus2 spidev ollama

# 5. Configure ALSA Audio
echo "[5/7] Configuring ALSA audio..."
# Remove any conflicting local asoundrc that breaks dmix
rm -f ~/.asoundrc

# 6. Sudo Shutdown Permissions
echo "[6/7] Enabling passwordless shutdown for UPS monitor..."
echo "frank ALL=(ALL) NOPASSWD: /sbin/shutdown, /usr/sbin/shutdown" | sudo tee /etc/sudoers.d/010_frank-nopasswd-shutdown

# 7. Install Ollama and Models
echo "[7/7] Installing Ollama and downloading Llama3 model (This might take a while)..."
if ! command -v ollama &> /dev/null
then
    curl -fsSL https://ollama.com/install.sh | sh
fi
# Start ollama in background just to pull the model
ollama serve &
OLLAMA_PID=$!
sleep 5
ollama pull llama3
kill $OLLAMA_PID

echo "=========================================="
echo " Setup Complete! You may want to reboot.  "
echo "=========================================="
