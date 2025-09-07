"""
    Short description
    -----------------
    HTTP-based Mega.nz downloader with AES-CTR decryption for public links
    (no mega.py and no megatools). Supports single files and public folders.

    Usage
    -----
    from utils.mega import MegaDownloader

    mega = MegaDownloader()
    path = mega.download_file("https://mega.nz/file/<ID>#<KEY>", dest_dir="./tmp")

    Example
    -------
    from utils.mega import MegaDownloader
    from utils.progress import ConsoleProgress

    mega = MegaDownloader()
    cb = ConsoleProgress()  # structured callback
    path = mega.download("https://mega.nz/folder/<ID>#<KEY>", dest_dir="./out", progress=cb)

    Notes
    -----
    - Library code does not print; it raises typed exceptions.
    - Optional `progress_line: Callable[[str], None]` is provided for simple string
        progress callbacks (UI helpers can print such strings). Structured callback
        via `utils.progress.ProgressUpdate` is also supported for backward
        compatibility with the unified renderer.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple, Union

import requests
from Crypto.Cipher import AES

__all__ = [
    "BaseError",
    "NetworkError",
    "ValidationError",
    "ProcessError",
    "MegaError",  # backward-compat alias
    "MegaNetworkError",  # backward-compat alias
    "MegaValidationError",  # backward-compat alias
    "MegaDownloader",
]


# -----------------
# Exceptions
# -----------------


class BaseError(Exception):
    """
        Base error for Mega downloader.
    """


class NetworkError(BaseError):
    """
        Network-level failures (HTTP/API).
    """


class ValidationError(BaseError):
    """
        Invalid input (bad URLs/keys) or unexpected API payload.
    """


class ProcessError(BaseError):
    """
        Reserved for process-related failures (kept for compatibility).
    """


# Backward-compatible aliases
MegaError = ValidationError
MegaNetworkError = NetworkError
MegaValidationError = ValidationError


# -----------------
# Utils: MEGA b64 and keys
# -----------------


def _mega_b64_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    pad = (4 - len(s) % 4) % 4
    s = s + ("=" * pad)
    return base64.b64decode(s)


def _bytes_to_a32(b: bytes) -> List[int]:
    # Big-endian 32-bit words
    if len(b) % 4:
        b += b"\0" * (4 - (len(b) % 4))
    return [int.from_bytes(b[i : i + 4], "big") for i in range(0, len(b), 4)]


def _a32_to_bytes(a: List[int]) -> bytes:
    return b"".join(x.to_bytes(4, "big") for x in a)


def _xor_a32(a: List[int], b: List[int]) -> List[int]:
    return [x ^ y for x, y in zip(a, b)]


def _derive_key_iv_from_k(k_a32: List[int]) -> Tuple[bytes, bytes]:
    # Mega: 4 words => 128-bit key, zero IV. >=8 words => key=a^b, iv=a[4:6]
    if len(k_a32) == 4:
        key = _a32_to_bytes(k_a32)
        iv = b"\0" * 8
        return key, iv
    if len(k_a32) >= 8:
        key_a = k_a32[:4]
        key_b = k_a32[4:8]
        key = _a32_to_bytes(_xor_a32(key_a, key_b))
    iv = _a32_to_bytes(k_a32[4:6])  # 8 bytes
    return key, iv
    raise ValidationError("Invalid MEGA file key")


def _decrypt_attrs(attr_b64: str, key_bytes: bytes) -> Dict[str, Union[str, int]]:
    # Attributes are AES-CBC(IV=zero)-encrypted and prefixed with 'MEGA'
    data = _mega_b64_decode(attr_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv=b"\0" * 16)
    dec = cipher.decrypt(data)
    if not dec.startswith(b"MEGA"):
        return {}
    dec = dec[4:]  # strip 'MEGA'
    dec = dec.rstrip(b"\0")
    try:
        return json.loads(dec.decode("utf-8"))
    except Exception:
        return {}


def _api_call(
    payload: List[Dict[str, Union[str, int]]],
    query_params: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Union[str, int, float]]]:
    url = "https://g.api.mega.co.nz/cs"
    params = {"id": str(int(time.time() * 1000))}
    if query_params:
        params.update(query_params)
    try:
        r = requests.post(url, params=params, json=payload, timeout=30)
    except requests.RequestException as e:
        raise NetworkError(str(e))
    if r.status_code != 200:
        raise NetworkError(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception as e:
        raise NetworkError(f"Bad JSON: {e}")
    if isinstance(data, list):
        return data  # typical response
    # some API variants return a single object
    return [data]


def _parse_file_link(url: str) -> Tuple[str, List[int]]:
    # https://mega.nz/file/<id>#<key> (also supports legacy format)
    m = re.search(r"mega\.nz/(?:#?!)?file/([a-zA-Z0-9_-]{8})#([a-zA-Z0-9_-]{16,})", url)
    if not m:
        m = re.search(r"mega\.nz/#?!([a-zA-Z0-9_-]{8})!([a-zA-Z0-9_-]{16,})", url)
    if not m:
        raise ValidationError("Invalid MEGA file URL")
    file_id = m.group(1)
    key_b = _mega_b64_decode(m.group(2))
    k_a32 = _bytes_to_a32(key_b)
    return file_id, k_a32


def _parse_folder_link(url: str) -> Tuple[str, List[int]]:
    m = re.search(r"mega\.nz/(?:#?!)?folder/([a-zA-Z0-9_-]{8})#([a-zA-Z0-9_-]{16,})", url)
    if not m:
        m = re.search(r"mega\.nz/#?!F!([a-zA-Z0-9_-]{8})!([a-zA-Z0-9_-]{16,})", url)
    if not m:
        raise ValidationError("Invalid MEGA folder URL")
    folder_id = m.group(1)
    key_b = _mega_b64_decode(m.group(2))
    k_a32 = _bytes_to_a32(key_b)
    # For 256-bit links, XOR halves to derive 128-bit key
    if len(k_a32) >= 8:
        k_a32 = _xor_a32(k_a32[:4], k_a32[4:8])
    return folder_id, k_a32


# -----------------
# Downloader
# -----------------


if TYPE_CHECKING:
    from utils.progress import ProgressCallback, ProgressUpdate


@dataclass
class _Speed:
    start_ts: float
    last_ts: float
    downloaded: int


class MegaDownloader:
    """
        HTTP + AES-CTR downloader for public MEGA links (files and folders).

        Parameters
        ----------
        None

        Notes
        -----
        - Progress can be reported via a structured callback (`progress`) or via
            a simple string callback (`progress_line`). Only UI should print.
    """

    def __init__(self) -> None:
        pass

    # ---------- file ----------
    def download_file(
        self,
        file_url: str,
        dest_dir: Optional[Union[str, Path]] = None,
        progress: Optional["ProgressCallback"] = None,
        progress_line: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        if not isinstance(file_url, str) or "mega.nz" not in file_url:
            raise ValidationError("Invalid MEGA URL")

        file_id, k_a32 = _parse_file_link(file_url)
        key_bytes, iv8 = _derive_key_iv_from_k(k_a32)

        # Get file metadata and direct URL
        resp = _api_call([{"a": "g", "g": 1, "p": file_id}])[0]
        if "g" not in resp or "s" not in resp:
            raise ValidationError("MEGA API didn't return direct link or size")
        g_url: str = str(resp["g"])  # direct link
        size: int = int(resp["s"])   # bytes

        # Derive filename from attributes
        name = "file"
        if "at" in resp:
            attrs = _decrypt_attrs(str(resp["at"]), key_bytes)
            name = str(attrs.get("n") or name)

        out_dir = Path(dest_dir or ".")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / name

        self._emit(
            progress,
            progress_line,
            source="mega",
            stage="starting",
            filename=name,
            total=size,
            downloaded=0,
            percent=0.0,
        )

        # Stream download + AES-CTR decrypt (nonce=IV, counter starts at 0)
        cipher = AES.new(key_bytes, AES.MODE_CTR, nonce=iv8, initial_value=0)
        downloaded = 0
        sp = _Speed(start_ts=time.time(), last_ts=time.time(), downloaded=0)

        try:
            with requests.get(g_url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        dec = cipher.decrypt(chunk)
                        f.write(dec)
                        downloaded += len(dec)
                        # progress
                        now = time.time()
                        dt = max(now - sp.last_ts, 1e-6)
                        speed = (downloaded - sp.downloaded) / dt
                        eta = None
                        pct = (downloaded / size * 100.0) if size else None
                        if size > 0 and speed > 1e-3:
                            remain = size - downloaded
                            eta = int(remain / speed)
                        self._emit(progress, progress_line, source="mega", stage="downloading",
                                   filename=name, total=size, downloaded=downloaded,
                                   percent=pct, speed=speed, eta=eta)
                        sp.last_ts = now
                        sp.downloaded = downloaded
        except requests.RequestException as e:
            raise NetworkError(str(e))

        self._emit(
            progress,
            progress_line,
            source="mega",
            stage="done",
            filename=name,
            total=size,
            downloaded=size,
            percent=100.0,
            speed=0.0,
            eta=0,
        )

        return str(out_path.resolve())

    # ---------- folder ----------
    def download_folder(
        self,
        folder_url: str,
        dest_dir: Optional[Union[str, Path]] = None,
        choose_files: bool = False,
    progress: Optional["ProgressCallback"] = None,
    progress_line: Optional[Callable[[str], None]] = None,
    ) -> List[str]:
        if not isinstance(folder_url, str) or "mega.nz" not in folder_url:
            raise ValidationError("Invalid MEGA URL")

        folder_id, shared_k_a32 = _parse_folder_link(folder_url)
        shared_key_bytes = _a32_to_bytes(shared_k_a32)

        folder_ids_in_url = re.findall(r"/folder/([a-zA-Z0-9_-]{8})", folder_url)
        target_subfolder: Optional[str] = None
        if len(folder_ids_in_url) > 1:
            target_subfolder = folder_ids_in_url[-1]

        # List folder nodes (public folder requires `n` in query params)
        resp = _api_call([{"a": "f", "c": 1, "r": 1, "ca": 1}], query_params={"n": folder_id})[0]
        nodes = resp.get("f") if isinstance(resp, dict) else None
        if not isinstance(nodes, list):
            raise ValidationError("Failed to retrieve MEGA folder listing")

        # Build maps and decrypt attributes for names
        by_h: Dict[str, Dict] = {}
        for n in nodes:
            if not isinstance(n, dict):
                continue
            by_h[n.get("h")] = n

        def node_key_bytes(n: Dict) -> Optional[bytes]:
            k = n.get("k")
            if not isinstance(k, str):
                return None
            try:
                b64 = k.split(":")[-1]
                enc = _mega_b64_decode(b64)
                dec = AES.new(shared_key_bytes, AES.MODE_ECB).decrypt(enc)
                if len(dec) == 32:
                    return bytes(x ^ y for x, y in zip(dec[:16], dec[16:32]))
                return dec[:16]
            except Exception:
                return None

        # Decrypt names for folders (t==1) and files (t==0)
        names: Dict[str, str] = {}
        for n in nodes:
            if not isinstance(n, dict):
                continue
            h = n.get("h")
            if not isinstance(h, str):
                continue
            kb = node_key_bytes(n)
            if kb and isinstance(n.get("a"), str):
                attrs = _decrypt_attrs(n["a"], kb)
                nm = attrs.get("n") if isinstance(attrs, dict) else None
                if isinstance(nm, str) and nm:
                    names[h] = nm

        def full_path(h: str) -> Path:
            parts: List[str] = []
            cur = by_h.get(h)
            while cur and cur.get("p") in by_h:
                p = cur.get("p")
                cur = by_h.get(p)
                if cur and cur.get("t") == 1:
                    hid = cur.get("h")
                    nm = names.get(hid, "")
                    if nm:
                        parts.append(nm)
                    if target_subfolder and hid == target_subfolder:
                        break
            return Path(*reversed(parts)) if parts else Path("")

        allowed: Optional[set] = None
        if target_subfolder and target_subfolder in by_h:
            children_map: Dict[str, List[str]] = {}
            for n in nodes:
                if isinstance(n, dict) and isinstance(n.get("h"), str) and isinstance(n.get("p"), str):
                    children_map.setdefault(n.get("p"), []).append(n.get("h"))
            stack = [target_subfolder]
            allowed = set()
            while stack:
                cur = stack.pop()
                if cur in allowed:
                    continue
                allowed.add(cur)
                for ch in children_map.get(cur, []):
                    stack.append(ch)

        out_dir = Path(dest_dir or ".")
        out_dir.mkdir(parents=True, exist_ok=True)

        results: List[str] = []
        item_idx = 0
        for n in nodes:
            if not isinstance(n, dict) or n.get("t") != 0:
                continue
            h = n.get("h")
            if not isinstance(h, str):
                continue
            if allowed is not None and h not in allowed:
                continue
            kb = node_key_bytes(n)
            if not kb:
                continue
            k_a32 = _bytes_to_a32(kb)
            key_bytes, iv8 = _derive_key_iv_from_k(k_a32)
            name = names.get(h, h)
            rel = full_path(h) / name
            if target_subfolder and target_subfolder in by_h:
                # ensure rel starts at target subfolder (strip any parents above it)
                parts = list(rel.parts)
                if parts:
                    target_name = names.get(target_subfolder, "")
                    if target_name and target_name in parts:
                        idx = parts.index(target_name)
                        rel = Path(*parts[idx:])
            (out_dir / rel.parent).mkdir(parents=True, exist_ok=True)

            gi = _api_call([{"a": "g", "g": 1, "n": h}], query_params={"n": folder_id})[0]
            if "g" not in gi or "s" not in gi:
                continue
            g_url: str = str(gi["g"])  # type: ignore[index]
            size: int = int(gi["s"])   # type: ignore[index]

            self._emit(progress, progress_line, source="mega", stage="starting",
                       filename=str(rel), total=size, downloaded=0, percent=0.0,
                       item_idx=item_idx + 1)

            cipher = AES.new(key_bytes, AES.MODE_CTR, nonce=iv8, initial_value=0)
            downloaded = 0
            sp = _Speed(start_ts=time.time(), last_ts=time.time(), downloaded=0)

            try:
                with requests.get(g_url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    with open(out_dir / rel, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            dec = cipher.decrypt(chunk)
                            f.write(dec)
                            downloaded += len(dec)
                            now = time.time()
                            dt = max(now - sp.last_ts, 1e-6)
                            speed = (downloaded - sp.downloaded) / dt
                            eta = None
                            pct = (downloaded / size * 100.0) if size else None
                            if size > 0 and speed > 1e-3:
                                remain = size - downloaded
                                eta = int(remain / speed)
                            self._emit(progress, progress_line, source="mega", stage="downloading",
                                       filename=str(rel), total=size, downloaded=downloaded,
                                       percent=pct, speed=speed, eta=eta, item_idx=item_idx + 1)
                            sp.last_ts = now
                            sp.downloaded = downloaded
            except requests.RequestException as e:
                raise NetworkError(str(e))

            self._emit(progress, progress_line, source="mega", stage="done",
                       filename=str(rel), total=size, downloaded=size,
                       percent=100.0, speed=0.0, eta=0, item_idx=item_idx + 1)
            results.append(str((out_dir / rel).resolve()))
            item_idx += 1

        self._emit(
            progress,
            progress_line,
            source="mega",
            stage="done",
            filename=None,
            total=None,
            downloaded=None,
            percent=None,
            speed=None,
            eta=None,
            message=f"{len(results)} files",
        )

        return results

    # ---------- autodetect ----------
    def download(
        self,
        url: str,
        dest_dir: Optional[Union[str, Path]] = None,
        choose_files: bool = False,
        progress: Optional["ProgressCallback"] = None,
        progress_line: Optional[Callable[[str], None]] = None,
    ) -> Union[Optional[str], List[str]]:
        if not isinstance(url, str) or "mega.nz" not in url:
            raise ValidationError("Invalid MEGA URL")
        if "/folder/" in url or re.search(r"#!F!", url):
            return self.download_folder(url, dest_dir=dest_dir, choose_files=choose_files, progress=progress, progress_line=progress_line)
        return self.download_file(url, dest_dir=dest_dir, progress=progress, progress_line=progress_line)

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
        item_idx: Optional[int] = None,
        item_count: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        # structured callback
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
                    item_idx=item_idx,
                    item_count=item_count,
                    message=message,
                ))
            except Exception:
                pass
        # plain line callback
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
        if percent >= 100.0:
            ptxt = "100.0%"
        else:
            ptxt = f"{percent:5.1f}%"
        cols.append(pad(ptxt, W_PCT))
    # bytes
    if (downloaded or 0) > 0 or (total is not None):
        left = fmt_bytes((total if (percent and percent >= 100.0 and total) else downloaded) or 0)
        if total:
            right = fmt_bytes(total)
            cols.append(pad(f"{left} / {right}", W_BYTES))
        else:
            cols.append(pad(left, W_BYTES))
    if speed is not None:
        # width-stable speed
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
