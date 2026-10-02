"""
usbc_check.py - can this Windows laptop charge over USB-C?

Two checks:
  1. Hardware scan   Looks for USB Type-C / Power Delivery controllers (UCSI, Thunderbolt, USB4).
                     A hint only: it shows Type-C ports exist, not that they accept charging.
  2. Live test       The real answer. Unplug the normal charger, plug in a USB-C PD charger,
                     and the script watches whether Windows sees external power and what the
                     battery does.

No pip installs needed (standard library + PowerShell).
Usage:  python usbc_check.py
"""

import json
import re
import subprocess
import sys
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ps_json(cmd, timeout=30):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
        ).stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def battery():
    rows = ps_json(
        "Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus | Select-Object -First 1 "
        "PowerOnline,Charging,Discharging,ChargeRate,DischargeRate | ConvertTo-Json -Compress"
    )
    if not rows:
        return None
    d = rows[0]
    return {
        "ac": bool(d.get("PowerOnline")),
        "charging": bool(d.get("Charging")),
        "discharging": bool(d.get("Discharging")),
        "charge_w": (d.get("ChargeRate") or 0) / 1000.0,
        "discharge_w": (d.get("DischargeRate") or 0) / 1000.0,
    }


# ---------------- step 1: hardware scan ----------------
def hardware_scan():
    print("=" * 60)
    print("STEP 1: Hardware scan")
    print("=" * 60)

    cs = ps_json("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json -Compress")
    prod = ps_json("Get-CimInstance Win32_ComputerSystemProduct | Select-Object Version | ConvertTo-Json -Compress")
    if cs:
        name = f"{cs[0].get('Manufacturer', '')} {cs[0].get('Model', '')}".strip()
        ver = (prod[0].get("Version") if prod else "") or ""
        print(f"Laptop: {name}" + (f"  ({ver})" if ver else ""))
        print("Tip: search that name + 'USB-C power delivery input' on the maker's spec page.\n")

    if battery() is None:
        print("No battery detected - this may not be a laptop.\n")

    print("Scanning devices for Type-C / PD controllers (takes a few seconds)...")
    devs = ps_json("Get-PnpDevice -PresentOnly | Select-Object FriendlyName,Class,Status | ConvertTo-Json -Compress", 60)
    pat = re.compile(r"UCSI|USB Connector Manager|Type-C|Thunderbolt|USB4|Power Delivery|UCM", re.I)
    hits = sorted({(d.get("FriendlyName") or "").strip() for d in devs if pat.search(d.get("FriendlyName") or "")})

    if hits:
        print("\nFound:")
        for h in hits:
            print(f"  - {h}")
        print("\nResult: this laptop has a USB Type-C controller, so it has Type-C ports with")
        print("        Power Delivery hardware. That does NOT prove they accept charging -")
        print("        some ports are data/video only. Run the live test below to be sure.\n")
    else:
        print("\nNo Type-C / PD controller found by Windows.")
        print("Result: USB-C ports (if any) are probably data/video only, or use a vendor")
        print("        controller Windows doesn't list. The live test can still confirm.\n")


# ---------------- step 2: live test ----------------
def wait_for(cond, timeout, label):
    start = time.time()
    while time.time() - start < timeout:
        b = battery()
        if b and cond(b):
            return b
        remaining = int(timeout - (time.time() - start))
        print(f"\r  {label} ({remaining:3d}s left)   ", end="", flush=True)
        time.sleep(1)
    print()
    return None


def live_test():
    print("=" * 60)
    print("STEP 2: Live charging test")
    print("=" * 60)
    print("You need a USB-C PD charger and a cable rated for it (ideally 5 A / 100 W e-marked).")
    ans = input("Run the live test now? [y/N] ").strip().lower()
    if ans != "y":
        print("Skipped.")
        return

    b = battery()
    if b is None:
        print("Can't read the battery - cannot run the live test.")
        return

    if b["ac"]:
        print("\n1) UNPLUG the normal (barrel) charger. Leave the laptop on battery.")
        b = wait_for(lambda x: not x["ac"], 90, "waiting for you to unplug")
        if b is None:
            print("\nStill on external power after 90 s. Unplug all chargers and run again.")
            return
        print("\n  On battery - good.")
    else:
        print("\nAlready on battery - good.")

    print("\n2) Now plug the USB-C charger into the laptop's USB-C port.")
    print("   (Do NOT plug in the barrel charger.)")
    b = wait_for(lambda x: x["ac"], 90, "waiting for USB-C power")
    if b is None:
        print("\n\nRESULT: Windows never saw external power.")
        print("  The port probably doesn't accept charging (data/video only), or this")
        print("  charger/cable didn't negotiate. Try a different port, cable, or PD charger")
        print("  before concluding. Check the port for a charging/lightning-bolt symbol.")
        return

    print("\n\n  External power detected! Letting the negotiation settle...")
    time.sleep(8)
    samples = []
    for _ in range(5):
        s = battery()
        if s:
            samples.append(s)
        time.sleep(2)
    if not samples:
        print("Lost battery readings - try again.")
        return
    s = samples[-1]

    print("\nRESULT:")
    if not s["ac"]:
        print("  Power dropped out again - cable or charger is unreliable. Try another.")
    elif s["charging"] and s["charge_w"] > 0:
        print(f"  USB-C charging WORKS. Battery is taking {s['charge_w']:.1f} W on top of what the laptop uses.")
        print("  The charger is delivering more than the idle load.")
    elif s["discharging"] and s["discharge_w"] > 0:
        print("  USB-C power is detected, but the battery is STILL DISCHARGING "
              f"({s['discharge_w']:.1f} W).")
        print("  The laptop accepts USB-C power but this charger/cable is too weak for the")
        print("  current load (or the laptop limits USB-C input). Fine for light use only;")
        print("  try a higher-wattage PD charger and a 5 A rated cable.")
    else:
        print("  USB-C charging WORKS. External power is detected and the battery is idle")
        print("  (likely full). Run power_monitor.py to see how it behaves under load.")


def main():
    if sys.platform != "win32":
        print("This script is for Windows.")
        return
    hardware_scan()
    live_test()
    input("\nPress Enter to exit.")


if __name__ == "__main__":
    main()
