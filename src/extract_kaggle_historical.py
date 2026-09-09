"""
Fuente 2 (descarga automatizada): dataset historico de Kaggle
"UEFA Champions League Historical Dataset 1955-2023"
https://www.kaggle.com/datasets/fardifaalam170041060/champions-league-dataset-1955-2023

Se descarga por codigo con kagglehub, usando credenciales por variables
de entorno (KAGGLE_USERNAME / KAGGLE_KEY), no un archivo kaggle.json
con rutas personales dentro del repositorio.
"""

import os
import shutil
from pathlib import Path

import kagglehub
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

KAGGLE_DATASET = "fardifaalam170041060/champions-league-dataset-1955-2023"


def download_dataset():
    load_dotenv(ROOT_DIR / ".env")
    if not os.getenv("KAGGLE_USERNAME") or not os.getenv("KAGGLE_KEY"):
        raise RuntimeError(
            "Faltan KAGGLE_USERNAME / KAGGLE_KEY en tu .env."
        )

    cache_path = Path(kagglehub.dataset_download(KAGGLE_DATASET))

    copied = []
    for csv_file in cache_path.glob("*.csv"):
        dest = RAW_DIR / csv_file.name
        shutil.copy(csv_file, dest)
        copied.append(dest)

    if not copied:
        raise RuntimeError(f"No se encontraron archivos .csv en {cache_path}")
    return copied


def main():
    files = download_dataset()
    for f in files:
        print(f"Guardado: {f}")


if __name__ == "__main__":
    main()
