import contextlib
import logging
import platform
import re
import stat
from pathlib import Path
from subprocess import PIPE, Popen
from tempfile import gettempdir
from typing import Callable, List, Optional, Sequence, Tuple, Union

import requests

__all__ = ["Megatools", "MegaError", "MegaDownloader"]

logger = logging.getLogger(Path(__file__).stem)


class MegaError(Exception):
    """Wyjątek dla błędów zgłaszanych przez megatools."""

    def __init__(self, returncode: int, *args: object) -> None:
        self.returncode = returncode
        super().__init__(*args)


Stream = List[str]


def _to_string(*seq: Sequence[str]) -> Tuple[str, ...]:
    return tuple("".join(s) for s in seq)


def _parse_options(command: List[str], **options) -> None:
    """Zamienia opcje keyword na przełączniki CLI dla megatools.

    Przykłady:
    - no_progress=True        => --no-progress
    - path="/tmp"             => --path=/tmp
    - limit_speed=1024        => --limit-speed=1024
    """

    for option, value in options.items():
        option = option.replace("_", "-")
        if value is True:
            command.append(f"--{option}")
            continue
        if value is False or value is None:
            # pomiń flagi z False/None
            continue
        command.append(f"--{option}={value}")


def _execute(command: List[str], on_line: Optional[Callable[[str], None]] = None) -> Tuple[str, str, int]:
    """Uruchamia proces i zwraca (stdout, stderr, returncode).

    Jeśli podano on_line, woła go dla każdej nowej linii stdout.
    """

    process = Popen(command, stdout=PIPE, stderr=PIPE,
                    text=True, encoding="utf-8", errors="ignore")
    out_lines: Stream = []
    err_lines: Stream = []
    # Czytaj stdout
    for line in iter(process.stdout.readline, ""):
        out_lines.append(line)
        if on_line:
            with contextlib.suppress(Exception):
                on_line(line)
    # Czytaj stderr
    for line in iter(process.stderr.readline, ""):
        err_lines.append(line)
    return ("".join(out_lines), "".join(err_lines), process.wait())


def _default_progress(line: str) -> None:
    print(line, end="")


def _parse_and_raise(returncode: int, error: str) -> None:
    pattern = re.compile(r"\w+: ")
    match = pattern.search(error)
    if match:
        error = error.replace(match.group(0), "", 1)
    raise MegaError(returncode, f"[returnCode {returncode}] {error.strip()}")


class Megatools:
    """Lekki wrapper wokół binarki 'megatools'."""

    def __init__(self, executable: Optional[Union[Path, str]] = None) -> None:
        self.tmp_directory = Path(gettempdir())
        if not executable:
            is_windows = platform.system() == "Windows"
            executable = self.tmp_directory / \
                ("megatools.exe" if is_windows else "megatools")
            if not Path(executable).exists():
                logger.info("Pobieranie binarki megatools...")
                url = "https://raw.githubusercontent.com/justaprudev/megatools/master/megatools"
                binary = requests.get(f"{url}.exe" if is_windows else url)
                with open(executable, "wb") as f:
                    f.write(binary.content)
                # Ustaw prawa wykonywania (na systemach uniksowych)
                with contextlib.suppress(Exception):
                    Path(executable).chmod(
                        Path(executable).stat(
                        ).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                    )
        self.executable = str(executable)

    def download(
        self,
        url: str,
        progress: Optional[Callable[[str], None]] = _default_progress,
        **options,
    ) -> Tuple[str, int]:
        """Pobiera plik lub folder z Mega.nz.

        Kluczowe opcje:
        - path=PATH             (katalog docelowy)
        - choose_files=True     (interaktywny wybór plików z folderu)
        - no_progress=True      (wyłącza pasek postępu megatools)

        Zwraca: (stdout_text, returncode). W przypadku błędu – MegaError.
        """

        command = [self.executable, "dl", url, "--no-ask-password"]
        _parse_options(command, **options)
        logger.info(f"Uruchamiam: {command}")
        stdout, stderr, returncode = _execute(command, progress)
        if stderr and returncode != 0:
            _parse_and_raise(returncode, stderr)
        return stdout, returncode

    @property
    def version(self) -> str:
        stdout, _ = self.download("", progress=None, version=True)
        return stdout.split()[1]

    def filename(self, url: str) -> Optional[str]:
        def _stop_after_first(line: str) -> None:
            # Nie mamy uchwytu do procesu tutaj, więc polegamy na szybkim trybie (limit_speed + tmp path)
            # Po prostu zbieramy stdout i później go sparsujemy.
            return

        stdout, _ = self.download(
            url,
            progress=_stop_after_first,
            print_names=True,
            limit_speed=1,
            path=str(self.tmp_directory),
        )
        parts = stdout.split(":", 1)
        return parts[0].strip() if parts else None


