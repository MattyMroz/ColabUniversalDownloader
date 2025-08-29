import contextlib
import logging
import platform
import re
import stat
import time
from pathlib import Path
from subprocess import PIPE, Popen
from tempfile import gettempdir
from typing import Callable, List, Optional, Sequence, Tuple, Union

import requests

__all__ = ["Megatools", "MegaError", "MegaDownloader", "make_refreshing_progress"]

logger = logging.getLogger(Path(__file__).stem)

# Bezpieczny import clear_output (jak w Pixeldrain), aby móc rysować odświeżany pasek w notebooku
try:
    from IPython.display import clear_output as _clear_output  # type: ignore
except Exception:  # środowisko bez IPython (fallback: brak czyszczenia)
    def _clear_output(*args, **kwargs):  # type: ignore
        pass


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
        if on_line:
            with contextlib.suppress(Exception):
                on_line(line)
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


# ------------------------
# Odświeżany pasek postępu (header + linia jak wget)
# ------------------------

def _format_eta(seconds: float) -> str:
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"eta {h}h {m}m {s}s"
    if m:
        return f"eta {m}m {s}s"
    return f"eta {s}s"


def _parse_speed_to_mib_per_s(speed_str: str) -> Optional[float]:
    """Zwraca prędkość w MiB/s na podstawie napisu typu '39.7 MiB/s', '4.2 MB/s', '600 KiB/s'."""
    if not speed_str:
        return None
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([KMG]?i?B)/s\s*", speed_str, re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).lower()
    # Konwersje do MiB
    if unit in ("mib",):
        return val
    if unit in ("mb",):  # MB (dziesiętne) ~ MiB * 1.048576
        return val / 1.048576
    if unit in ("kib",):
        return val / 1024.0
    if unit in ("kb",):
        return val / (1024.0 * 1.048576)
    if unit in ("gib",):
        return val * 1024.0
    if unit in ("gb",):
        return (val * 1024.0) / 1.048576
    return None


def make_refreshing_progress(header_lines: List[str], bar_width: int = 20) -> Callable[[str], None]:
    """Tworzy callback, który rysuje pasek w stylu wget z nagłówkiem (clear_output).

    header_lines: lista linii nagłówka, np. [
        "==================== ZADANIE MEGA — PLIK ====================",
        f"URL: ...",
        "Nazwa pliku: —",
        "Rozmiar: —",
        "--------------------------------------------------",
    ]
    Funkcja aktualizuje pozycje 3 i 4 (nazwa i rozmiar), gdy tylko dane będą znane z logu megatools.
    """
    # Stałe formatowania/animacji
    NAME_WIDTH = 19          # szerokość okna nazwy
    LEAD_SPACES = 9          # start "od środka" – 9 spacji
    SCROLL_STEP = 3          # krok przesunięcia nazwy między klatkami
    FRAMES_PER_TICK = 4      # ile klatek narysować na jeden update z megatools
    FRAME_DELAY = 0.25       # opóźnienie między klatkami

    state = {"name": None, "total": None, "scroll": 0}  # total w MiB (float), scroll do nazwy

    def _bar_from_percent(p: float, width: int) -> str:
        p = max(0.0, min(100.0, p))
        filled = int((p / 100.0) * width)
        if filled >= width:
            return "[" + "=" * width + "]"
        return "[" + "=" * filled + ">" + " " * (width - filled - 1) + "]"

    def _format_line(name: str, perc: float, downloaded_mib: float, total_mib: float, speed_str: str) -> str:
        # 1) 19 znaków przewijanej nazwy + spacja + procent (dopasowanie -1)
        # Na pierwszej klatce zaczynamy z 9 spacjami z przodu, by był efekt "od środka",
        # potem przewijamy szybciej (większy krok).
        base = (name or "").strip()
        if not base:
            base = "-"
        if state["scroll"] == 0:
            name20 = (" " * LEAD_SPACES + base)
            # docięcie/dopełnienie do stałej szerokości 19
            name20 = (name20 + " " * NAME_WIDTH)[:NAME_WIDTH]
        else:
            scroll_src = (base + "   ") * 2  # bufor do przewijania
            idx = (state["scroll"] * SCROLL_STEP) % max(1, (len(scroll_src) - NAME_WIDTH))
            name20 = scroll_src[idx: idx + NAME_WIDTH]
            if len(name20) < NAME_WIDTH:
                name20 = name20.ljust(NAME_WIDTH)
        pct = f"{int(round(perc)):>3}%"

        # 2) Pasek postępu jak w wget
        bar = _bar_from_percent(perc, bar_width)

        # 3) Rozmiar w MB (dziesiętne), format "NNN.NNM" (wg wget: bez 'i')
        downloaded_mb = downloaded_mib * 1.048576
        size_s = f"{downloaded_mb:.2f}M"

        # 4) Prędkość w MB/s (dziesiętne) i ETA
        sp_mib = _parse_speed_to_mib_per_s(speed_str)
        mbps = sp_mib * 1.048576 if sp_mib is not None else None
        speed_s = f"{mbps:.1f}MB/s" if mbps is not None else speed_str.replace(" ", "")
        eta_s = ""
        if sp_mib and total_mib and perc > 0:
            remaining_mib = max(0.0, total_mib - downloaded_mib)
            eta = remaining_mib / sp_mib
            eta_s = _format_eta(eta)

        # 5) Sklej w stały układ odstępów (jak w przykładowej linii wget)
        # name(19) + ' ' + pct + bar + ' ' + size + '  ' + speed + '    ' + eta
        return f"{name20} {pct}{bar} {size_s}  {speed_s}    {eta_s}".rstrip()

    # Przykładowa linia megatools (ang.):
    # name.mp4: 39.78% - 238.4 MiB (249968240 bytes) of 599.3 MiB (39.7 MiB/s)
    PATTERN = re.compile(
        r"^(?P<name>[^:]+):\s*"
        r"(?P<perc>[0-9]+(?:\.[0-9]+)?)%\s*-\s*"
        r"(?P<down>[0-9]+(?:\.[0-9]+)?)\s*MiB.*?of\s*"
        r"(?P<total>[0-9]+(?:\.[0-9]+)?)\s*MiB.*?\("
        r"(?P<speed>[^)]+)\)\s*$",
        re.IGNORECASE,
    )

    def _callback(line: str) -> None:
        s = (line or "").strip()
        if not s:
            return
        m = PATTERN.match(s)
        if not m:
            return  # ignoruj niepasujące linie
        name = m.group("name").strip()
        perc = float(m.group("perc"))
        downloaded = float(m.group("down"))
        total = float(m.group("total"))
        speed = m.group("speed").strip()

        # Aktualizuj header (nazwa + rozmiar) przy pierwszym wykryciu lub zmianie pliku
        if name and name != state["name"]:
            state["name"] = name
            if len(header_lines) >= 3:
                header_lines[2] = f"Nazwa pliku: {name}"
        if total and total != state["total"]:
            state["total"] = total
            if len(header_lines) >= 4:
                header_lines[3] = f"Rozmiar: {total:.2f} MB"

        # Narysuj kilka klatek na jeden update z megatools – szybciej widoczny ruch nazwy
        for _ in range(FRAMES_PER_TICK):
            out_line = _format_line(name, perc, downloaded, total, speed)
            _clear_output(wait=True)
            for h in header_lines:
                print(h)
            print(out_line)
            state["scroll"] += 1
            with contextlib.suppress(Exception):
                time.sleep(FRAME_DELAY)

    return _callback
