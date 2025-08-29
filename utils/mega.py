# """Mega.nz downloader utilities.

# Ten moduł dostarcza dwie warstwy:

# - Niskopoziomowy wrapper ``Megatools`` (na bazie sprawdzonej biblioteki
#   pymegatools), który uruchamia polecenie ``megatools`` i przechwytuje
#   wyjście/progress.
# - Wysokopoziomową klasę ``MegaDownloader`` z prostym API do:
#   - pobierania pojedynczego pliku,
#   - pobierania całego folderu (z opcjonalną interaktywną selekcją plików),
#   - listowania zawartości folderu (tekstowo).

# Uwaga:
# - Do działania wymagany jest binarny ``megatools``. Jeśli nie podasz
#   ścieżki do istniejącego programu, wrapper pobierze gotowy binarny plik
#   (Linux/Windows) do katalogu tymczasowego i użyje go automatycznie.
# - Interaktywna selekcja (choose_files=True) delegowana jest do wbudowanego
#   trybu megatools (``--choose-files``) i działa najlepiej w zwykłym
#   terminalu (CMD/PowerShell/Colab input). W trybie nieinteraktywnym
#   pozostaw choose_files=False – pobierze cały folder.
# """

# from __future__ import annotations

# from dataclasses import dataclass
# from pathlib import Path
# from subprocess import PIPE, Popen
# from tempfile import gettempdir
# from typing import Callable, Coroutine, Optional, Sequence, Union, List
# import logging
# import platform
# import re
# import stat
# import requests

# __all__ = [
#     "MegaError",
#     "Megatools",
#     "MegaDownloader",
# ]


# logger = logging.getLogger(Path(__file__).stem)


# class MegaError(Exception):
#     """Wyjątek dla wszystkich błędów raportowanych przez megatools.

#     Atrybuty:
#         returncode: Kod wyjścia procesu megatools.
#     """

#     def __init__(self, returncode: int, *args: object) -> None:
#         self.returncode = returncode
#         super().__init__(*args)


# Stream = list[str]


# def _to_string(*seq: Sequence) -> tuple[str, ...]:
#     return tuple("".join(s) for s in seq)


# def _parse_options(command: list[str], **options) -> None:
#     """Zamienia opcje keyword na przełączniki CLI dla megatools.

#     Przykłady:
#     - no_progress=True        => --no-progress
#     - path="/tmp"             => --path=/tmp
#     - limit_speed=1024        => --limit-speed=1024
#     """

#     for option, value in options.items():
#         option = option.replace("_", "-")
#         if value is True:
#             command.append(f"--{option}")
#             continue
#         if value is False or value is None:
#             # pomiń flagi z False/None
#             continue
#         command.append(f"--{option}={value}")


# def _execute(command: list[str], on_read: Optional[Callable] = None, *args) -> tuple:
#     """Uruchamia proces i strumieniowo czyta stdout/stderr, opcjonalnie
#     wywołując callback po każdej nowej linii.

#     Zwraca: (stdout_text, stderr_text, returncode)
#     """

#     if on_read and hasattr(on_read, "__call__") and getattr(on_read, "__code__", None):
#         # zakładamy, że callback jest synchroniczny; brak wsparcia dla async tutaj
#         pass
#     process = Popen(command, stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="ignore")
#     streams: list[Stream] = []
#     for f in (process.stdout, process.stderr):
#         stream: Stream = []
#         for line in iter(f.readline, ""):
#             stream.append(line)
#             if on_read:
#                 on_read(stream, process, *args)
#         streams.append(stream)
#     return (*_to_string(*streams), process.wait())


# def _default_progress(stream: Stream, _) -> None:
#     """Domyślny callback – wypisuje ostatnią linię strumienia."""

#     print(end=stream[-1])


# def _parse_and_raise(returncode: int, error: str) -> None:
#     """Normalizuje komunikat o błędzie z megatools i podnosi MegaError."""

#     pattern = re.compile(r"\w+: ")
#     match = pattern.search(error)
#     if match:
#         error = error.replace(match.group(0), "", 1)
#     raise MegaError(returncode, f"[returnCode {returncode}] {error.strip()}")


# class Megatools:
#     """Lekki wrapper wokół binarki 'megatools'.

#     Jeżeli nie podasz ścieżki do istniejącego programu, wrapper pobierze
#     odpowiedni plik wykonywalny do katalogu tymczasowego.
#     """

#     def __init__(self, executable: Union[Path, str, None] = None) -> None:
#         self.tmp_directory = Path(gettempdir())
#         if not executable:
#             # Pobierz gotową binarkę (Linux/Windows) – fallback gdy brak w systemie
#             is_windows = platform.system() == "Windows"
#             executable = self.tmp_directory / ("megatools.exe" if is_windows else "megatools")
#             if not Path(executable).exists():
#                 logger.info("Pobieranie binarki megatools...")
#                 url = "https://raw.githubusercontent.com/justaprudev/megatools/master/megatools"
#                 binary = requests.get(f"{url}.exe" if is_windows else url)
#                 with open(executable, "wb") as f:
#                     f.write(binary.content)
#                 # Ustaw prawa wykonywania (na systemach uniksowych)
#                 try:
#                     Path(executable).chmod(
#                         Path(executable).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
#                     )
#                 except Exception:
#                     # Na Windowsie może nie być konieczne/mozliwe ustawienie chmod – ignorujemy
#                     pass
#         self.executable = str(executable)

