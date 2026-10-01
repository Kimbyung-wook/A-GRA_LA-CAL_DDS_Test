"""Wall-clock timestamps precise enough to order sub-millisecond trace events across processes.

On Windows, time.time() (Python <= 3.12) is GetSystemTimeAsFileTime(), which only advances every
0.5-15.6 ms depending on the system timer resolution. Events of one message chain that happen within a
millisecond on two CAL Servers then share a timestamp, and a monitor cannot tell cause from effect.
GetSystemTimePreciseAsFileTime() reads the same system clock with sub-microsecond resolution, so
timestamps from different processes on one host stay comparable. Elsewhere time.time() is already
fine-grained.
"""
import sys
import time

if sys.platform == "win32":
    import ctypes

    _precise = ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime
    _EPOCH_OFFSET = 116444736000000000  # 1601-01-01 -> 1970-01-01 in 100 ns ticks

    def now() -> float:
        ft = ctypes.c_uint64()
        _precise(ctypes.byref(ft))
        return (ft.value - _EPOCH_OFFSET) / 1e7
else:
    now = time.time
