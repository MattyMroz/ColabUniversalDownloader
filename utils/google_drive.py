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
from typing import Optional, Dict, List

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

    # Prost y kontekst do tłumienia wyjątków w pętlach sprzątających
    from contextlib import contextmanager
    @contextmanager
    def _suppress_exc(self):
        try:
            yield
        except Exception:
            pass

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

    def upload_and_share(self, local_filepath: str, parent_id: str, *, skip_if_exists: bool = True, replace_if_exists: bool = False) -> Optional[Dict[str, str]]:
        """
        Wysyła plik na Dysk Google, udostępnia go publicznie i zwraca link.

        Args:
            local_filepath (str): Ścieżka do pliku na maszynie lokalnej.
            parent_id (str): ID folderu/dysku nadrzędnego na GDrive.
            skip_if_exists (bool): Jeśli w folderze istnieje plik o tej samej nazwie – pomiń upload i zwróć link do istniejącego.
            replace_if_exists (bool): Jeśli True i istnieje plik o tej samej nazwie – usuń istniejący i prześlij na nowo.

        Returns:
            Optional[Dict[str, str]]: Słownik z linkiem i ID pliku, lub None.
        """
        if not self.is_ready():
            return None
        try:
            filename = os.path.basename(local_filepath)
            # 0) Deduplikacja: sprawdź, czy w folderze istnieje już plik o tej nazwie
            existing = None
            try:
                existing = self._find_file_in_folder_by_name(parent_id, filename)
            except Exception:
                existing = None

            if existing and skip_if_exists and not replace_if_exists:
                file_id = existing.get('id')
                print(f"--> Plik o nazwie '{filename}' już istnieje (ID: {file_id}). Pomijam upload.")
                # Upewnij się, że jest udostępniony publicznie i pobierz link
                try:
                    self.drive_service.permissions().create(
                        fileId=file_id,
                        body={'role': 'reader', 'type': 'anyone'},
                        supportsAllDrives=True
                    ).execute()
                except Exception:
                    pass
                updated_file = self.drive_service.files().get(
                    fileId=file_id,
                    fields='webContentLink',
                    supportsAllDrives=True
                ).execute()
                public_link = updated_file.get('webContentLink')
                print("--> Link publiczny istniejącego pliku pobrany.")
                return {'link': public_link, 'id': file_id}

            if existing and replace_if_exists:
                # Usuń istniejący i kontynuuj upload
                try:
                    self.drive_service.files().delete(fileId=existing.get('id'), supportsAllDrives=True).execute()
                    print(f"--> Usunięto istniejący plik '{filename}' (ID: {existing.get('id')}).")
                except Exception as e:
                    print(f"⚠️ Nie udało się usunąć istniejącego pliku '{filename}': {e}")

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

    def _find_file_in_folder_by_name(self, parent_id: str, filename: str) -> Optional[Dict[str, str]]:
        """Zwraca pierwszy plik w folderze o podanej nazwie (nie przeszukuje w głąb)."""
        if not self.is_ready():
            return None
        # Ucieczka cudzysłowów – używamy pojedynczych w zapytaniu
        safe_name = filename.replace("'", "\\'")
        q = f"name = '{safe_name}' and '{parent_id}' in parents and trashed = false"
        resp = self.drive_service.files().list(
            q=q,
            fields='files(id, name)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=1
        ).execute()
        items = resp.get('files', [])
        return items[0] if items else None

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
            print(f"--> [Wątek w tle] Plik {file_id} zostanie usunięty za {delay_seconds}s.")
            time.sleep(delay_seconds)
            # Kilka prób z krótkim odstępem (na wypadek chwilowych błędów)
            attempts = 3
            for i in range(1, attempts + 1):
                try:
                    self.drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
                    print(f"--> [Wątek w tle] Plik {file_id} został pomyślnie usunięty.")
                    return
                except Exception as e:
                    if i == attempts:
                        print(f"--> [Wątek w tle] Błąd podczas usuwania pliku {file_id}: {e}")
                    else:
                        time.sleep(2)

        threading.Thread(target=task).start()

    def delete_folder_after_delay(self, folder_id: str, delay_seconds: int):
        """
        Usuwa (trwale) folder z Dysku Google po określonym czasie (w osobnym wątku).

        Uwaga: Operacja usuwa folder po stronie GDrive (w większości przypadków trafia on do kosza).
        Jeśli folder znajduje się na Dysku współdzielonym, wymagane są odpowiednie uprawnienia.

        Args:
            folder_id (str): ID folderu do usunięcia.
            delay_seconds (int): Czas w sekundach do usunięcia.
        """
        if not self.is_ready():
            return

        def task():
            print(f"--> [Wątek w tle] Folder {folder_id} zostanie usunięty za {delay_seconds}s.")
            time.sleep(delay_seconds)
            try:
                # 1) Spróbuj usunąć wszystkie dzieci folderu (do 1000 na stronę)
                page_token = None
                while True:
                    query = f"'{folder_id}' in parents and trashed = false"
                    resp = self.drive_service.files().list(
                        q=query,
                        fields='nextPageToken, files(id)',
                        pageSize=1000,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageToken=page_token
                    ).execute()
                    for item in resp.get('files', []):
                        fid = item.get('id')
                        if not fid:
                            continue
                        with self._suppress_exc():
                            self.drive_service.files().delete(fileId=fid, supportsAllDrives=True).execute()
                    page_token = resp.get('nextPageToken')
                    if not page_token:
                        break

                # 2) Usuń sam folder (z retry)
                attempts = 3
                for i in range(1, attempts + 1):
                    try:
                        self.drive_service.files().delete(fileId=folder_id, supportsAllDrives=True).execute()
                        print(f"--> [Wątek w tle] Folder {folder_id} został pomyślnie usunięty.")
                        return
                    except Exception as e:
                        if i == attempts:
                            print(f"--> [Wątek w tle] Błąd podczas usuwania folderu {folder_id}: {e}")
                        else:
                            time.sleep(2)
            except Exception as e:
                print(f"--> [Wątek w tle] Błąd podczas usuwania zawartości folderu {folder_id}: {e}")

        threading.Thread(target=task).start()

    # Natychmiastowe usuwanie – pliki
    def delete_files_now(self, file_ids: List[str]):
        if not self.is_ready() or not file_ids:
            return
        for file_id in file_ids:
            print(f"--> Usuwam plik {file_id}...")
            attempts = 3
            for i in range(1, attempts + 1):
                try:
                    self.drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
                    print(f"✅ Usunięto plik {file_id}.")
                    break
                except Exception as e:
                    if i == attempts:
                        print(f"❌ Nie udało się usunąć pliku {file_id}: {e}")
                    else:
                        time.sleep(2)

    # Natychmiastowe usuwanie – folder (z opróżnieniem zawartości)
    def delete_folder_now(self, folder_id: str):
        if not self.is_ready() or not folder_id:
            return
        print(f"--> Usuwam folder {folder_id} wraz z zawartością...")
        try:
            page_token = None
            while True:
                query = f"'{folder_id}' in parents and trashed = false"
                resp = self.drive_service.files().list(
                    q=query,
                    fields='nextPageToken, files(id)',
                    pageSize=1000,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=page_token
                ).execute()
                for item in resp.get('files', []):
                    fid = item.get('id')
                    if not fid:
                        continue
                    with self._suppress_exc():
                        self.drive_service.files().delete(fileId=fid, supportsAllDrives=True).execute()
                page_token = resp.get('nextPageToken')
                if not page_token:
                    break
            attempts = 3
            for i in range(1, attempts + 1):
                try:
                    self.drive_service.files().delete(fileId=folder_id, supportsAllDrives=True).execute()
                    print(f"✅ Usunięto folder {folder_id}.")
                    return
                except Exception as e:
                    if i == attempts:
                        print(f"❌ Nie udało się usunąć folderu {folder_id}: {e}")
                    else:
                        time.sleep(2)
        except Exception as e:
            print(f"❌ Błąd podczas usuwania zawartości folderu {folder_id}: {e}")
