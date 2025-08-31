"""
    Short description
    -----------------
    Downloader for files hosted on pixeldrain.com, using HTTP streaming
    and optional progress callbacks. No prints; raises typed exceptions.

    Usage
    -----
    from utils.pixeldrain import PixelDrainDownloader

    dl = PixelDrainDownloader()
    path = dl.download("https://pixeldrain.com/u/<ID>", dest_dir="./out")

    Example
    -------
    from utils.pixeldrain import PixelDrainDownloader
    from utils.progress import ConsoleProgress

    dl = PixelDrainDownloader()
    cb = ConsoleProgress()
    path = dl.download("https://pixeldrain.com/u/<ID>", dest_dir="./out", progress=cb)

    Notes
    -----
    - `progress` accepts a structured callback (ProgressUpdate).
    - `progress_line` accepts a plain string line formatted consistently with other downloaders.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import IO, Any, Dict, List, Match, Optional, Pattern, TYPE_CHECKING, Callable

import requests

__all__: List[str] = [
    "BaseError",
    "ValidationError",
    "NetworkError",
    "ProcessError",
    "DownloadError",
    # Back-compat aliases
    "PixelDrainError",
    "InvalidUrlError",
    "ToolNotFoundError",
    # Main API
    "PixelDrainDownloader",
]


class BaseError(Exception):
    """
        Base error for PixelDrain operations.
    """


class ValidationError(BaseError):
    """
        Invalid input (bad URL) or malformed metadata.
    """


class NetworkError(BaseError):
    """
        Network failures (metadata or download).
    """


class ProcessError(BaseError):
    """
        External process returned non-zero exit or could not start.
    """


class DownloadError(BaseError):
    """
        Download failed or output file missing after completion.
    """


# Backward-compatible aliases
PixelDrainError = BaseError
InvalidUrlError = ValidationError
ToolNotFoundError = ProcessError


class PixelDrainDownloader:
    """
        Downloader for files hosted on pixeldrain.com.

        Methods
        -------
        download(url, progress=None, dest_dir=None, timeout=10, filename=None) -> str
            Download a file from PixelDrain and return the absolute path to the file.
            Raises a subclass of PixelDrainError on failure.

        probe(url, timeout=10) -> dict
            Fetch basic metadata (name, size) for the given URL.
    """

    FILE_ID_REGEX: Pattern[str] = re.compile(
        r"pixeldrain\.com/(?:u|l)/([A-Za-z0-9]+)")

    def _extract_file_id(self, url: str) -> str:
        """
                Extract file id from PixelDrain URL or raise InvalidUrlError.
        """
        m: Optional[Match[str]] = self.FILE_ID_REGEX.search(url)
        if not m:
            raise ValidationError(f"Invalid PixelDrain URL: {url}")
        return m.group(1)

    def _download_url(self, file_id: str) -> str:
        url: str = f"https://pixeldrain.com/api/file/{file_id}?download"
        return url

    def _info_url(self, file_id: str) -> str:
        url: str = f"https://pixeldrain.com/api/file/{file_id}/info"
        return url

    def probe(self, url: str, *, timeout: int = 10) -> Dict[str, Any]:
        """
            Return metadata for the PixelDrain file.

            Returns a dict with at least keys: name (str), size (int) if available.
        """
        file_id: str = self._extract_file_id(url)
        try:
            resp: requests.Response = requests.get(
                self._info_url(file_id), timeout=timeout)
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json() or {}
            return data
        except requests.RequestException as exc:  # pragma: no cover - network
            raise NetworkError(f"Failed to fetch metadata: {exc}") from exc

    if TYPE_CHECKING:
        from utils.progress import ProgressCallback  # for typing only

    def download(
        self,
        url: str,
        progress: Optional["ProgressCallback"] = None,
        progress_line: Optional[Callable[[str], None]] = None,
        dest_dir: Optional[str] = None,
        timeout: int = 10,
        filename: Optional[str] = None,
    ) -> str:
        """
            Download a PixelDrain file.

            Parameters
            ----------
            url : str
                PixelDrain file URL (e.g., https://pixeldrain.com/u/XXXXXX).
            dest_dir : str, optional
                Destination directory (defaults to current working directory).
            timeout : int
                Network timeout in seconds for metadata and HTTP fallback.
            filename : str, optional
                Force output filename; otherwise metadata name is used.

            Returns
            -------
            str
                Absolute path to the downloaded file.

            Raises
            ------
            ValidationError, NetworkError, ProcessError, DownloadError
        """
        file_id: str = self._extract_file_id(url)
        # Metadata
        info: Dict[str, Any] = self.probe(url, timeout=timeout)
        name: str = filename or info.get("name") or f"{file_id}.bin"
        dest_dir_resolved: str = dest_dir or os.getcwd()
        os.makedirs(dest_dir_resolved, exist_ok=True)
        out_path: str = os.path.abspath(os.path.join(dest_dir_resolved, name))

        # Prefer external wget if available when progress is not requested
        wget_path: Optional[str] = shutil.which("wget")
        if wget_path and progress is None and progress_line is None:
            cmd: List[str] = [
                wget_path,
                "-q",
                self._download_url(file_id),
                "-O",
                name,
            ]
            try:
                proc: subprocess.Popen[str] = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=dest_dir_resolved,
                )
            except OSError as exc:
                raise ProcessError(f"Failed to start wget: {exc}") from exc
            # Do not emit progress; wait for completion
            proc.communicate()
            ret: int = proc.returncode if proc.returncode is not None else 0
            if ret != 0:
                raise ProcessError(f"wget returned non-zero exit code: {ret}")
            if not os.path.exists(out_path):
                raise DownloadError("File not found after wget completion")
            return out_path

        # Python streaming with requests (supports progress callback)
        try:
            with requests.get(self._download_url(file_id), stream=True, timeout=timeout) as r:
                r.raise_for_status()
                chunk: int = 1024 * 64
                total: Optional[int] = None
                try:
                    total = int(r.headers.get("Content-Length", "0")) or None
                except Exception:
                    total = None
                downloaded = 0
                # announce start
                self._emit(
                    progress,
                    progress_line,
                    source="pixeldrain",
                    stage="starting",
                    filename=name,
                    total=total,
                    downloaded=0,
                    percent=(0.0 if total else None),
                )
                import time as _time
                start_ts = _time.time()
                last_ts = start_ts
                last_bytes = 0
                with open(out_path, "wb") as f:
                    file_handle: IO[bytes] = f
                    for data in r.iter_content(chunk_size=chunk):
                        if not data:
                            continue
                        file_handle.write(data)
                        downloaded += len(data)
                        now = _time.time()
                        dt = max(now - last_ts, 1e-6)
                        inst_speed = (downloaded - last_bytes) / dt
                        pct = (downloaded * 100.0 / total) if total else None
                        eta = None
                        if total and inst_speed > 1e-3:
                            remain = max(0, total - downloaded)
                            eta = int(remain / inst_speed)
                        self._emit(
                            progress,
                            progress_line,
                            source="pixeldrain",
                            stage="downloading",
                            filename=name,
                            total=total,
                            downloaded=downloaded,
                            percent=pct,
                            speed=inst_speed,
                            eta=eta,
                        )
                        last_ts = now
                        last_bytes = downloaded
                self._emit(
                    progress,
                    progress_line,
                    source="pixeldrain",
                    stage="done",
                    filename=name,
                    total=total,
                    downloaded=downloaded,
                    percent=(100.0 if total else None),
                    speed=0.0,
                    eta=0,
                )
        except requests.RequestException as exc:  # pragma: no cover - network
            raise NetworkError(f"Failed during HTTP download: {exc}") from exc

        if not os.path.exists(out_path):
            raise DownloadError("File not found after HTTP download")
        return out_path

    # ---------- helpers ----------
    def _emit(
        self,
        progress: Optional["ProgressCallback"],
        progress_line: Optional[Callable[[str], None]],
        *,
        source: str,
        stage: str,
        filename: Optional[str],
        total: Optional[int],
        downloaded: Optional[int],
        percent: Optional[float],
        speed: Optional[float] = None,
        eta: Optional[float] = None,
    ) -> None:
        if progress is not None:
            try:
                from utils.progress import ProgressUpdate  # type: ignore
                progress(ProgressUpdate(
                    source=source,
                    stage=stage,
                    filename=filename,
                    total_bytes=total,
                    downloaded_bytes=(downloaded or 0),
                    percent=percent,
                    speed_bps=speed,
                    eta_sec=eta,
                ))
            except Exception:
                pass
        if progress_line is not None:
            try:
                line = _format_progress_line(
                    source=source,
                    filename=filename,
                    percent=percent,
                    downloaded=downloaded,
                    total=total,
                    speed=speed,
                    eta=eta,
                )
                progress_line(line)
            except Exception:
                pass


def _format_progress_line(
    *,
    source: str,
    filename: Optional[str],
    percent: Optional[float],
    downloaded: Optional[int],
    total: Optional[int],
    speed: Optional[float],
    eta: Optional[float],
) -> str:
    W_NAME, W_PCT, W_BYTES, W_SPEED, W_ETA = 50, 7, 23, 10, 12

    def fmt_bytes(b: Optional[int]) -> str:
        if b is None:
            return "0B"
        units = ["B", "KB", "MB", "GB", "TB"]
        v = float(b or 0)
        i = 0
        while v >= 1024.0 and i < len(units) - 1:
            v /= 1024.0
            i += 1
        return (f"{int(v)}{units[i]}" if i == 0 else f"{v:.1f}{units[i]}")

    def pad(s: str, w: int) -> str:
        return s + (" " * max(0, w - len(s)))

    def fit(s: str, w: int) -> str:
        if not s:
            return "".ljust(w)
        if len(s) <= w:
            return s.ljust(w)
        ell = "…"
        keep = w - len(ell)
        return (ell + s[-keep:]) if keep > 0 else s[-w:]
    cols: List[str] = [f"[{source}]"]
    if filename:
        cols.append(fit(filename, W_NAME))
    if percent is not None:
        ptxt = "100.0%" if percent >= 100.0 else f"{percent:5.1f}%"
        cols.append(pad(ptxt, W_PCT))
    if (downloaded or 0) > 0 or (total is not None):
        left = fmt_bytes(
            (total if (percent and percent >= 100.0 and total) else downloaded) or 0)
        if total:
            right = fmt_bytes(total)
            cols.append(pad(f"{left} / {right}", W_BYTES))
        else:
            cols.append(pad(left, W_BYTES))
    if speed is not None:
        v = float(speed)
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        i = 0
        while v >= 1024.0 and i < len(units) - 1:
            v /= 1024.0
            i += 1
        cols.append(pad(f"{v:4.1f}{units[i]}", W_SPEED))
    if eta is not None:
        s = int(max(0, eta))
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        cols.append(pad(f"ETA {h:02d}:{m:02d}:{s:02d}", W_ETA))
    return " | ".join(c for c in cols if c)