#     def download(
#         self,
#         url: str,
#         progress: Optional[Callable] = _default_progress,
#         progress_arguments: tuple = (),
#         assume_async: bool = False,  # nieużywane – tylko zgodność interfejsu
#         **options,
#     ) -> tuple[str, int]:
#         """Pobiera plik lub folder z podanego URL Mega.nz.

#         Kluczowe opcje:
#         - path=PATH             (katalog docelowy)
#         - choose_files=True     (interaktywny wybór plików z folderu)
#         - no_progress=True      (wyłącza pasek postępu megatools)

#         Zwraca: (stdout_text, returncode). W przypadku błędu – MegaError.
#         """

#         command = [self.executable, "dl", url, "--no-ask-password"]
#         _parse_options(command, **options)
#         logger.info(f"Uruchamiam: {command}")
#         stdout, stderr, returncode = _execute(command, progress, *progress_arguments)
#         if stderr and returncode != 0:
#             _parse_and_raise(returncode, stderr)
#         return stdout, returncode

#     # Uwaga: Nie używamy już subkomendy 'ls' – by uniknąć wymogu logowania.
#     # Listowanie folderu realizujemy poprzez 'dl' z opcją 'print_names' i wczesnym zakończeniem.

#     @property
#     def version(self) -> str:
#         """Zwraca wersję megatools (np. 1.11.0)."""

#         stdout, _ = self.download("", progress=None, version=True)
#         return stdout.split()[1]

#     def filename(self, url: str) -> str:
#         """Zwraca nazwę pliku dla linku Mega.nz.

#         Realizowane przez szybkie zakończenie pobierania po odczycie nazwy.
#         """

#         def _stop_early(stream: Stream, process: Popen) -> None:
#             # Po pierwszej linii megatools zwykle wypisuje 'Nazwa: ...' – kończymy proces
#             if stream and stream[-1]:
#                 try:
#                     process.terminate()
#                 except Exception:
#                     pass

#         stdout, _ = self.download(
#             url,
#             progress=_stop_early,
#             print_names=True,
#             limit_speed=1,
#             path=str(self.tmp_directory),
#         )
#         return stdout.split(":")[0].strip()


# # ------------------------
# # Wyższy poziom: API proste
# # ------------------------


# ProgressFn = Optional[Callable[[str], None]]


# @dataclass
# class MegaDownloader:
#     """Wysokopoziomowy pomocnik do pobierania z Mega.nz.

#     Metody kluczowe:
#     - list_folder_contents(url)           -> str (tekst do wypisania)
#     - download_file(url, dest_dir, ...)   -> Optional[str] (ścieżka pliku)
#     - download_folder(url, choose_files)  -> List[str] (lista ścieżek)

#     Uwaga: Dla wybierania plików w folderze ustaw ``choose_files=True`` –
#     zostanie użyty tryb interaktywny megatools. W przeciwnym razie pobierze
#     cały folder.
#     """

#     executable: Optional[Union[str, Path]] = None

#     def __post_init__(self) -> None:
#         self._mega = Megatools(self.executable)

#     # --- API ---
#     def list_folder_contents(self, folder_url: str) -> str:
#         """Zwraca listę nazw elementów folderu bez logowania.

#         Implementacja używa `dl` z `print_names=True` i natychmiastowym
#         przerwaniem procesu po pierwszych liniach, aby tylko wydrukować nazwy.
#         """

#         collected: list[str] = []

#         def _collect_names(stream: Stream, process: Popen) -> None:
#             # Megatools przy --print-names drukuje linie "<name>: ..."
#             line = stream[-1]
#             # Zgromadź, ale po kilku liniach zakończ, by uniknąć realnego pobierania
#             if ":" in line:
#                 collected.append(line)
#             # Po 10 liniach kończymy (heurystyka)
#             if len(collected) >= 10:
#                 try:
#                     process.terminate()
#                 except Exception:
#                     pass

#         try:
#             self._mega.download(
#                 folder_url,
#                 progress=_collect_names,
#                 print_names=True,
#                 limit_speed=1,
#                 path=str(Path(self._mega.tmp_directory)),
#                 no_progress=True,
#             )
#         except MegaError:
#             # Ignorujemy błąd zakończenia procesu – ważne, że zebraliśmy nazwy
#             pass

#         # Zwróć zebrane linie jako tekst
#         return "".join(collected)

#     def download_file(
#         self,
#         file_url: str,
#         dest_dir: Optional[Union[str, Path]] = None,
#         progress: ProgressFn = None,
#     ) -> Optional[str]:
#         """Pobiera pojedynczy plik z Mega.nz.

