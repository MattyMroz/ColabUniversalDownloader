"""
    Unified progress reporting primitives for all downloaders.

    This module defines a small, backend-agnostic contract (ProgressUpdate)
    and a lightweight console renderer (ConsoleProgress) that can be used
    across Mega, PixelDrain, YouTube, etc.

    Usage
    -----
    from utils.progress import ProgressUpdate, ConsoleProgress

    progress = ConsoleProgress()
    # Pass `progress` as a callback where supported, e.g.:
    # pixeldrain.download(url, progress=progress)
    # mega.download_file(url, progress=progress)

    Notes
    -----
    - The callback is best-effort: some backends may not provide total size
    or fine-grained bytes; the renderer will still display an updating line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
import sys
import time

__all__ = [
    "ProgressUpdate",
    "ProgressCallback",
    "ConsoleProgress",
]


@dataclass
class ProgressUpdate:
    source: str  # e.g. "pixeldrain" | "mega" | "youtube"
    stage: str   # e.g. "starting" | "downloading" | "merging" | "done" | "error"
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    percent: Optional[float] = None  # 0..100
    speed_bps: Optional[float] = None
    eta_sec: Optional[float] = None
    filename: Optional[str] = None
    item_idx: Optional[int] = None
    item_count: Optional[int] = None
    message: Optional[str] = None


ProgressCallback = Callable[[ProgressUpdate], None]


class ConsoleProgress:
    """
        Very small single-line console progress renderer.

        Prints an updating line with carriage return. When a task completes (stage
        == 'done' or percent >= 100), a newline is printed.
    """

    def __init__(self) -> None:
        self._last_len: int = 0
        self._last_flush: float = 0.0
        self._last_line: str = ""
        # Fixed column widths
        self._W_NAME = 70
        self._W_PCT = 7    # e.g. '100.0%'
        self._W_BYTES = 23 # e.g. '415.6KB / 415.6KB'
        self._W_SPEED = 10 # e.g. '271.4KB/s'
        self._W_ETA = 12   # e.g. 'ETA 00:00:05'

    def __call__(self, u: ProgressUpdate) -> None:
        # Throttle to ~20 Hz to reduce spam
        now = time.time()
        if (now - self._last_flush) < 0.05 and (u.stage not in ("done", "error")):
            return
        self._last_flush = now

        # Compose structured columns similar to wget
        cols = []
        # [source] and optional index
        src = u.source or "?"
        if u.item_idx and u.item_count:
            src = f"{src} [{u.item_idx}/{u.item_count}]"
        cols.append(f"[{src}]")
        # filename (if available)
        if u.filename:
            cols.append(self._fit(u.filename, self._W_NAME))
        # percent first
        pct_val = u.percent
        # Clamp in-progress >=100% to 99.9 to avoid double 100% lines
        if u.stage not in ("done", "error") and pct_val is not None and pct_val >= 100.0:
            pct_val = 99.9
        # Force 100% at done when total known
        if u.stage in ("done", "error") and ((pct_val is None) or pct_val < 100.0) and (u.total_bytes is not None):
            pct_val = 100.0
        if pct_val is not None:
            pct = f"{pct_val:5.1f}%"
            cols.append(self._pad(pct, self._W_PCT))
        # downloaded / total next
        show_bytes = (u.downloaded_bytes or 0) > 0 or (u.total_bytes is not None)
        if show_bytes:
            left_bytes = u.downloaded_bytes or 0
            # At done prefer showing full total on the left as well
            if u.stage in ("done", "error") and (u.total_bytes is not None):
                left_bytes = int(u.total_bytes)
            left = self._fmt_bytes(left_bytes)
            if u.total_bytes is not None and u.total_bytes > 0:
                right = self._fmt_bytes(u.total_bytes)
                bt = f"{left} / {right}"
            else:
                bt = left
            cols.append(self._pad(bt, self._W_BYTES))
        # speed then
        if u.speed_bps is not None:
            cols.append(self._pad(self._fmt_speed(u.speed_bps), self._W_SPEED))
        # ETA at the end
        if u.eta_sec is not None:
            cols.append(self._pad(self._fmt_eta(u.eta_sec), self._W_ETA))
        # extra message
        if u.message:
            cols.append(u.message)

        line = " | ".join(str(p) for p in cols if p)
        # Pad to erase previous remnants
        pad = max(0, self._last_len - len(line))
        # If normal update, render as usual and remember line
        if u.stage not in ("done", "error"):
            sys.stdout.write("\r" + line + (" " * pad))
            sys.stdout.flush()
            self._last_len = len(line)
            self._last_line = line
            return

        # For 'done'/'error' print exactly one final 100% line composed above
        pad = max(0, self._last_len - len(line))
        sys.stdout.write("\r" + line + (" " * pad) + "\n")
        sys.stdout.flush()
        self._last_len = 0
        self._last_line = ""

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        v = float(bps)
        i = 0
        while v >= 1024.0 and i < len(units) - 1:
            v /= 1024.0
            i += 1
        return f"{v:4.1f}{units[i]}"  # width stabilized, e.g. ' 6.1MB/s'

    @staticmethod
    def _fmt_eta(sec: float) -> str:
        s = int(max(0, sec))
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        return f"ETA {h:02d}:{m:02d}:{s:02d}"  # fixed HH:MM:SS

    @staticmethod
    def _fmt_bytes(b: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        v = float(b or 0)
        i = 0
        while v >= 1024.0 and i < len(units) - 1:
            v /= 1024.0
            i += 1
        if i == 0:
            return f"{int(v)}{units[i]}"
        return f"{v:.1f}{units[i]}"

    @staticmethod
    def _pad(s: str, width: int) -> str:
        if len(s) >= width:
            return s
        return s + (" " * (width - len(s)))

    @staticmethod
    def _fit(s: str, width: int) -> str:
        if len(s) <= width:
            return s + (" " * (width - len(s)))
        # keep the end of the path/filename
        ell = "…"
        keep = width - len(ell)
        if keep <= 0:
            return s[-width:]
        return ell + s[-keep:]
