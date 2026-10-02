"""
power_monitor.py - battery, system and GPU power monitor.

Windows only, standard library + PowerShell + nvidia-smi (no pip installs).

Per line:
  AC       external power detected
  Batt     charge % and remaining / full capacity in Wh
  Volts    battery voltage (V)
  Amps     battery current (A): + charging, - discharging (calculated W / V)
  Battery  battery-side power (W): + charging, - discharging
  System   total the laptop is using (known while on battery = discharge rate)
  GPU      nvidia-smi power.draw
  Left     estimated runtime while discharging

Usage:
  python power_monitor.py
  python power_monitor.py --interval 1 --csv log.csv
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PS = r"""
$s = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus | Select-Object -First 1
$f = Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1
[pscustomobject]@{
  Voltage = $s.Voltage
  Remaining = $s.RemainingCapacity
  ChargeRate = $s.ChargeRate
  DischargeRate = $s.DischargeRate
  PowerOnline = $s.PowerOnline
  Charging = $s.Charging
  Discharging = $s.Discharging
  Full = $f.FullChargedCapacity
} | ConvertTo-Json -Compress
"""


def read_battery():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PS],
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
        ).stdout.strip()
        if not out:
            return None
        d = json.loads(out)

        charge = (d.get("ChargeRate") or 0) / 1000.0        # mW -> W
        discharge = (d.get("DischargeRate") or 0) / 1000.0
        volts = (d.get("Voltage") or 0) / 1000.0            # mV -> V
        remaining_wh = (d.get("Remaining") or 0) / 1000.0   # mWh -> Wh
        full_wh = (d.get("Full") or 0) / 1000.0

        if d.get("Discharging") and discharge > 0:
            watts, state = -discharge, "discharging"
        elif d.get("Charging") and charge > 0:
            watts, state = charge, "charging"
        else:
            watts, state = 0.0, "idle"

        return {
            "watts": watts,
            "state": state,
            "ac": bool(d.get("PowerOnline")),
            "volts": volts if volts > 0 else None,
            "amps": (watts / volts) if volts > 0 else None,
            "remaining_wh": remaining_wh if remaining_wh > 0 else None,
            "full_wh": full_wh if full_wh > 0 else None,
            "percent": (remaining_wh / full_wh * 100) if full_wh > 0 and remaining_wh > 0 else None,
        }
    except Exception:
        return None


def read_gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
        ).stdout.strip().splitlines()
        return sum(float(x) for x in out if x.strip())
    except Exception:
        return None


def fmt(v, unit, width=6, signed=False, dec=1):
    if v is None:
        return "---".rjust(width) + f" {unit}"
    return (f"{v:+{width}.{dec}f}" if signed else f"{v:{width}.{dec}f}") + f" {unit}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--csv", help="log to this CSV file")
    args = ap.parse_args()

    avg_sys, avg_gpu = deque(maxlen=30), deque(maxlen=30)
    f = writer = None
    if args.csv:
        f = open(args.csv, "a", newline="")
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(["time", "ac", "percent", "remaining_wh", "full_wh", "volts",
                             "amps", "battery_w", "system_w", "gpu_w", "runtime_h", "state"])

    print("Amps/Battery: + charging / - discharging | Ctrl+C to stop\n")
    try:
        while True:
            b = read_battery()
            gpu = read_gpu()

            if b is None:
                print(f"{datetime.now():%H:%M:%S}  no battery data | GPU {fmt(gpu, 'W')}")
                time.sleep(args.interval)
                continue

            system = -b["watts"] if b["state"] == "discharging" else None
            runtime_h = (b["remaining_wh"] / system) if (system and b["remaining_wh"]) else None

            if system is not None:
                avg_sys.append(system)
            if gpu is not None:
                avg_gpu.append(gpu)

            cap = (f"{b['remaining_wh']:.1f}/{b['full_wh']:.1f} Wh"
                   if b["remaining_wh"] and b["full_wh"] else "--- Wh")
            left = f" | Left {runtime_h:.1f} h" if runtime_h else ""

            print(f"{datetime.now():%H:%M:%S}  AC {'yes' if b['ac'] else 'no '}"
                  f" | Batt {fmt(b['percent'], '%', 5, dec=0)} {cap}"
                  f" | {fmt(b['volts'], 'V', 5)} {fmt(b['amps'], 'A', 6, True, 2)}"
                  f" | Battery {fmt(b['watts'], 'W', 6, True)}"
                  f" | System {fmt(system, 'W')}"
                  f" | GPU {fmt(gpu, 'W')}{left}  ({b['state']})")

            if writer:
                r = lambda v, n=1: "" if v is None else round(v, n)
                writer.writerow([datetime.now().isoformat(timespec="seconds"), int(b["ac"]),
                                 r(b["percent"], 0), r(b["remaining_wh"]), r(b["full_wh"]),
                                 r(b["volts"], 2), r(b["amps"], 2), r(b["watts"]), r(system),
                                 r(gpu), r(runtime_h), b["state"]])
                f.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        if avg_sys:
            print(f"Avg system: {sum(avg_sys)/len(avg_sys):.1f} W")
        if avg_gpu:
            print(f"Avg GPU:    {sum(avg_gpu)/len(avg_gpu):.1f} W")
    finally:
        if f:
            f.close()


if __name__ == "__main__":
    sys.exit(main())
