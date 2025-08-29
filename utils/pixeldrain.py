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
from typing import Optional, Dict, Any


class PixelDrainDownloader:
    """
    Klasa odpowiedzialna za pobieranie plików z serwisu Pixeldrain.
    """

    def download(self, url: str, progress_callback: callable) -> Optional[str]:
        """
        Pobiera pojedynczy plik z podanego URL-a Pixeldrain.

        Args:
            url (str): Pełny URL do pliku na Pixeldrain.
            progress_callback (callable): Funkcja zwrotna do raportowania postępu,
                                          przyjmująca jeden argument (string z logiem).

        Returns:
            Optional[str]: Ścieżka absolutna do pobranego pliku w przypadku sukcesu,
                           w przeciwnym razie None.
        """
        match = re.search(r'pixeldrain\.com/(u|l)/([a-zA-Z0-9]+)', url)
        if not match:
            progress_callback(
                f"❌ BŁĄD: Nieprawidłowy format URL-a Pixeldrain: {url}")
            return None

        file_id = match.group(2)
        filename = f"{file_id}.bin"  # Domyślna nazwa w razie błędu API
        header = f"{'='*20} ZADANIE PIXELDRAIN {'='*20}\nURL: {url}"

        try:
            progress_callback("--> Pobieranie informacji o pliku...")
            info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
            info_resp = requests.get(info_url, timeout=10)
            info_resp.raise_for_status()
            info = info_resp.json()
            filename = info.get('name', filename)
            size_mb = info.get('size', 0) / 1024 / 1024
            header += f"\nNazwa pliku: {filename}\nRozmiar: {size_mb:.2f} MB\n{'-'*50}"
            progress_callback(header)

        except requests.exceptions.RequestException as e:
            progress_callback(f"❌ BŁĄD SIECIOWY podczas pobierania info: {e}")
            return None

        download_url = f"https://pixeldrain.com/api/file/{file_id}?download"
        # Używamy wget, ponieważ jest zoptymalizowany do pobierania w środowisku Linux
        # i daje świetny, szczegółowy output postępu.
        command = ['wget', download_url, '-O',
                   filename, '--progress=bar:force']

        try:
            process = subprocess.Popen(
                command,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            for line in iter(process.stderr.readline, ''):
                progress_callback(line.strip())

            process.wait()

            if process.returncode == 0:
                progress_callback(f"\n✅ POBRANO NA MASZYNĘ COLAB: {filename}")
                return os.path.abspath(filename)
            else:
                progress_callback(
                    f"\n❌ BŁĄD! wget zakończył działanie z kodem: {process.returncode}")
                return None

        except Exception as e:
            progress_callback(
                f"\n❌ BŁĄD KRYTYCZNY podczas uruchamiania wget: {e}")
            return None
