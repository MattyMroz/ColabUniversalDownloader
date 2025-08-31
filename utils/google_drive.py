"""
	Short description
	-----------------
	Google Drive helper built for Google Colab usage. Provides a thin wrapper to upload files,
	make them publicly accessible, and schedule deletions, adhering to the repository rules:
	no prints in library code (use exceptions), English docs, and types.

	Usage
	-----
    from utils.google_drive import GoogleDriveManager, GoogleDriveError

    gd = GoogleDriveManager()

	try:
		info = gd.upload_and_share("/path/to/file", parent_id="root", skip_if_exists=True)
		print(info)
	except GoogleDriveError as e:
		print(f"Drive error: {e}")

	Example
	-------
	- Deleting with delay from a notebook cell:
		GoogleDriveManager().delete_file_after_delay(file_id="...", delay_seconds=60)

	Notes
	-----
	- This module assumes Google Colab environment for authentication.
	- UI printing is not performed by the library; use exceptions for errors.
"""

from __future__ import annotations

import importlib
import os
import threading
import time
from contextlib import contextmanager, suppress
from typing import Any, Callable, Dict, List, Optional

__all__: List[str] = [
    "GoogleDriveManager",
    "GoogleDriveBaseError",
    "GoogleDriveAuthError",
    "GoogleDriveApiError",
    "GoogleDriveValidationError",
    "GoogleDriveError",
]


class GoogleDriveBaseError(Exception):
    """
        Base error for Google Drive operations.

        Notes
        -----
        Subclasses: GoogleDriveAuthError, GoogleDriveApiError, GoogleDriveValidationError.
    """


class GoogleDriveAuthError(GoogleDriveBaseError):
    """
        Authentication/availability error (e.g., not running in Colab or auth failed).
    """


class GoogleDriveApiError(GoogleDriveBaseError):
    """
        API error raised when Google Drive API requests fail.
    """


class GoogleDriveValidationError(GoogleDriveBaseError):
    """
        Validation error (e.g., invalid arguments).
    """


class GoogleDriveError(GoogleDriveApiError):
    """
        Backward-compatible alias for API errors.
    """


