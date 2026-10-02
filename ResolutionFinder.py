"""
Resolution Finder - opens fullscreen and shows the monitor's resolution in the middle.

    python ResolutionFinder.py

Keys
    Space / Left-click / Right arrow   next monitor (if you have more than one)
    Left arrow                         previous monitor
    C                                  copy the resolution (e.g. 1920x1080) to the clipboard
    Esc / Q / Right-click              quit

It opens on the monitor your mouse is on. The big number is the monitor's real (native) pixel
size as Windows reports it - not the "scaled" size that apps see when Windows display scaling
is at 125% / 150%. The border and corner labels let you check the picture reaches every edge.

Compile to an .exe (optional):
    pip install pyinstaller
    pyinstaller --onefile --noconsole ResolutionFinder.py

No packages needed - just Python with Tkinter.
"""

import ctypes
import math
import os
import tkinter as tk
from tkinter import font as tkfont

BG = "#0b0f14"
ACCENT = "#22d3ee"
TEXT = "#f8fafc"
MUTED = "#94a3b8"
FONT_FAMILY = "Segoe UI"            # falls back to Tk's default if not installed


# ----------------------------------------------------------------------------------------------
#  Windows monitor details (ctypes, no extra packages). Everything here is best-effort: if any
#  call fails the app just falls back to Tk's own idea of the screen size.
# ----------------------------------------------------------------------------------------------

def set_dpi_aware():
    """Must run BEFORE the Tk window is created. Without it Windows hands the app a scaled-down
    desktop size (e.g. 1536x864 on a 1920x1080 screen at 125%) and everything looks blurry."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)        # per-monitor aware
    except Exception:                                          # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                                      # noqa: BLE001
            pass


def _win_structs():
    """The three Win32 structures we need, built from fixed-size ctypes types (so the layout is
    identical on any platform - handy for testing)."""
    i32, u32, u16 = ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint16

    class RECT(ctypes.Structure):
        _fields_ = [("left", i32), ("top", i32), ("right", i32), ("bottom", i32)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", u32), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", u32),
                    ("szDevice", u16 * 32)]

    class DEVMODEW(ctypes.Structure):
        _fields_ = [("dmDeviceName", u16 * 32), ("dmSpecVersion", u16), ("dmDriverVersion", u16),
                    ("dmSize", u16), ("dmDriverExtra", u16), ("dmFields", u32),
                    ("dmPositionX", i32), ("dmPositionY", i32),
                    ("dmDisplayOrientation", u32), ("dmDisplayFixedOutput", u32),
                    ("dmColor", ctypes.c_int16), ("dmDuplex", ctypes.c_int16),
                    ("dmYResolution", ctypes.c_int16), ("dmTTOption", ctypes.c_int16),
                    ("dmCollate", ctypes.c_int16), ("dmFormName", u16 * 32),
                    ("dmLogPixels", u16), ("dmBitsPerPel", u32), ("dmPelsWidth", u32),
                    ("dmPelsHeight", u32), ("dmDisplayFlags", u32), ("dmDisplayFrequency", u32),
                    ("dmICMMethod", u32), ("dmICMIntent", u32), ("dmMediaType", u32),
                    ("dmDitherType", u32), ("dmReserved1", u32), ("dmReserved2", u32),
                    ("dmPanningWidth", u32), ("dmPanningHeight", u32)]

    return RECT, MONITORINFOEXW, DEVMODEW


def _wstr(units):
    return "".join(chr(u) for u in units if u)


def enumerate_monitors_windows(user32=None, shcore=None):
    """Every attached monitor: its position/size on the virtual desktop, its native resolution
    and refresh rate (from the display driver), and its display-scaling percentage."""
    RECT, MONITORINFOEXW, DEVMODEW = _win_structs()
    if user32 is None:
        user32 = ctypes.windll.user32
    if shcore is None:
        try:
            shcore = ctypes.windll.shcore
        except Exception:                                      # noqa: BLE001
            shcore = None

    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    proc_type = callback_type(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(RECT), ctypes.c_ssize_t)
    found = []

    def on_monitor(hmon, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.pointer(info)):
            return 1
        r = info.rcMonitor
        mon = {"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
               "device": _wstr(info.szDevice), "primary": bool(info.dwFlags & 1),
               "native": None, "hz": None, "scale": None}
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        try:
            if user32.EnumDisplaySettingsW(mon["device"], -1, ctypes.pointer(dm)):   # -1 = current mode
                if dm.dmPelsWidth and dm.dmPelsHeight:
                    mon["native"] = (int(dm.dmPelsWidth), int(dm.dmPelsHeight))
                if dm.dmDisplayFrequency and dm.dmDisplayFrequency > 1:              # 0/1 = "default"
                    mon["hz"] = int(dm.dmDisplayFrequency)
        except Exception:                                                            # noqa: BLE001
            pass
        if shcore is not None:
            try:
                dpi_x, dpi_y = ctypes.c_uint(0), ctypes.c_uint(0)
                if shcore.GetDpiForMonitor(hmon, 0, ctypes.pointer(dpi_x), ctypes.pointer(dpi_y)) == 0:
                    mon["scale"] = round(dpi_x.value / 96.0 * 100)
            except Exception:                                                        # noqa: BLE001
                pass
        found.append(mon)
        return 1

    user32.EnumDisplayMonitors(None, None, proc_type(on_monitor), 0)
    found.sort(key=lambda m: m["device"])                       # DISPLAY1, DISPLAY2 ... like Windows
    return found


def get_monitors(root):
    """Monitors to cycle through. Falls back to a single 'screen' straight from Tk."""
    mons = []
    if os.name == "nt":
        try:
            mons = enumerate_monitors_windows()
        except Exception:                                                            # noqa: BLE001
            mons = []
    if not mons:
        mons = [{"x": 0, "y": 0, "w": root.winfo_screenwidth(), "h": root.winfo_screenheight(),
                 "device": "", "primary": True, "native": None, "hz": None, "scale": None}]
    return mons


# ----------------------------------------------------------------------------------------------
#  Helpers
# ----------------------------------------------------------------------------------------------

_COMMON_RATIOS = [(16, 9, 0.01), (16, 10, 0.01), (4, 3, 0.01), (5, 4, 0.01), (3, 2, 0.01),
                  (21, 9, 0.03), (32, 9, 0.01), (1, 1, 0.01), (9, 16, 0.01)]


def aspect_label(w, h):
    """'16:9', '21:9' ... the usual name for a resolution's shape, else the reduced ratio."""
    if not w or not h:
        return ""
    ratio = w / h
    for a, b, tol in _COMMON_RATIOS:
        if abs(ratio - a / b) / (a / b) <= tol:
            return f"{a}:{b}"
    g = math.gcd(int(w), int(h))
    return f"{int(w) // g}:{int(h) // g}"


