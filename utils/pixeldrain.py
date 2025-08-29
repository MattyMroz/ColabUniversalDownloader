# -*- coding: utf-8 -*-
"""
Moduł: pixeldrain.py
Cel: Obsługa pobierania plików z serwisu Pixeldrain.

Ten moduł zawiera klasę PixelDrainDownloader, która jest odpowiedzialna za
cały proces pobierania pliku z Pixeldrain: od analizy linku, przez pobranie
metadanych, aż po ściągnięcie pliku na maszynę wirtualną Colab za pomocą `wget`.
"""

import os
import re
import subprocess
import requests
from typing import Optional

# clear_output do odświeżania wyjścia jak w przykładzie ULTRATHING
try:
    from IPython.display import clear_output as _clear_output
except Exception:  # środowisko bez IPython (fallback: brak czyszczenia)
    def _clear_output(*args, **kwargs):
        pass


class PixelDrainDownloader:
    """Klasa odpowiedzialna za pobieranie plików z serwisu Pixeldrain."""

    def download(self, url: str, progress_callback: callable = None) -> Optional[str]:
        """
        Pobiera pojedynczy plik z podanego URL-a Pixeldrain z dynamicznym
        odświeżaniem postępu (clear_output) — zgodnie z przykładem ULTRATHING.

        Argument progress_callback jest opcjonalny i nie jest używany w trybie
        clear_output.
        """
        match = re.search(r'pixeldrain\.com/(u|l)/([a-zA-Z0-9]+)', url)
        if not match:
            print(f"❌ BŁĄD: Nieprawidłowy format URL-a Pixeldrain: {url}")
            return None

        file_id = match.group(2)
        filename = f"{file_id}.bin"  # Domyślna nazwa w razie błędu API

        # Zbuduj wstępny nagłówek w preferowanym formacie (zostanie wzbogacony po pobraniu metadanych)
        header = f"{'='*20} ZADANIE PIXELDRAIN {'='*20}\nURL: {url}"

        # Pobranie metadanych do nagłówka
        try:
            info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
            info_resp = requests.get(info_url, timeout=10)
            info_resp.raise_for_status()
            info = info_resp.json()
            filename = info.get('name', filename)
            size = info.get('size', 0)
            header = (
                f"{'='*20} ZADANIE PIXELDRAIN {'='*20}\n"
                f"URL: {url}\n"
                f"Nazwa pliku: {filename}\n"
                f"Rozmiar: {size / 1024 / 1024:.2f} MB\n"
                f"{'-'*50}"
            )
        except requests.exceptions.RequestException as e:
            print(f"❌ BŁĄD SIECIOWY podczas pobierania info: {e}")
            return None

        download_url = f"https://pixeldrain.com/api/file/{file_id}?download"
        command = ['wget', download_url, '-O', filename, '--progress=bar:force']

        try:
            process = subprocess.Popen(
                command,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            # Dynamiczne odświeżanie: czyść i pokaż nagłówek + aktualną linię z wget
            for line in iter(process.stderr.readline, ''):
                _clear_output(wait=True)
                print(header)
                print(line, end='')

            process.wait()

            _clear_output(wait=True)
            print(header)

            if process.returncode == 0:
                print(f"\n✅ POBRANO NA MASZYNĘ COLAB: {filename}")
                return os.path.abspath(filename)
            else:
                print(f"\n❌ BŁĄD! wget zakończył działanie z kodem: {process.returncode}")
                return None

        except Exception as e:
            _clear_output(wait=True)
            print(header)
            print(f"\n❌ BŁĄD KRYTYCZNY podczas uruchamiania wget: {e}")
            return None
