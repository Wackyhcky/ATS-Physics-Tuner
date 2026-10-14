"""Set the rotation range of a MOZA wheelbase over its USB serial port.

Protocol from the open-source boxflat project (github.com/Lawstorant/boxflat):
  frame   = 0x7E, len, group, device, cmd_id..., payload..., checksum
  checksum= (13 + sum(frame bytes so far)) % 256
  base device id 19; read group 40, write group 41
  'limit' (id 1) and 'max-angle' (id 23): 2-byte big endian, value = degrees / 2
Uses plain Win32 calls, so no extra Python packages are needed.
"""
import ctypes
import time
from ctypes import wintypes

MOZA_VID = "346E"
START, MAGIC = 0x7E, 13
BASE_ID, READ_GROUP, WRITE_GROUP = 19, 40, 41
CMD_LIMIT, CMD_MAX_ANGLE = 1, 23

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
INVALID = wintypes.HANDLE(-1).value


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [(n, wintypes.DWORD) for n in ("ReadIntervalTimeout", "ReadTotalTimeoutMultiplier",
                                              "ReadTotalTimeoutConstant", "WriteTotalTimeoutMultiplier",
                                              "WriteTotalTimeoutConstant")]


class MozaError(Exception):
    pass


def find_ports():
    """COM ports belonging to MOZA USB devices (VID 346E), from the registry."""
    import winreg
    ports = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB")
    except OSError:
        return ports
    i = 0
    while True:
        try:
            dev = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        if f"VID_{MOZA_VID}" not in dev.upper():
            continue
        dk = winreg.OpenKey(root, dev)
        j = 0
        while True:
            try:
                inst = winreg.EnumKey(dk, j)
            except OSError:
                break
            j += 1
            try:
                pk = winreg.OpenKey(dk, inst + r"\Device Parameters")
                name = winreg.QueryValueEx(pk, "PortName")[0]
                if name.upper().startswith("COM") and name not in ports:
                    ports.append(name)
            except OSError:
                pass
    return ports


def _frame(group, cmd_id, payload=b""):
    body = bytes([START, 1 + len(payload), group, BASE_ID, cmd_id]) + payload
    return body + bytes([(MAGIC + sum(body)) % 256])


class MozaBase:
    def __init__(self, port):
        self.port = port
        self.h = k32.CreateFileW("\\\\.\\" + port, 0xC0000000, 0, None, 3, 0, None)  # RW, OPEN_EXISTING
        if self.h in (INVALID, None):
            err = ctypes.get_last_error()
            if err == 5:
                raise MozaError(f"{port} is in use by another program (usually MOZA Pit House). "
                                "Close Pit House and try again.")
            raise MozaError(f"Can't open {port} (Windows error {err}).")
        t = COMMTIMEOUTS(50, 0, 300, 0, 300)
        k32.SetCommTimeouts(self.h, ctypes.byref(t))

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _write(self, data):
        n = wintypes.DWORD()
        if not k32.WriteFile(self.h, data, len(data), ctypes.byref(n), None) or n.value != len(data):
            raise MozaError(f"Write to {self.port} failed (error {ctypes.get_last_error()}).")

    def _read_all(self, wait=0.4):
        out, end = b"", time.time() + wait
        buf = ctypes.create_string_buffer(256)
        n = wintypes.DWORD()
        while time.time() < end:
            if k32.ReadFile(self.h, buf, 256, ctypes.byref(n), None) and n.value:
                out += buf.raw[:n.value]
        return out

    def get_rotation(self):
        """Current rotation range in degrees, or None if the base didn't answer."""
        self._read_all(0.05)
        self._write(_frame(READ_GROUP, CMD_LIMIT, b"\x00\x00"))
        data = self._read_all()
        # reply: 7E len group|0x80 device(nibbles swapped) id value(2) checksum
        want_group, want_dev = READ_GROUP | 0x80, ((BASE_ID & 0xF) << 4) | (BASE_ID >> 4)
        for i in range(len(data) - 7):
            if (data[i] == START and data[i + 2] == want_group and data[i + 3] == want_dev
                    and data[i + 4] == CMD_LIMIT):
                return int.from_bytes(data[i + 5:i + 7], "big") * 2
        return None

    def set_rotation(self, degrees):
        half = int(round(max(90, min(2700, degrees)) / 2))
        payload = half.to_bytes(2, "big")
        self._write(_frame(WRITE_GROUP, CMD_LIMIT, payload))
        time.sleep(0.03)
        self._write(_frame(WRITE_GROUP, CMD_MAX_ANGLE, payload))
        self._read_all(0.1)
        return half * 2


def set_rotation(degrees, port=None):
    """Find the base and set its rotation. Returns (port, degrees) or raises MozaError."""
    ports = [port] if port else find_ports()
    if not ports:
        raise MozaError("No MOZA USB device found.")
    last = None
    for p in ports:
        try:
            with MozaBase(p) as b:
                return p, b.set_rotation(degrees)
        except MozaError as e:
            last = e
    raise last