def monitor_at(monitors, x, y):
    for i, m in enumerate(monitors):
        if m["x"] <= x < m["x"] + m["w"] and m["y"] <= y < m["y"] + m["h"]:
            return i
    for i, m in enumerate(monitors):
        if m["primary"]:
            return i
    return 0


# ----------------------------------------------------------------------------------------------
#  The fullscreen window
# ----------------------------------------------------------------------------------------------

class ResolutionFinder:
    def __init__(self, root, monitors=None):
        self.root = root
        self.monitors = monitors or get_monitors(root)
        try:
            px, py = root.winfo_pointerxy()
        except tk.TclError:
            px, py = 0, 0
        self.index = monitor_at(self.monitors, px, py)

        root.configure(bg=BG)
        root.overrideredirect(True)                     # no title bar / borders
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0, cursor="none")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        for key in ("<Escape>", "<q>", "<Q>"):
            root.bind(key, lambda _e: self.quit())
        for key in ("<space>", "<Right>", "<Tab>"):
            root.bind(key, lambda _e: self.step(1))
        root.bind("<Left>", lambda _e: self.step(-1))
        for key in ("<c>", "<C>"):
            root.bind(key, lambda _e: self.copy())
        self.canvas.bind("<Button-1>", lambda _e: self.step(1))
        self.canvas.bind("<Button-3>", lambda _e: self.quit())
        self.canvas.bind("<Configure>", lambda _e: self.draw())

        self.show_monitor()
        root.after(100, root.focus_force)

    # -- what to display ----------------------------------------------------------------------
    def current(self):
        return self.monitors[self.index]

    def resolution(self, mon=None):
        mon = mon or self.current()
        return mon["native"] or (mon["w"], mon["h"])

    def step(self, delta):
        if len(self.monitors) > 1:
            self.index = (self.index + delta) % len(self.monitors)
            self.show_monitor()

    def show_monitor(self):
        m = self.current()
        self.root.geometry(f"{m['w']}x{m['h']}+{m['x']}+{m['y']}")      # cover exactly this monitor
        self.root.update_idletasks()
        self.draw()
        self.root.after(30, self.root.focus_force)      # a borderless window can lose keyboard focus when it moves

    def copy(self):
        w, h = self.resolution()
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{w}x{h}")
        self.flash = "Copied to clipboard"
        self.draw()
        self.root.after(1200, self._clear_flash)

    flash = ""

    def _clear_flash(self):
        self.flash = ""
        self.draw()

    def quit(self):
        self.root.destroy()

    # -- drawing ------------------------------------------------------------------------------
    def info_lines(self):
        m = self.current()
        w, h = self.resolution()
        bits = [aspect_label(w, h)]
        if m["hz"]:
            bits.append(f"{m['hz']} Hz")
        if m["scale"]:
            bits.append(f"{m['scale']}% scaling")
        line2 = "   \u2022   ".join(b for b in bits if b)
        where = f"Display {self.index + 1} of {len(self.monitors)}"
        if m["primary"] and len(self.monitors) > 1:
            where += " (primary)"
        if m["device"]:
            where += f"   {m['device']}"
        return f"{w} \u00d7 {h}", line2, where

    def fit_font(self, text, max_w, max_h, family, weight="bold"):
        size = int(max_h)
        f = tkfont.Font(root=self.root, family=family, size=-size, weight=weight)
        while size > 10 and f.measure(text) > max_w:
            size = int(size * 0.94)
            f.configure(size=-size)
        return f

    def draw(self):
        c = self.canvas
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        m = self.current()
        if cw < 50 or ch < 50:                          # not laid out yet
            cw, ch = m["w"], m["h"]
        w, h = self.resolution()
        main, line2, where = self.info_lines()
        family = FONT_FAMILY

        # border + corner labels, so you can see the image really reaches every edge
        c.create_rectangle(2, 2, cw - 3, ch - 3, outline=ACCENT, width=3)
        small = tkfont.Font(root=self.root, family=family, size=-max(12, ch // 60))
        pad = max(12, ch // 70)
        c.create_text(pad, pad, text="0, 0", anchor="nw", fill=MUTED, font=small)
        c.create_text(cw - pad, pad, text=f"{w - 1}, 0", anchor="ne", fill=MUTED, font=small)
        c.create_text(pad, ch - pad, text=f"0, {h - 1}", anchor="sw", fill=MUTED, font=small)
        c.create_text(cw - pad, ch - pad, text=f"{w - 1}, {h - 1}", anchor="se", fill=MUTED, font=small)

        # the resolution, as big as fits (up to ~70% of the width)
        big = self.fit_font(main, cw * 0.70, ch * 0.22, family)
        cy = ch * 0.45
        c.create_text(cw / 2, cy, text=main, fill=TEXT, font=big)

        y = cy + big.metrics("linespace") * 0.62
        sub = tkfont.Font(root=self.root, family=family, size=-max(16, ch // 28))
        if line2:
            c.create_text(cw / 2, y, text=line2, fill=ACCENT, font=sub)
            y += sub.metrics("linespace") * 1.25
        c.create_text(cw / 2, y, text=where, fill=MUTED, font=sub)

        hint = "Space / click: next monitor   \u2022   \u2190 previous   \u2022   C: copy   \u2022   Esc: quit"
        if len(self.monitors) < 2:
            hint = "C: copy   \u2022   Esc: quit"
        hint_font = tkfont.Font(root=self.root, family=family, size=-max(12, ch // 55))
        c.create_text(cw / 2, ch - max(40, ch // 12), text=self.flash or hint,
                      fill=ACCENT if self.flash else MUTED, font=hint_font)


def main():
    set_dpi_aware()                                     # before Tk exists, or Windows scales everything
    root = tk.Tk()
    ResolutionFinder(root)
    root.mainloop()


if __name__ == "__main__":
    main()
