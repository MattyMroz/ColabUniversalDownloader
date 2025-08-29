# -*- coding: utf-8 -*-
"""
Moduł: google_drive.py
Cel: Zarządzanie interakcją z API Dysku Google.

Moduł ten dostarcza klasę GoogleDriveManager, która upraszcza operacje
takie jak uwierzytelnianie, wyszukiwanie ID dysków (współdzielonych i "Mój Dysk"),
wysyłanie plików, tworzenie publicznych linków do pobierania oraz
planowanie usunięcia plików po określonym czasie.
"""

import os
import threading
import time
from typing import Optional, Dict

try:
    from google.colab import auth
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    IS_COLAB = True
except ImportError:
    IS_COLAB = False


class GoogleDriveManager:
    """
    Klasa do zarządzania operacjami na Dysku Google.
    """

    def __init__(self):
        """
        Inicjalizuje managera i próbuje uwierzytelnić użytkownika w Colab.
        """
        self.drive_service = None
        if IS_COLAB:
            try:
                auth.authenticate_user()
                self.drive_service = build('drive', 'v3')
            except Exception as e:
                print(f"Błąd autoryzacji Google: {e}")
        else:
            print("Ostrzeżenie: Wygląda na to, że nie jesteś w środowisku Google Colab. Funkcje GDrive nie będą działać.")

    def is_ready(self) -> bool:
        """Sprawdza, czy usługa API Dysku jest gotowa do użycia."""
        return self.drive_service is not None

    def get_drive_id(self, drive_name: str, is_shared: bool) -> Optional[str]:
        """
        Pobiera ID Dysku (Współdzielonego lub 'root' dla Mojego Dysku).

        Args:
            drive_name (str): Nazwa dysku współdzielonego (ignorowane dla Mojego Dysku).
            is_shared (bool): True, jeśli szukamy dysku współdzielonego.

        Returns:
            Optional[str]: ID dysku lub None w przypadku błędu/nieznalezienia.
        """
        if not self.is_ready():
            return None
        if not is_shared:
            return 'root'  # 'root' to alias dla "Mój Dysk"

        try:
            drives = self.drive_service.drives().list().execute()
            for d in drives.get('drives', []):
                if d['name'] == drive_name:
                    return d['id']
            return None
        except Exception as e:
            print(f"❌ BŁĄD podczas wyszukiwania Dysków Współdzielonych: {e}")
            return None

    def upload_and_share(self, local_filepath: str, parent_id: str) -> Optional[Dict[str, str]]:
        """
        Wysyła plik na Dysk Google, udostępnia go publicznie i zwraca link.

        Args:
            local_filepath (str): Ścieżka do pliku na maszynie lokalnej.
            parent_id (str): ID folderu/dysku nadrzędnego na GDrive.

        Returns:
            Optional[Dict[str, str]]: Słownik z linkiem i ID pliku, lub None.
        """
        if not self.is_ready():
            return None
        try:
            filename = os.path.basename(local_filepath)
            print(f"--> Rozpoczynam wysyłanie '{filename}' na Dysk Google...")

            file_metadata = {'name': filename, 'parents': [parent_id]}
            media = MediaFileUpload(local_filepath, resumable=True)

            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id',
                supportsAllDrives=True
            ).execute()

            file_id = file.get('id')
            print(f"--> Plik wysłany. ID: {file_id}. Udostępnianie...")

            self.drive_service.permissions().create(
                fileId=file_id,
                body={'role': 'reader', 'type': 'anyone'},
                supportsAllDrives=True
            ).execute()

            updated_file = self.drive_service.files().get(
                fileId=file_id,
                fields='webContentLink',
                supportsAllDrives=True
            ).execute()

            public_link = updated_file.get('webContentLink')
            print("--> Link publiczny wygenerowany.")

            return {'link': public_link, 'id': file_id}

        except Exception as e:
            print(f"❌ BŁĄD podczas operacji na Dysku Google: {e}")
            return None

    def delete_file_after_delay(self, file_id: str, delay_seconds: int):
        """
        Usuwa plik z Dysku Google po określonym czasie (w osobnym wątku).

        Args:
            file_id (str): ID pliku do usunięcia.
            delay_seconds (int): Czas w sekundach do usunięcia.
        """
        if not self.is_ready():
            return

        def task():
            print(
                f"--> [Wątek w tle] Plik {file_id} zostanie usunięty za {delay_seconds}s.")
            time.sleep(delay_seconds)
            try:
                self.drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
                print(
                    f"--> [Wątek w tle] Plik {file_id} został pomyślnie usunięty.")
            except Exception as e:
                print(
                    f"--> [Wątek w tle] Błąd podczas usuwania pliku {file_id}: {e}")

        threading.Thread(target=task).start()
