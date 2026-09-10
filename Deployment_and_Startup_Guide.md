# Jukebox Deployment & Startup Guide

This guide explains how to start the Jukebox system manually for testing, how to set it up to start automatically in the car (headless mode), and how to revert it for debugging.

---

## 1. Manual Startup (Testing & Debugging)

If you are SSH'd into the Pi or have a monitor connected, you can start the system manually.

### A. Testing WITHOUT the UPS (Desk Mode)
When testing at your desk with a standard 5V supply, you **must** use the `--desk-mode` flag on the UPS monitor so it doesn't try to shut down your Pi.

Open two terminal windows:
**Terminal 1 (Jukebox App):**
```bash
source /home/frank/jukebox-env/bin/activate
python3 "/home/frank/jukebox-env/version 3 fuzzy/pure_fuzzy_controller.py"
```

**Terminal 2 (UPS Monitor - Desk Mode):**
```bash
source /home/frank/jukebox-env/bin/activate
python3 /home/frank/jukebox-env/ups_monitor.py --desk-mode
```

### B. Testing WITH the UPS (Car Mode)
When the UPS is connected and you want it to actually shut down the Pi when power is lost.

**Terminal 1 (Jukebox App):**
```bash
source /home/frank/jukebox-env/bin/activate
python3 "/home/frank/jukebox-env/version 3 fuzzy/pure_fuzzy_controller.py"
```

**Terminal 2 (UPS Monitor - Active):**
```bash
source /home/frank/jukebox-env/bin/activate
python3 /home/frank/jukebox-env/ups_monitor.py
```

---

## 2. Setting Up "Headless" Auto-Start (For the Car)

When you are ready to put the system in your car without a display or keyboard, you need to configure the Raspberry Pi to automatically start both scripts in the background the moment it boots up. 

We do this by creating two "systemd services". Run these commands once to set it up.

### Step 1: Create the Auto-Start Files
Run this command to create the Jukebox service:
```bash
sudo tee /etc/systemd/system/jukebox.service > /dev/null << 'EOF'
[Unit]
Description=Voice Jukebox Controller
After=network.target sound.target

[Service]
Type=simple
User=frank
WorkingDirectory="/home/frank/jukebox-env/version 3 fuzzy"
ExecStart=/home/frank/jukebox-env/bin/python3 "/home/frank/jukebox-env/version 3 fuzzy/pure_fuzzy_controller.py"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Run this command to create the UPS Monitor service:
```bash
sudo tee /etc/systemd/system/ups_monitor.service > /dev/null << 'EOF'
[Unit]
Description=UPS Power Monitor
After=network.target

[Service]
Type=simple
User=frank
WorkingDirectory=/home/frank/jukebox-env
ExecStart=/home/frank/jukebox-env/bin/python3 /home/frank/jukebox-env/ups_monitor.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
```

### Step 2: Enable the Auto-Start
Run these commands to tell the Pi to start them on every boot:
```bash
sudo systemctl daemon-reload
sudo systemctl enable jukebox.service
sudo systemctl enable ups_monitor.service
sudo systemctl start jukebox.service
sudo systemctl start ups_monitor.service
```
**That's it!** The next time you plug the Pi into your car, both scripts will launch silently in the background.

---

## 3. Disabling Auto-Start (Flipping Back for Debugging)

If you bring the Pi back to your desk to modify the code, you will want to stop the auto-start services so they aren't fighting with you while you test.

**To stop them temporarily (until next reboot):**
```bash
sudo systemctl stop jukebox.service
sudo systemctl stop ups_monitor.service
```

**To disable them permanently (until you re-enable them):**
```bash
sudo systemctl disable jukebox.service
sudo systemctl disable ups_monitor.service
```

> [!TIP]
> If you just want to check if they are running correctly in the background, use:
> `sudo systemctl status jukebox.service`
> `sudo systemctl status ups_monitor.service`
