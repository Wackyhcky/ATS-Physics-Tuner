"""Auto-switch the steering wheel rotation when you change between truck and car.

ATS keeps a single g_steer_anim_range per profile and has no outside API to change
it while running. This watches game.log.txt for the vehicle switch lines

    Desktop screen config switched to 'car'
    [actor] Vehicle suspension class is standard.

and, once the ATS window is in front, types the command into the game console
(~, command, Enter, ~) with synthetic key presses.
"""
import ctypes
import os
import re
import threading
import time
from ctypes import wintypes

import tuner_core as core

SWITCH_RE = re.compile(r"Desktop screen config switched to '(truck|car)'")
ACTOR_RE = re.compile(r"\[actor\] Vehicle suspension class is (\w+)")
GAME_EXE = "amtrucks.exe"

# ------------------------------------------------------------------ win32 input
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
SC_GRAVE = 0x29   # the ~ / ` key (console) - layout independent scancode
SC_ENTER = 0x1C
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)


def _send(*events):
    arr = (INPUT * len(events))()
    for i, (scan, flags) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _tap_scan(scan):
    _send((scan, KEYEVENTF_SCANCODE), (scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))


def _type_text(text):
    for ch in text:
        _send((ord(ch), KEYEVENTF_UNICODE), (ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(0.01)


def game_in_front():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return False
        return os.path.basename(buf.value).lower() == GAME_EXE
    finally:
        kernel32.CloseHandle(h)


def send_console_command(cmd):
    _tap_scan(SC_GRAVE)          # open console
    time.sleep(0.25)
    _type_text(cmd)
    time.sleep(0.05)
    _tap_scan(SC_ENTER)
    time.sleep(0.15)
    _tap_scan(SC_GRAVE)          # close console


# --------------------------------------------------------------------- watcher
class AutoSwitcher:
    """Background thread. get_rotation(cls) -> degrees; on_status(str) for the UI."""

    def __init__(self, log_path, get_rotation, on_status, set_hardware=None):
        """set_hardware(deg) -> str: optional, e.g. MOZA wheelbase; raise to retry later."""
        self.log_path = log_path
        self.get_rotation = get_rotation
        self.on_status = on_status
        self.set_hardware = set_hardware
        self.hw_applied = None
        self._hw_next_try = 0
        self._hw_msg = ""
        self.current = None      # 'truck' / 'car' (last vehicle the game switched to)
        self.applied = None      # degrees we last sent to the game
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _scan(self, text):
        for line in text.splitlines():
            m = SWITCH_RE.search(line)
            if m:
                self.current = m.group(1)
                continue
            m = ACTOR_RE.search(line)
            if m and self.current is None:
                # fallback when we only saw the actor line: cars use the 'standard' class
                self.current = "car" if m.group(1) == "standard" else "truck"

    def _run(self):
        pos, size_seen = 0, -1
        front_since = None
        while not self._stop.is_set():
            try:
                size = os.path.getsize(self.log_path)
                if size < size_seen:      # game restarted -> new log
                    pos, self.applied = 0, None
                size_seen = size
                if size > pos:
                    with open(self.log_path, encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    self._scan(chunk)
            except OSError:
                pass

            if self.current and self.set_hardware:
                self._update_hardware(self.get_rotation(self.current))

            if self.current and core.game_running():
                want = self.get_rotation(self.current)
                if want != self.applied:
                    if game_in_front():
                        front_since = front_since or time.time()
                        # give the player a moment after alt-tabbing / loading
                        if time.time() - front_since > 1.5:
                            send_console_command(f"{core.STEER_CVAR} {core.fmt(want)}")
                            self.applied = want
                            self.on_status(f"Auto-switch: {self.current} -> {core.fmt(want)} deg (sent to game)"
                                           + self._hw_msg)
                    else:
                        front_since = None
                        self.on_status(f"Auto-switch: {self.current} -> {core.fmt(want)} deg "
                                       "(waiting for ATS window)")
                else:
                    front_since = None
                    self.on_status(f"Auto-switch: driving {self.current}, rotation {core.fmt(want)} deg"
                                   + self._hw_msg)
            else:
                self.on_status("Auto-switch: waiting for game" if not self.current
                               else f"Auto-switch: last vehicle {self.current} (game not running)")
            self._stop.wait(0.5)

    def _update_hardware(self, want):
        if want == self.hw_applied or time.time() < self._hw_next_try:
            return
        try:
            self._hw_msg = " | " + self.set_hardware(want)
            self.hw_applied = want
        except Exception as e:  # noqa: BLE001 - report and retry in a few seconds
            self._hw_msg = f" | wheel: {e}"
            self._hw_next_try = time.time() + 5
