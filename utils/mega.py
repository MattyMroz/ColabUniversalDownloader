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
