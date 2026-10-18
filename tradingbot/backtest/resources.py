"""Arbeitsspeicher messen (Prozess und System) – Windows ohne Zusatzpaket, sonst Linux/psutil.

Windows: GetProcessMemoryInfo (Working Set) und GlobalMemoryStatusEx – dieselben Werte wie im
Task-Manager. Ist psutil installiert, wird es bevorzugt.
"""

from __future__ import annotations

import gc
import os
import sys


def _psutil():
    try:
        import psutil  # optional
        return psutil
    except ImportError:
        return None


def process_mb() -> float | None:
    ps = _psutil()
    if ps:
        return ps.Process().memory_info().rss / 2**20
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.WorkingSetSize / 2**20 if ok else None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def system_mb() -> tuple[float | None, float | None]:
    """(verfügbar, gesamt) in MB."""
    ps = _psutil()
    if ps:
        vm = ps.virtual_memory()
        return vm.available / 2**20, vm.total / 2**20
    if sys.platform == "win32":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullAvailPhys / 2**20, m.ullTotalPhys / 2**20
        return None, None
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0]) / 1024
        return info.get("MemAvailable"), info.get("MemTotal")
    except OSError:
        return None, None


def status() -> str:
    p = process_mb()
    avail, total = system_mb()
    parts = []
    if p is not None:
        parts.append(f"RAM Prozess {p:,.0f} MB")
    if avail is not None and total is not None:
        parts.append(f"frei {avail / 1024:.1f} von {total / 1024:.1f} GB")
    return ", ".join(parts).replace(",", ".", 0) if parts else "RAM unbekannt"


def snapshot() -> dict:
    avail, total = system_mb()
    return {"process_mb": None if process_mb() is None else round(process_mb(), 0),
            "available_mb": None if avail is None else round(avail, 0),
            "total_mb": None if total is None else round(total, 0), "pid": os.getpid()}


def free_memory() -> None:
    gc.collect()
