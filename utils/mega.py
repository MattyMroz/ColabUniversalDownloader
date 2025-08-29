"""Mega.nz downloader utilities.

Ten moduł dostarcza dwie warstwy:

- Niskopoziomowy wrapper ``Megatools`` (na bazie sprawdzonej biblioteki
  pymegatools), który uruchamia polecenie ``megatools`` i przechwytuje
  wyjście/progress.
- Wysokopoziomową klasę ``MegaDownloader`` z prostym API do:
  - pobierania pojedynczego pliku,
  - pobierania całego folderu (z opcjonalną interaktywną selekcją plików),
  - listowania zawartości folderu (tekstowo).

Uwaga:
- Do działania wymagany jest binarny ``megatools``. Jeśli nie podasz
  ścieżki do istniejącego programu, wrapper pobierze gotowy binarny plik
  (Linux/Windows) do katalogu tymczasowego i użyje go automatycznie.
- Interaktywna selekcja (choose_files=True) delegowana jest do wbudowanego
  trybu megatools (``--choose-files``) i działa najlepiej w zwykłym
  terminalu (CMD/PowerShell/Colab input). W trybie nieinteraktywnym
  pozostaw choose_files=False – pobierze cały folder.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE, Popen
from tempfile import gettempdir
from typing import Callable, Coroutine, Optional, Sequence, Union, List
import logging
import platform
import re
import stat
import requests

__all__ = [
    "MegaError",
    "Megatools",
    "MegaDownloader",
]


logger = logging.getLogger(Path(__file__).stem)


class MegaError(Exception):
    """Wyjątek dla wszystkich błędów raportowanych przez megatools.

    Atrybuty:
        returncode: Kod wyjścia procesu megatools.
    """

    def __init__(self, returncode: int, *args: object) -> None:
        self.returncode = returncode
        super().__init__(*args)


Stream = list[str]


def _to_string(*seq: Sequence) -> tuple[str, ...]:
    return tuple("".join(s) for s in seq)


def _parse_options(command: list[str], **options) -> None:
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


def _execute(command: list[str], on_read: Optional[Callable] = None, *args) -> tuple:
    """Uruchamia proces i strumieniowo czyta stdout/stderr, opcjonalnie
    wywołując callback po każdej nowej linii.

    Zwraca: (stdout_text, stderr_text, returncode)
    """

    if on_read and hasattr(on_read, "__call__") and getattr(on_read, "__code__", None):
        # zakładamy, że callback jest synchroniczny; brak wsparcia dla async tutaj
        pass
    process = Popen(command, stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="ignore")
    streams: list[Stream] = []
    for f in (process.stdout, process.stderr):
        stream: Stream = []
        for line in iter(f.readline, ""):
            stream.append(line)
            if on_read:
                on_read(stream, process, *args)
        streams.append(stream)
    return (*_to_string(*streams), process.wait())


def _default_progress(stream: Stream, _) -> None:
    """Domyślny callback – wypisuje ostatnią linię strumienia."""

    print(end=stream[-1])


def _parse_and_raise(returncode: int, error: str) -> None:
    """Normalizuje komunikat o błędzie z megatools i podnosi MegaError."""

    pattern = re.compile(r"\w+: ")
    match = pattern.search(error)
    if match:
        error = error.replace(match.group(0), "", 1)
    raise MegaError(returncode, f"[returnCode {returncode}] {error.strip()}")


class Megatools:
    """Lekki wrapper wokół binarki 'megatools'.

    Jeżeli nie podasz ścieżki do istniejącego programu, wrapper pobierze
    odpowiedni plik wykonywalny do katalogu tymczasowego.
    """

    def __init__(self, executable: Union[Path, str, None] = None) -> None:
        self.tmp_directory = Path(gettempdir())
        if not executable:
            # Pobierz gotową binarkę (Linux/Windows) – fallback gdy brak w systemie
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
                    # Na Windowsie może nie być konieczne/mozliwe ustawienie chmod – ignorujemy
                    pass
        self.executable = str(executable)

    def download(
        self,
        url: str,
        progress: Optional[Callable] = _default_progress,
        progress_arguments: tuple = (),
        assume_async: bool = False,  # nieużywane – tylko zgodność interfejsu
        **options,
    ) -> tuple[str, int]:
        """Pobiera plik lub folder z podanego URL Mega.nz.

        Kluczowe opcje:
        - path=PATH             (katalog docelowy)
        - choose_files=True     (interaktywny wybór plików z folderu)
        - no_progress=True      (wyłącza pasek postępu megatools)

        Zwraca: (stdout_text, returncode). W przypadku błędu – MegaError.
        """

        command = [self.executable, "dl", url, "--no-ask-password"]
        _parse_options(command, **options)
        logger.info(f"Uruchamiam: {command}")
        stdout, stderr, returncode = _execute(command, progress, *progress_arguments)
        if stderr and returncode != 0:
            _parse_and_raise(returncode, stderr)
        return stdout, returncode

    def ls(self, url: str, **options) -> str:
        """Zwraca tekstowe listowanie zawartości folderu/publicznego linku.

        Używa podkomendy `ls`. Wynik jest tekstowy – idealny do wypisania w logu.
        """

        command = [self.executable, "ls", url]
        _parse_options(command, **options)
        logger.info(f"Uruchamiam: {command}")
        stdout, stderr, returncode = _execute(command)
        if stderr and returncode != 0:
            _parse_and_raise(returncode, stderr)
        return stdout

    @property
    def version(self) -> str:
        """Zwraca wersję megatools (np. 1.11.0)."""

        stdout, _ = self.download("", progress=None, version=True)
        return stdout.split()[1]

    def filename(self, url: str) -> str:
        """Zwraca nazwę pliku dla linku Mega.nz.

        Realizowane przez szybkie zakończenie pobierania po odczycie nazwy.
        """

        def _stop_early(stream: Stream, process: Popen) -> None:
            # Po pierwszej linii megatools zwykle wypisuje 'Nazwa: ...' – kończymy proces
            if stream and stream[-1]:
                try:
                    process.terminate()
                except Exception:
                    pass

        stdout, _ = self.download(
            url,
            progress=_stop_early,
            print_names=True,
            limit_speed=1,
            path=str(self.tmp_directory),
        )
        return stdout.split(":")[0].strip()


# ------------------------
# Wyższy poziom: API proste
# ------------------------


ProgressFn = Optional[Callable[[str], None]]


@dataclass
class MegaDownloader:
    """Wysokopoziomowy pomocnik do pobierania z Mega.nz.

    Metody kluczowe:
    - list_folder_contents(url)           -> str (tekst do wypisania)
    - download_file(url, dest_dir, ...)   -> Optional[str] (ścieżka pliku)
    - download_folder(url, choose_files)  -> List[str] (lista ścieżek)

    Uwaga: Dla wybierania plików w folderze ustaw ``choose_files=True`` –
    zostanie użyty tryb interaktywny megatools. W przeciwnym razie pobierze
    cały folder.
    """

    executable: Optional[Union[str, Path]] = None

    def __post_init__(self) -> None:
        self._mega = Megatools(self.executable)

    # --- API ---
    def list_folder_contents(self, folder_url: str) -> str:
        """Zwraca surowe listowanie zawartości folderu.

        Parametry:
            folder_url: Publiczny link do folderu Mega.nz.

        Zwraca:
            Tekst wyjścia z ``megatools ls`` – można wypisać lub sparsować
            zewnętrznie, w zależności od potrzeb UI.
        """

        return self._mega.ls(folder_url)

    def download_file(
        self,
        file_url: str,
        dest_dir: Optional[Union[str, Path]] = None,
        progress: ProgressFn = None,
    ) -> Optional[str]:
        """Pobiera pojedynczy plik z Mega.nz.

        Parametry:
            file_url: Link do pliku Mega.nz (https://mega.nz/file/...).
            dest_dir: Katalog docelowy (opcjonalnie).
            progress: Callback wywoływany z nowymi liniami logów (opcjonalnie).

        Zwraca:
            Pełną ścieżkę do pobranego pliku lub None w razie błędu.
        """

        opts = {}
        if dest_dir:
            opts["path"] = str(dest_dir)

        def _progress_adapter(stream: Stream, _proc):
            if progress:
                progress(stream[-1])

        try:
            stdout, rc = self._mega.download(file_url, progress=_progress_adapter if progress else _default_progress, **opts)
            # heurystyka: ostatnia linia zwykle zawiera 'Downloaded <nazwa>' – spróbujmy znaleźć nazwę
            # Jeśli dest_dir podany, plik znajdzie się w dest_dir
            # W przeciwnym razie – w bieżącym katalogu
            filename = None
            for line in stdout.splitlines()[::-1]:
                m = re.search(r"Downloaded (.+)$", line.strip())
                if m:
                    filename = m.group(1)
                    break
            if not filename:
                # Plan B – użyj Megatools.filename
                try:
                    filename = self._mega.filename(file_url)
                except Exception:
                    filename = None
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
        """Pobiera folder z Mega.nz.

        Parametry:
            folder_url: Link do folderu Mega.nz (https://mega.nz/folder/...).
            dest_dir: Katalog docelowy (opcjonalnie).
            choose_files: Jeśli True – używa interaktywnego wyboru (megatools --choose-files).
            progress: Callback do logowania postępu (opcjonalnie).

        Zwraca:
            Listę pełnych ścieżek do pobranych elementów (heurystycznie wykryte). W razie trudności
            może zwrócić pustą listę, choć sam transfer mógł się powieść –
            zależne od wyjścia megatools.
        """

        opts = {"choose_files": True} if choose_files else {}
        if dest_dir:
            opts["path"] = str(dest_dir)

        def _progress_adapter(stream: Stream, _proc):
            if progress:
                progress(stream[-1])

        try:
            stdout, _ = self._mega.download(folder_url, progress=_progress_adapter if progress else _default_progress, **opts)
            # Spróbuj wyciągnąć nazwy pobranych plików z logu
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

    # Zgodność wsteczna / skrót: rozpoznaje typ linku i wywołuje odpowiednią metodę
    def download(
        self,
        url: str,
        progress: ProgressFn = None,
        dest_dir: Optional[Union[str, Path]] = None,
        choose_files: bool = False,
    ) -> Optional[Union[str, List[str]]]:
        """Skrót: pobierz plik lub folder w zależności od URL.

        - Dla linku do pliku zwraca ścieżkę (str) lub None.
        - Dla linku do folderu zwraca listę ścieżek (List[str]) lub [].
        """

        if "/folder/" in url:
            return self.download_folder(url, dest_dir=dest_dir, choose_files=choose_files, progress=progress)
        return self.download_file(url, dest_dir=dest_dir, progress=progress)

# -*- coding: utf-8 -*-
"""
Moduł: mega.py
Kryptonim: ULTRATHING
Cel: Obsługa pobierania plików i listowania folderów z serwisu Mega.nz.

Ten moduł zawiera klasę MegaDownloader, która jest odpowiedzialna za
interakcję z narzędziem `megatools`. Umożliwia pobieranie pojedynczych plików
oraz, co kluczowe, listowanie zawartości folderów, aby użytkownik mógł
dokonać wyboru przed pobraniem.
"""

import os
import re
import subprocess
from typing import Optional, List, Dict, Any


class MegaDownloader:
    """
    Klasa odpowiedzialna za pobieranie plików i listowanie folderów z Mega.nz.
    """

    def _parse_megals_output(self, output: str) -> List[Dict[str, Any]]:
        """
        Prywatna metoda do parsowania wyjścia komendy `megals`.

        Przykładowe wyjście do sparsowania:
        A         30864243 2023-11-15 18:10:05 some_file_name.zip
        d                - 2024-01-20 12:30:00 some_folder_name

        Returns:
            Lista słowników, gdzie każdy reprezentuje plik lub folder.
        """
        files_list = []
        lines = output.strip().split('\n')
        # Pomijamy linię nagłówka, jeśli istnieje
        if lines and "ATTRIBUTES" in lines[0] and "SIZE" in lines[0]:
            lines = lines[1:]

        for line in lines:
            parts = re.split(r'\s+', line.strip(), maxsplit=4)
            if len(parts) < 5:
                continue

            attributes, size_str, date, time, name = parts
            is_directory = 'd' in attributes

            try:
                size_bytes = int(size_str) if not is_directory else 0
            except ValueError:
                size_bytes = 0

            files_list.append({
                'name': name,
                'size_bytes': size_bytes,
                'is_directory': is_directory
            })
        return files_list

    def list_folder_contents(self, folder_url: str) -> Optional[List[Dict[str, Any]]]:
        """
        Listuje zawartość folderu Mega.nz.

        Args:
            folder_url (str): URL do folderu na Mega.nz.

        Returns:
            Lista słowników z informacjami o plikach lub None w przypadku błędu.
        """
        print(f"--> Listowanie zawartości folderu: {folder_url}")
        command = ['megals', '--header', folder_url]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8',
                errors='replace'
            )
            return self._parse_megals_output(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"❌ BŁĄD podczas listowania folderu. Kod: {e.returncode}")
            print(f"   Stderr: {e.stderr.strip()}")
            return None
        except FileNotFoundError:
            print("❌ BŁĄD KRYTYCZNY: Komenda 'megals' nie została znaleziona. Upewnij się, że 'megatools' jest zainstalowane.")
            return None

    def download_file(self, url: str, progress_callback: callable) -> Optional[str]:
        """
        Pobiera pojedynczy plik z podanego URL-a Mega.

        Args:
            url (str): Pełny URL do pliku na Mega.
            progress_callback (callable): Funkcja zwrotna do raportowania postępu.

        Returns:
            Optional[str]: Ścieżka absolutna do pobranego pliku lub None.
        """
        header = f"{'='*20} ZADANIE MEGA {'='*20}\nURL: {url}\n{'-'*50}"
        progress_callback(header)

        # megadl domyślnie pobiera do bieżącego katalogu
        # Wyłączamy wbudowany pasek, by samemu kontrolować output
        command = ['megadl', url, '--no-progress']

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,  # megadl wysyła status na stdout
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            filename = ""
            # Przechwytujemy output w czasie rzeczywistym
            for line in iter(process.stdout.readline, ''):
                # Próbujemy wyciągnąć nazwę pliku z logów
                if not filename and "Downloading" in line:
                    match = re.search(r"Downloading\s+(.+?)\s*\.\.\.", line)
                    if match:
                        filename = match.group(1).strip().strip("'")
                progress_callback(line.strip())

            process.wait()

            stderr_output = process.stderr.read()

            if process.returncode == 0:
                # Jeśli nie udało się sparsować nazwy, próbujemy znaleźć plik
                if not filename:
                    # To jest fallback, ale rzadko powinien być potrzebny
                    progress_callback(
                        "Nie udało się automatycznie wykryć nazwy pliku, próba odnalezienia...")
                    # Prosta heurystyka - znajdź najnowszy plik w /content/
                    files = [f for f in os.listdir(
                        '/content') if os.path.isfile(f)]
                    if files:
                        filename = max(files, key=os.path.getctime)

                if filename and os.path.exists(filename):
                    progress_callback(
                        f"\n✅ POBRANO NA MASZYNĘ COLAB: {filename}")
                    return os.path.abspath(filename)
                else:
                    progress_callback(
                        f"\n⚠️ OSTRZEŻENIE: Proces zakończony sukcesem, ale nie znaleziono pliku wyjściowego '{filename}'.")
                    return None
            else:
                progress_callback(
                    f"\n❌ BŁĄD! megadl zakończył działanie z kodem: {process.returncode}")
                progress_callback(f"   Stderr: {stderr_output.strip()}")
                return None

        except FileNotFoundError:
            progress_callback(
                "❌ BŁĄD KRYTYCZNY: Komenda 'megadl' nie została znaleziona. Upewnij się, że 'megatools' jest zainstalowane.")
            return None
        except Exception as e:
            progress_callback(
                f"\n❌ BŁĄD KRYTYCZNY podczas uruchamiania megadl: {e}")
            return None