#         Parametry:
#             file_url: Link do pliku Mega.nz (https://mega.nz/file/...).
#             dest_dir: Katalog docelowy (opcjonalnie).
#             progress: Callback wywoływany z nowymi liniami logów (opcjonalnie).

#         Zwraca:
#             Pełną ścieżkę do pobranego pliku lub None w razie błędu.
#         """

#         opts = {}
#         if dest_dir:
#             opts["path"] = str(dest_dir)

#         def _progress_adapter(stream: Stream, _proc):
#             if progress:
#                 progress(stream[-1])

#         try:
#             stdout, rc = self._mega.download(
#                 file_url,
#                 progress=_progress_adapter if progress else _default_progress,
#                 **opts,
#             )
#             # heurystyka: ostatnia linia zwykle zawiera 'Downloaded <nazwa>' – spróbujmy znaleźć nazwę
#             # Jeśli dest_dir podany, plik znajdzie się w dest_dir
#             # W przeciwnym razie – w bieżącym katalogu
#             filename = None
#             for line in stdout.splitlines()[::-1]:
#                 m = re.search(r"Downloaded (.+)$", line.strip())
#                 if m:
#                     filename = m.group(1)
#                     break
#             if not filename:
#                 # Plan B – użyj Megatools.filename
#                 try:
#                     filename = self._mega.filename(file_url)
#                 except Exception:
#                     filename = None
#             if filename:
#                 return str(Path(dest_dir or ".").joinpath(filename).resolve())
#             return None
#         except MegaError as e:
#             if progress:
#                 progress(f"BŁĄD: {e}")
#             return None

#     def download_folder(
#         self,
#         folder_url: str,
#         dest_dir: Optional[Union[str, Path]] = None,
#         choose_files: bool = False,
#         progress: ProgressFn = None,
#     ) -> List[str]:
#         """Pobiera folder z Mega.nz.

#         Parametry:
#             folder_url: Link do folderu Mega.nz (https://mega.nz/folder/...).
#             dest_dir: Katalog docelowy (opcjonalnie).
#             choose_files: Jeśli True – używa interaktywnego wyboru (megatools --choose-files).
#             progress: Callback do logowania postępu (opcjonalnie).

#         Zwraca:
#             Listę pełnych ścieżek do pobranych elementów (heurystycznie wykryte). W razie trudności
#             może zwrócić pustą listę, choć sam transfer mógł się powieść –
#             zależne od wyjścia megatools.
#         """

#         opts = {"choose_files": True} if choose_files else {}
#         if dest_dir:
#             opts["path"] = str(dest_dir)

#         def _progress_adapter(stream: Stream, _proc):
#             if progress:
#                 progress(stream[-1])

#         try:
#             stdout, _ = self._mega.download(
#                 folder_url,
#                 progress=_progress_adapter if progress else _default_progress,
#                 **opts,
#             )
#             # Spróbuj wyciągnąć nazwy pobranych plików z logu
#             results: List[str] = []
#             for line in stdout.splitlines():
#                 m = re.search(r"Downloaded (.+)$", line.strip())
#                 if m:
#                     results.append(str(Path(dest_dir or ".").joinpath(m.group(1)).resolve()))
#             return results
#         except MegaError as e:
#             if progress:
#                 progress(f"BŁĄD: {e}")
#             return []

#     # Zgodność wsteczna / skrót: rozpoznaje typ linku i wywołuje odpowiednią metodę
#     def download(
#         self,
#         url: str,
#         progress: ProgressFn = None,
#         dest_dir: Optional[Union[str, Path]] = None,
#         choose_files: bool = False,
#     ) -> Optional[Union[str, List[str]]]:
#         """Skrót: pobierz plik lub folder w zależności od URL.

#         - Dla linku do pliku zwraca ścieżkę (str) lub None.
#         - Dla linku do folderu zwraca listę ścieżek (List[str]) lub [].
#         """

#         if "/folder/" in url:
#             return self.download_folder(url, dest_dir=dest_dir, choose_files=choose_files, progress=progress)
#         return self.download_file(url, dest_dir=dest_dir, progress=progress)


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

    process = Popen(command, stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="ignore")
    out_lines: Stream = []
    err_lines: Stream = []
    # Czytaj stdout
    for line in iter(process.stdout.readline, ""):
        out_lines.append(line)
        if on_line:
            try:
                on_line(line)
            except Exception:
                # nie przerywaj pobierania, jeśli callback rzuci wyjątkiem
                pass
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
            executable = self.tmp_directory / ("megatools.exe" if is_windows else "megatools")
            if not Path(executable).exists():
                logger.info("Pobieranie binarki megatools...")
                url = "https://raw.githubusercontent.com/justaprudev/megatools/master/megatools"
                binary = requests.get(f"{url}.exe" if is_windows else url)
                with open(executable, "wb") as f:
                    f.write(binary.content)
                # Ustaw prawa wykonywania (na systemach uniksowych)
                try:
                    Path(executable).chmod(
                        Path(executable).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                    )
                except Exception:
                    pass
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
                    results.append(str(Path(dest_dir or ".").joinpath(m.group(1)).resolve()))
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