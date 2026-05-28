"""Read Mac fan RPM directly from the SMC, in-process, with no extra daemon.

Ports the SMC IOConnectCallStructMethod approach from the companion
mac-fan-speed-meter project to pure-Python ctypes so claude-meter can read
fan speed itself — no menu-bar app or helper binary to run alongside.

The connection talks to the AppleSMC kernel driver. The request/response
struct is the canonical 80-byte SMCKeyData_t in C layout: key, then the
action byte (`data8`) at offset 42 and the 32-byte payload at offset 48.
On Apple Silicon (e.g. the M-series Mac mini) the current speed lives in
the plain SMC key F0Ac; F0Mn/F0Mx/F0Tg give the min/max/target.

macOS only. Every entry point degrades to None/[] off macOS or when the
SMC is unreadable, so callers never need to special-case the platform.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from typing import Optional

_KERNEL_INDEX_SMC = 2  # IOConnect selector (kSMCHandleYPCEvent)
_CMD_READ_BYTES = 5
_CMD_READ_KEYINFO = 9
_RESULT_OK = 0
_RESULT_KEY_NOT_FOUND = 0x84


@dataclass
class FanReading:
    index: int
    rpm: float
    min: float
    max: float
    target: Optional[float]


def _make_structs():
    import ctypes

    class _Vers(ctypes.Structure):
        _fields_ = [("major", ctypes.c_ubyte), ("minor", ctypes.c_ubyte),
                    ("build", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte),
                    ("release", ctypes.c_ushort)]

    class _PLimit(ctypes.Structure):
        _fields_ = [("version", ctypes.c_ushort), ("length", ctypes.c_ushort),
                    ("cpuPLimit", ctypes.c_uint32), ("gpuPLimit", ctypes.c_uint32),
                    ("memPLimit", ctypes.c_uint32)]

    class _KeyInfo(ctypes.Structure):
        _fields_ = [("dataSize", ctypes.c_uint32), ("dataType", ctypes.c_uint32),
                    ("dataAttributes", ctypes.c_ubyte)]

    # Canonical SMCKeyData_t. With natural C alignment this is exactly 80
    # bytes (data8 @42, data32 @44, bytes @48) — the layout the kernel
    # expects. Do NOT add an explicit padding field: that is a Swift-only
    # workaround and over-shifts the layout to 84 bytes here.
    class _Param(ctypes.Structure):
        _fields_ = [("key", ctypes.c_uint32),
                    ("vers", _Vers),
                    ("pLimitData", _PLimit),
                    ("keyInfo", _KeyInfo),
                    ("result", ctypes.c_ubyte),
                    ("status", ctypes.c_ubyte),
                    ("data8", ctypes.c_ubyte),
                    ("data32", ctypes.c_uint32),
                    ("bytes", ctypes.c_ubyte * 32)]

    return _Param


def _fourcc(s: str) -> int:
    v = 0
    for b in s.encode("ascii"):
        v = (v << 8) | b
    return v


def _decode_fourcc(v: int) -> str:
    return bytes([(v >> 24) & 0xFF, (v >> 16) & 0xFF,
                  (v >> 8) & 0xFF, v & 0xFF]).decode("ascii", "replace")


class _SMC:
    """Thin SMC client. One open connection reused for the process lifetime."""

    def __init__(self):
        import ctypes

        self._ctypes = ctypes
        self._Param = _make_structs()
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        libc = ctypes.CDLL(None)

        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        iokit.IOServiceOpen.restype = ctypes.c_int
        iokit.IOServiceOpen.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                        ctypes.POINTER(ctypes.c_uint)]
        iokit.IOServiceClose.argtypes = [ctypes.c_uint]
        iokit.IOConnectCallStructMethod.restype = ctypes.c_int
        iokit.IOConnectCallStructMethod.argtypes = [
            ctypes.c_uint, ctypes.c_uint,
            ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        self._iokit = iokit

        task = ctypes.c_uint.in_dll(libc, "mach_task_self_").value
        service = iokit.IOServiceGetMatchingService(
            0, iokit.IOServiceMatching(b"AppleSMC"))
        if not service:
            raise OSError("AppleSMC service not found")
        conn = ctypes.c_uint(0)
        rc = iokit.IOServiceOpen(service, task, 0, ctypes.byref(conn))
        if rc != 0 or not conn.value:
            raise OSError(f"IOServiceOpen failed: rc={rc}")
        self._conn = conn.value

    def close(self):
        if getattr(self, "_conn", 0):
            self._iokit.IOServiceClose(self._conn)
            self._conn = 0

    def _call(self, action: int, inp):
        ctypes = self._ctypes
        inp.data8 = action
        out = self._Param()
        out_size = ctypes.c_size_t(ctypes.sizeof(self._Param))
        rc = self._iokit.IOConnectCallStructMethod(
            self._conn, _KERNEL_INDEX_SMC,
            ctypes.byref(inp), ctypes.sizeof(self._Param),
            ctypes.byref(out), ctypes.byref(out_size))
        return rc, out

    def read_float(self, key: str) -> float:
        inp = self._Param()
        inp.key = _fourcc(key)
        rc, out = self._call(_CMD_READ_KEYINFO, inp)
        if rc != 0:
            raise OSError(f"keyInfo({key}) rc={rc}")
        if out.result == _RESULT_KEY_NOT_FOUND:
            raise KeyError(key)
        size, dtype = out.keyInfo.dataSize, out.keyInfo.dataType

        inp = self._Param()
        inp.key = _fourcc(key)
        inp.keyInfo.dataSize = size
        rc, out = self._call(_CMD_READ_BYTES, inp)
        if rc != 0 or out.result != _RESULT_OK:
            raise OSError(f"readKey({key}) rc={rc} result=0x{out.result:02x}")

        data = bytes(out.bytes[:size])
        t = _decode_fourcc(dtype)
        if t == "flt ":
            return float(struct.unpack("<f", data[:4])[0])
        if t == "fpe2":
            return ((data[0] << 8) | data[1]) / 4.0
        if t in ("ui8 ", "ui16", "ui32"):
            v = 0
            for b in data:
                v = (v << 8) | b
            return float(v)
        if t == "sp78":
            raw = (data[0] << 8) | data[1]
            if raw & 0x8000:
                raw -= 0x10000
            return raw / 256.0
        raise OSError(f"unsupported SMC type {t!r} for {key}")


_smc: Optional[_SMC] = None
_smc_failed = False


def _get_smc() -> Optional[_SMC]:
    global _smc, _smc_failed
    if not sys.platform.startswith("darwin"):
        return None
    if _smc is not None:
        return _smc
    if _smc_failed:
        return None
    try:
        _smc = _SMC()
    except Exception:
        _smc_failed = True
        return None
    return _smc


def _reset_smc():
    global _smc, _smc_failed
    if _smc is not None:
        try:
            _smc.close()
        except Exception:
            pass
    _smc = None
    _smc_failed = False


def read_fans() -> list[FanReading]:
    """All fans the SMC reports. Empty list if unavailable (e.g. non-macOS)."""
    smc = _get_smc()
    if smc is None:
        return []

    def _try(key: str) -> Optional[float]:
        try:
            return smc.read_float(key)
        except Exception:
            return None

    try:
        count = _try("FNum")
        n = int(count) if count else 0
        if n <= 0:  # FNum unreadable on some models — probe sequentially.
            n = 0
            for i in range(8):
                if _try(f"F{i}Ac") is None:
                    break
                n = i + 1

        out: list[FanReading] = []
        for i in range(n):
            actual = _try(f"F{i}Ac")
            if actual is None:
                continue
            out.append(FanReading(
                index=i,
                rpm=actual,
                min=_try(f"F{i}Mn") or 0.0,
                max=_try(f"F{i}Mx") or 0.0,
                target=_try(f"F{i}Tg"),
            ))
        return out
    except Exception:
        _reset_smc()  # drop a wedged connection so the next call reopens it.
        return []


def read_fan(index: int = 0) -> Optional[FanReading]:
    """A single fan (default the first), or None if there isn't one."""
    for f in read_fans():
        if f.index == index:
            return f
    return None