class GoogleDriveManager:
    """
        High-level manager for Google Drive operations in Google Colab.

        Notes
        -----
        - The service is initialized on construction in Colab. If unavailable, operations raise
            GoogleDriveAuthError.
    """

    def __init__(self) -> None:
        self.drive_service: Optional[Any] = None
        self._MediaFileUpload: Optional[Any] = None
        try:
            auth_mod = importlib.import_module("google.colab.auth")
            discovery = importlib.import_module("googleapiclient.discovery")
            http_mod = importlib.import_module("googleapiclient.http")
            # Authenticate user in Colab
            auth_mod.authenticate_user()
            self.drive_service = discovery.build("drive", "v3")
            self._MediaFileUpload = getattr(http_mod, "MediaFileUpload")
        except Exception as ex:  # pragma: no cover
            # Leave drive_service as None; operations will raise via _require_ready
            raise GoogleDriveAuthError(
                f"Google Drive API not available or authentication failed: {ex}"
            ) from ex

    def is_ready(self) -> bool:
        """
            Check whether the Google Drive API service is ready to use.

            Returns
            -------
            bool
                True if the service is available.
        """
        return self.drive_service is not None

    @contextmanager
    def _suppress_exc(self) -> Any:
        """
            Context manager that suppresses all exceptions (used in cleanup loops).
        """
        try:
            yield
        except Exception:
            return None

    def _require_ready(self) -> None:
        if not self.is_ready():
            raise GoogleDriveAuthError(
                "Google Drive API is not available (not running in Colab or auth failed)."
            )

    def get_drive_id(self, drive_name: str, is_shared: bool) -> Optional[str]:
        """
            Retrieve Drive ID (Shared Drive or 'root' for My Drive).

            Parameters
            ----------
            drive_name : str
                Shared Drive name (ignored for My Drive).
            is_shared : bool
                Whether to look for a Shared Drive.


            Returns
            -------
            Optional[str]
                Drive ID or None when not found.
        """
        self._require_ready()
        if not is_shared:
            return "root"
        try:
            drives: Dict[str, Any] = self.drive_service.drives(
            ).list().execute()  # type: ignore[union-attr]
            for d in drives.get("drives", []):
                if d.get("name") == drive_name:
                    return d.get("id")
            return None
        except Exception as ex:
            raise GoogleDriveApiError(
                f"Failed to list shared drives: {ex}") from ex

    def upload_and_share(
        self,
        local_filepath: str,
        parent_id: str,
        *,
        skip_if_exists: bool = True,
        replace_if_exists: bool = False,
        progress: Optional[Callable[[Any], None]] = None,
        progress_line: Optional[Callable[[str], None]] = None,
    ) -> Optional[Dict[str, str]]:
        """
            Upload a file to Google Drive, set public permissions, and return a link.

            Parameters
            ----------
            local_filepath : str
                Path to a local file to upload.
            parent_id : str
                Target folder/drive ID on Google Drive.
            skip_if_exists : bool
                If a file with the same name exists in the folder, skip the upload and return its link.
            replace_if_exists : bool
                If True and a file with the same name exists, delete it then upload anew.
            progress : Optional[ProgressCallback]
                Structured progress callback receiving ProgressUpdate.
            progress_line : Optional[Callable[[str], None]]
                Optional callback receiving a preformatted single-line progress string.

            Returns
            -------
            Optional[Dict[str, str]]
                Dictionary with link and file ID, or None when not found/created.

            Raises
            ------
            GoogleDriveValidationError
                When inputs are invalid.
            GoogleDriveApiError
                When API calls fail.
        """
        self._require_ready()
        if not local_filepath or not isinstance(local_filepath, str):
            raise GoogleDriveValidationError(
                "local_filepath must be a non-empty string")
        if not parent_id or not isinstance(parent_id, str):
            raise GoogleDriveValidationError(
                "parent_id must be a non-empty string")
        succeeded: bool = False
        try:
            filename: str = os.path.basename(local_filepath)
            existing: Optional[Dict[str, str]] = None
            with suppress(Exception):
                existing = self._find_file_in_folder_by_name(
                    parent_id, filename)

            if existing and skip_if_exists and not replace_if_exists:
                file_id: Optional[str] = existing.get("id")
                # Ensure public permission
                try:
                    self.drive_service.permissions().create(  # type: ignore[union-attr]
                        fileId=file_id,
                        body={"role": "reader", "type": "anyone"},
                        supportsAllDrives=True,
                    ).execute()
                except Exception:
                    pass
                updated_file: Dict[str, Any] = self.drive_service.files().get(  # type: ignore[union-attr]
                    fileId=file_id,
                    fields="webContentLink",
                    supportsAllDrives=True,
                ).execute()
                public_link: Optional[str] = updated_file.get("webContentLink")
                return {"link": public_link, "id": file_id or ""}

            if existing and replace_if_exists:
                try:
                    self.drive_service.files().delete(  # type: ignore[union-attr]
                        fileId=existing.get("id"), supportsAllDrives=True
                    ).execute()
                except Exception:
                    pass

            file_metadata: Dict[str, Any] = {
                "name": filename, "parents": [parent_id]}
            if not self._MediaFileUpload:
                raise GoogleDriveAuthError(
                    "Google Drive client not initialized")
            # Use explicit chunk size to ensure incremental progress events (8 MB)
            media: Any = self._MediaFileUpload(
                local_filepath,
                chunksize=8 * 1024 * 1024,
                resumable=True,
            )

            request = self.drive_service.files().create(  # type: ignore[union-attr]
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            # Resumable upload loop
            uploaded_bytes: int = 0
            try:
                total_bytes: int = int(os.path.getsize(local_filepath))
            except Exception:
                total_bytes = 0
            created: Optional[Dict[str, Any]] = None
            # Initial progress event
            self._emit(
                progress=progress,
                progress_line=progress_line,
                source="gdrive",
                stage="starting",
                filename=filename,
                downloaded_bytes=0,
                total_bytes=total_bytes if total_bytes > 0 else None,
                speed_bps=None,
                eta_sec=None,
            )
            t0: float = time.time()
            last_t: float = t0
            last_b: int = 0
            while created is None:
                # type: ignore[attr-defined]
                status, created = request.next_chunk()
                if status is not None:
                    # Extract fraction 0..1; googleapiclient exposes progress() method
                    progress_fraction: Optional[float] = None
                    try:
                        prog = getattr(status, "progress", None)
                        if prog is not None:
                            progress_fraction = float(
                                prog()) if callable(prog) else float(prog)
                    except Exception:
                        progress_fraction = None
                    # Fallback: resumable_progress / total_size
                    if progress_fraction is None:
                        try:
                            rp = getattr(status, "resumable_progress", None)
                            ts = getattr(status, "total_size", None)
                            if rp is not None and ts:
                                progress_fraction = float(rp) / float(ts)
                        except Exception:
                            progress_fraction = None
                    if progress_fraction is not None and total_bytes > 0:
                        # ensure monotonic increase
                        uploaded_bytes = max(uploaded_bytes, int(
                            progress_fraction * total_bytes))
                # Emit progress at most ~20 Hz
                now: float = time.time()
                if (now - last_t) >= 0.05:
                    # Compute instantaneous speed over the last interval
                    dt: float = max(1e-6, now - last_t)
                    db: int = max(0, uploaded_bytes - last_b)
                    speed: Optional[float] = float(db) / dt if db > 0 else None
                    eta: Optional[float] = None
                    if total_bytes > 0 and (speed or 0) > 0:
                        remaining: int = max(0, total_bytes - uploaded_bytes)
                        # type: ignore[arg-type]
                        eta = float(remaining) / float(speed)
                    self._emit(
                        progress=progress,
                        progress_line=progress_line,
                        source="gdrive",
                        stage="uploading",
                        filename=filename,
                        downloaded_bytes=uploaded_bytes,
                        total_bytes=total_bytes if total_bytes > 0 else None,
                        speed_bps=speed,
                        eta_sec=eta,
                    )
                    last_t = now
                    last_b = uploaded_bytes
            created = created or {}

            file_id2: Optional[str] = created.get("id")
            if not file_id2:
                raise GoogleDriveApiError(
                    "Upload succeeded but file ID is missing")

            # Public sharing with retries
            for i in range(1, 4):
                try:
                    self.drive_service.permissions().create(  # type: ignore[union-attr]
                        fileId=file_id2,
                        body={"role": "reader", "type": "anyone"},
                        supportsAllDrives=True,
                    ).execute()
                    break
                except Exception as ex:
                    if i == 3:
                        pass
                    time.sleep(2)

            # Get link (fallback to webViewLink)
            public_link2: Optional[str] = None
            for i in range(1, 4):
                try:
                    updated_file2: Dict[str, Any] = self.drive_service.files().get(  # type: ignore[union-attr]
                        fileId=file_id2,
                        fields="webContentLink, webViewLink",
                        supportsAllDrives=True,
                    ).execute()
                    public_link2 = updated_file2.get("webContentLink")
                    if not public_link2:
                        fallback: Optional[str] = updated_file2.get(
                            "webViewLink")
                        if fallback:
                            public_link2 = fallback
                    break
                except Exception as ex:
                    if i == 3:
                        pass
                    time.sleep(2)

            succeeded = True
            return {"link": public_link2, "id": file_id2}

        except GoogleDriveBaseError:
            raise
        except Exception as ex:
            raise GoogleDriveApiError(
                f"Failed to upload/share file: {ex}") from ex
        finally:
            # Final 'done' progress only on success
            if succeeded:
                try:
                    self._emit(
                        progress=progress,
                        progress_line=progress_line,
                        source="gdrive",
                        stage="done",
                        filename=os.path.basename(
                            local_filepath) if local_filepath else None,
                        downloaded_bytes=total_bytes if 'total_bytes' in locals() and total_bytes > 0 else 0,
                        total_bytes=total_bytes if 'total_bytes' in locals() and total_bytes > 0 else None,
                        speed_bps=None,
                        eta_sec=0.0,
                    )
                except Exception:
                    pass

    def _find_file_in_folder_by_name(self, parent_id: str, filename: str) -> Optional[Dict[str, str]]:
        """
            Return the first file (non-recursive) in a folder by name.

            Parameters
            ----------
            parent_id : str
                Parent folder ID on Google Drive.
            filename : str
                Filename to look for.

            Returns
            -------
            Optional[Dict[str, str]]
                First matching file metadata or None.
        """
        self._require_ready()
        safe_name: str = filename.replace("'", "\\'")
        q: str = f"name = '{safe_name}' and '{parent_id}' in parents and trashed = false"
        resp: Dict[str, Any] = self.drive_service.files().list(  # type: ignore[union-attr]
            q=q,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=1,
        ).execute()
        items: List[Dict[str, str]] = resp.get("files", [])
        return items[0] if items else None

    def delete_file_after_delay(self, file_id: str, delay_seconds: int) -> None:
        """
            Delete a file from Google Drive after a given delay (in a background thread).

            Parameters
            ----------
            file_id : str
                ID of the file to delete.
            delay_seconds : int
                Delay in seconds before deletion.

        """
        self._require_ready()

        def task() -> None:
            time.sleep(delay_seconds)
            attempts: int = 3
            for i in range(1, attempts + 1):
                try:
                    self.drive_service.files().delete(
                        fileId=file_id, supportsAllDrives=True).execute()  # type: ignore[union-attr]
                    return
                except Exception as ex:
                    if i != attempts:
                        time.sleep(2)

        threading.Thread(target=task, daemon=True).start()

    def delete_folder_after_delay(self, folder_id: str, delay_seconds: int) -> None:
        """
            Permanently delete a folder from Google Drive after a given delay (background thread).

            Notes
            -----
            The folder may end up in the trash; Shared Drives require appropriate permissions.

            Parameters
            ----------
            folder_id : str
                Folder ID to delete.
            delay_seconds : int
                Delay in seconds before deletion.

        """
        self._require_ready()

        def task() -> None:
            time.sleep(delay_seconds)
            try:
                # Attempt to delete children first (multiple passes for consistency delays)
                for _ in range(3):
                    page_token: Optional[str] = None
                    any_deleted: bool = False
                    while True:
                        query: str = f"'{folder_id}' in parents and trashed = false"
                        resp: Dict[str, Any] = self.drive_service.files().list(  # type: ignore[union-attr]
                            q=query,
                            fields="nextPageToken, files(id)",
                            pageSize=1000,
                            supportsAllDrives=True,
                            includeItemsFromAllDrives=True,
                            pageToken=page_token,
                        ).execute()
                        items: List[Dict[str, str]] = resp.get("files", [])
                        for item in items:
                            fid: Optional[str] = item.get("id")
                            if not fid:
                                continue
                            with self._suppress_exc():
                                self.drive_service.files().delete(
                                    fileId=fid, supportsAllDrives=True).execute()  # type: ignore[union-attr]
                                any_deleted = True
                        page_token = resp.get("nextPageToken")
                        if not page_token:
                            break
                    if not any_deleted:
                        break
                    time.sleep(1)

                # Delete the folder with retries
                attempts: int = 3
                for i in range(1, attempts + 1):
                    try:
                        self.drive_service.files().delete(
                            fileId=folder_id, supportsAllDrives=True).execute()  # type: ignore[union-attr]
                        return
                    except Exception as ex:
                        if i != attempts:
                            time.sleep(2)
            except Exception as ex:
                pass

        threading.Thread(target=task, daemon=True).start()

    def delete_files_now(self, file_ids: List[str]) -> None:
        """
            Immediately delete a list of files.

            Parameters
            ----------
            file_ids : List[str]
                List of file IDs to delete.

        """
        self._require_ready()
        if not file_ids:
            return
        for file_id in file_ids:
            attempts: int = 3
            for i in range(1, attempts + 1):
                try:
                    self.drive_service.files().delete(
                        fileId=file_id, supportsAllDrives=True).execute()  # type: ignore[union-attr]
                    break
                except Exception as ex:
                    if i != attempts:
                        time.sleep(2)

    def delete_folder_now(self, folder_id: str) -> None:
        """
            Immediately delete a folder and its contents.

            Parameters
            ----------
            folder_id : str
                Folder ID to delete.

        """
        self._require_ready()
        if not folder_id:
            return
        try:
            page_token: Optional[str] = None
            while True:
                query: str = f"'{folder_id}' in parents and trashed = false"
                resp2: Dict[str, Any] = self.drive_service.files().list(  # type: ignore[union-attr]
                    q=query,
                    fields="nextPageToken, files(id)",
                    pageSize=1000,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=page_token,
                ).execute()
                for item in resp2.get("files", []):
                    fid2: Optional[str] = item.get("id")
                    if not fid2:
                        continue
                    with self._suppress_exc():
                        self.drive_service.files().delete(
                            fileId=fid2, supportsAllDrives=True).execute()  # type: ignore[union-attr]
                page_token = resp2.get("nextPageToken")
                if not page_token:
                    break
            attempts2: int = 3
            for i in range(1, attempts2 + 1):
                try:
                    self.drive_service.files().delete(
                        fileId=folder_id, supportsAllDrives=True).execute()  # type: ignore[union-attr]
                    return
                except Exception as ex:
                    if i != attempts2:
                        time.sleep(2)
        except Exception as ex:
            pass

    # --------------------------
    # Progress helpers
    # --------------------------
    def _emit(
        self,
        *,
        progress: Optional[Callable[[Any], None]],
        progress_line: Optional[Callable[[str], None]],
        source: str,
        stage: str,
        filename: Optional[str],
        downloaded_bytes: int,
        total_bytes: Optional[int],
        speed_bps: Optional[float],
        eta_sec: Optional[float],
        item_idx: Optional[int] = None,
        item_count: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        try:
            if progress is not None:
                pct: Optional[float] = None
                if total_bytes and total_bytes > 0:
                    pct = (float(downloaded_bytes) /
                           float(total_bytes)) * 100.0
                try:
                    from utils.progress import ProgressUpdate as _PU  # local import
                    u = _PU(
                        source=source,
                        stage=stage,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=total_bytes,
                        percent=pct,
                        speed_bps=speed_bps,
                        eta_sec=eta_sec,
                        filename=filename,
                        item_idx=item_idx,
                        item_count=item_count,
                        message=message,
                    )
                except Exception:
                    # Fallback: minimal shim object with attributes
                    class _Shim:
                        def __init__(self) -> None:
                            self.source = source
                            self.stage = stage
                            self.downloaded_bytes = downloaded_bytes
                            self.total_bytes = total_bytes
                            self.percent = pct
                            self.speed_bps = speed_bps
                            self.eta_sec = eta_sec
                            self.filename = filename
                            self.item_idx = item_idx
                            self.item_count = item_count
                            self.message = message

                    u = _Shim()  # type: ignore[assignment]
                progress(u)  # type: ignore[misc]
            if progress_line is not None:
                line = self._format_progress_line(
                    source=source,
                    filename=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    speed_bps=speed_bps,
                    eta_sec=eta_sec,
                    stage=stage,
                    item_idx=item_idx,
                    item_count=item_count,
                )
                progress_line(line)
        except Exception:
            # Do not break core flow on progress callback errors
            pass

    @staticmethod
    def _format_progress_line(
        *,
        source: str,
        filename: Optional[str],
        downloaded_bytes: int,
        total_bytes: Optional[int],
        speed_bps: Optional[float],
        eta_sec: Optional[float],
        stage: str,
        item_idx: Optional[int] = None,
        item_count: Optional[int] = None,
    ) -> str:
        # Basic wget-like columns; widths consistent with other modules
        from utils.progress import ConsoleProgress  # local import to reuse formatters
        cp = ConsoleProgress()
        # Build fields similarly to ConsoleProgress
        src = source or "?"
        if item_idx and item_count:
            src = f"{src} [{item_idx}/{item_count}]"
        parts: List[str] = [f"[{src}]"]
        if filename:
            parts.append(cp._fit(filename, 50))
        pct: Optional[float] = None
        if total_bytes and total_bytes > 0:
            pct = (float(downloaded_bytes) / float(total_bytes)) * 100.0
            if stage not in ("done", "error") and pct >= 100.0:
                pct = 99.9
        if stage in ("done", "error") and (total_bytes and total_bytes > 0):
            pct = 100.0
        if pct is not None:
            parts.append(cp._pad(f"{pct:5.1f}%", 7))
        show_bytes = (downloaded_bytes or 0) > 0 or (total_bytes is not None)
        if show_bytes:
            left_b = downloaded_bytes
            if stage in ("done", "error") and (total_bytes and total_bytes > 0):
                left_b = int(total_bytes)
            left = cp._fmt_bytes(left_b)
            if total_bytes and total_bytes > 0:
                right = cp._fmt_bytes(total_bytes)
                parts.append(cp._pad(f"{left} / {right}", 23))
            else:
                parts.append(cp._pad(left, 23))
        if speed_bps is not None:
            parts.append(cp._pad(cp._fmt_speed(speed_bps), 10))
        if eta_sec is not None:
            parts.append(cp._pad(cp._fmt_eta(eta_sec), 12))
        return " | ".join(parts)
