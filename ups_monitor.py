#!/usr/bin/env python3
import argparse
import time
import subprocess
from ina219 import INA219
from ina219 import DeviceRangeError

# The UPS outputs a solid 5.16V when the 12V supply is connected.
# When the 12V supply is removed, the supercapacitors slowly drain, and the voltage drops.
# A threshold of 5.00V is well below any normal load droop, but catches the power loss
# early enough (usually within 10-15 seconds) to safely shut down the Pi.
THRESHOLD_VOLTAGE = 5.00

def main():
    parser = argparse.ArgumentParser(description="UPS Power Monitor")
    parser.add_argument('--desk-mode', action='store_true', help="Run in Desk Mode (disables actual shutdown)")
    args = parser.parse_args()

    try:
        ina = INA219(0.1, 2.0, address=0x40, busnum=1)
        ina.configure()
    except Exception as e:
        print(f"[UPS Monitor] ERROR: Could not connect to INA219. {e}")
        return

    print(f"[UPS Monitor] Started monitoring INA219 voltage on I2C address 0x40.")
    if args.desk_mode:
        print(f"[UPS Monitor] Running in DESK MODE. Safe testing active. No real shutdown will occur.")
    print(f"[UPS Monitor] Shutdown threshold set to {THRESHOLD_VOLTAGE}V.")

    # Grace period: Wait for supercapacitors to charge fully on boot before arming
    print("[UPS Monitor] Waiting for UPS capacitors to charge above threshold...")
    while True:
        try:
            v = ina.voltage()
            if v >= THRESHOLD_VOLTAGE:
                print(f"[UPS Monitor] Voltage stable at {v:.3f}V. Arming power loss monitor.")
                break
        except Exception:
            pass
        time.sleep(1)

    while True:
        try:
            v = ina.voltage()
            if v < THRESHOLD_VOLTAGE:
                print(f"[UPS Monitor] WARNING: Voltage dropped to {v:.3f}V! Verifying...")
                time.sleep(3)
                v_verify = ina.voltage()
                if v_verify < THRESHOLD_VOLTAGE:
                    if args.desk_mode:
                        print(f"[UPS Monitor] DESK MODE: Power loss confirmed ({v_verify:.3f}V). Skipping shutdown.")
                    else:
                        print(f"[UPS Monitor] Power loss confirmed ({v_verify:.3f}V). Initiating safe shutdown now.")
                        subprocess.run(["sudo", "shutdown", "-h", "now"])
                        break
                else:
                    print(f"[UPS Monitor] Voltage recovered to {v_verify:.3f}V. Shutdown cancelled.")
        except DeviceRangeError:
            pass
        except Exception as e:
            # If the I2C bus fails temporarily, just ignore it and try again next loop
            pass
            
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[UPS Monitor] Exiting.")
