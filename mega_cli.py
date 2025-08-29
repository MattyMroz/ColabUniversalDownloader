# -*- coding: utf-8 -*-
"""
mega_cli.py — prosty skrypt CLI do pobierania z Mega.nz

Użycie (w Colab lub lokalnie):
- Pobierz plik:
  python mega_cli.py --url "https://mega.nz/file/..." --dest "/sciezka/docelowa"

- Pobierz folder (cały):
  python mega_cli.py --url "https://mega.nz/folder/..." --dest "/sciezka/docelowa"

- Pobierz folder (z interaktywnym wyborem plików):
  python mega_cli.py --url "https://mega.nz/folder/..." --choose --dest "/sciezka/docelowa"
"""

import argparse
from pathlib import Path
from typing import Optional

from utils.mega import MegaDownloader


def main():
    parser = argparse.ArgumentParser(description="Downloader Mega.nz (megatools)")
    parser.add_argument("--url", required=True, help="Link do pliku lub folderu Mega.nz")
    parser.add_argument("--dest", default=".", help="Katalog docelowy")
    parser.add_argument("--choose", action="store_true", help="Interaktywna selekcja plików w folderze")

    args = parser.parse_args()

    downloader = MegaDownloader()
    dest_dir: Optional[Path] = Path(args.dest).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    url = args.url

    def progress_printer(line: str):
        print(line, end="")

    if "/folder/" in url:
        print("Rozpoczynam pobieranie folderu...\n")
        results = downloader.download_folder(
            url,
            dest_dir=dest_dir,
            choose_files=args.choose,
            progress=progress_printer,
        )
        if results:
            print("\n✅ Zakończono. Pobrane pliki:")
            for p in results:
                print(f" - {p}")
        else:
            print("\n❌ Brak wykrytych plików lub wystąpił błąd.")
    else:
        print("Rozpoczynam pobieranie pliku...\n")
        result = downloader.download_file(url, dest_dir=dest_dir, progress=progress_printer)
        if result:
            print(f"\n✅ Zakończono. Plik: {result}")
        else:
            print("\n❌ Nie udało się pobrać pliku.")


if __name__ == "__main__":
    main()