# ------------------------
# Wyższy poziom: proste API
# ------------------------


ProgressFn = Optional[Callable[[str], None]]


class MegaDownloader:
    """Pomocnik do pobierania z Mega.nz (plików i folderów)."""

    def __init__(self, executable: Optional[Union[str, Path]] = None) -> None:
        self._mega = Megatools(executable)

    def download_file(
        self,
        file_url: str,
        dest_dir: Optional[Union[str, Path]] = None,
        progress: ProgressFn = None,
    ) -> Optional[str]:
        """Pobiera pojedynczy plik z Mega.nz i zwraca jego ścieżkę."""

        opts: dict = {}
        if dest_dir:
            opts["path"] = str(dest_dir)

        def _progress_adapter(line: str) -> None:
            if progress:
                progress(line)

        try:
            stdout, _ = self._mega.download(
                file_url,
                progress=_progress_adapter if progress else _default_progress,
                **opts,
            )
            # Spróbuj wyciągnąć nazwę pobranego pliku z logów
            filename: Optional[str] = None
            for line in stdout.splitlines()[::-1]:
                m = re.search(r"Downloaded (.+)$", line.strip())
                if m:
                    filename = m.group(1)
                    break
            if not filename:
                filename = self._mega.filename(file_url)
            if filename:
                return str(Path(dest_dir or ".").joinpath(filename).resolve())
            return None
        except MegaError as e:
            if progress:
                progress(f"BŁĄD: {e}")
            return None

    def download_folder(
        self,
        folder_url: str,
        dest_dir: Optional[Union[str, Path]] = None,
        choose_files: bool = False,
        progress: ProgressFn = None,
    ) -> List[str]:
        """Pobiera folder z Mega.nz; zwraca listę ścieżek pobranych plików (heurystyka)."""

        opts: dict = {"choose_files": True} if choose_files else {}
        if dest_dir:
            opts["path"] = str(dest_dir)

        def _progress_adapter(line: str) -> None:
            if progress:
                progress(line)

        try:
            stdout, _ = self._mega.download(
                folder_url,
                progress=_progress_adapter if progress else _default_progress,
                **opts,
            )
            results: List[str] = []
            for line in stdout.splitlines():
                m = re.search(r"Downloaded (.+)$", line.strip())
                if m:
                    results.append(
                        str(Path(dest_dir or ".").joinpath(m.group(1)).resolve()))
            return results
        except MegaError as e:
            if progress:
                progress(f"BŁĄD: {e}")
            return []

    def download(
        self,
        url: str,
        progress: ProgressFn = None,
        dest_dir: Optional[Union[str, Path]] = None,
        choose_files: bool = False,
    ) -> Union[Optional[str], List[str]]:
        """Skrót: rozpoznaje typ linku i pobiera plik lub folder."""

        if "/folder/" in url:
            return self.download_folder(url, dest_dir=dest_dir, choose_files=choose_files, progress=progress)
        return self.download_file(url, dest_dir=dest_dir, progress=progress)
