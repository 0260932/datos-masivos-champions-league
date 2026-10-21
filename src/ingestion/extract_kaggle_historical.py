"""
Fuente 2 (descarga automatizada): dataset historico de Kaggle
"UEFA Champions League Historical Dataset 1955-2023"
https://www.kaggle.com/datasets/fardifaalam170041060/champions-league-dataset-1955-2023

Se descarga por codigo con kagglehub, usando credenciales por variables
de entorno (KAGGLE_USERNAME / KAGGLE_KEY), no un archivo kaggle.json
con rutas personales dentro del repositorio.

NOTA (Entrega 2): la correccion de la columna "goals" (que en el archivo
original de Kaggle quedo en formato de hora, ej. "1076:55:00" en vez de
"1068:535") ya NO se hace aqui. Esta es una tarea de PROCESAMIENTO, no de
ingestion, y se construye en src/processing/ (ver issue de Integrante 2).
Este script unicamente descarga y guarda copias crudas en data/raw/.

Ejecucion:
    python -m src.ingestion.download_data
"""

import logging
import os
import shutil
from datetime import date
from pathlib import Path

import kagglehub
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "data" / "raw"
LOGS_DIR = ROOT_DIR / "logs"
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

KAGGLE_DATASET = "fardifaalam170041060/champions-league-dataset-1955-2023"
PERFORMANCE_FILE = "UCL_AllTime_Performance_Table.csv"

logger = logging.getLogger(__name__)


def download_dataset():
    load_dotenv(ROOT_DIR / ".env")
    if not os.getenv("KAGGLE_USERNAME") or not os.getenv("KAGGLE_KEY"):
        raise RuntimeError(
            "Faltan KAGGLE_USERNAME / KAGGLE_KEY en tu .env."
        )

    logger.info("Descargando dataset de Kaggle: %s", KAGGLE_DATASET)
    cache_path = Path(kagglehub.dataset_download(KAGGLE_DATASET))

    copied = []
    for csv_file in cache_path.glob("*.csv"):
        dest = RAW_DIR / csv_file.name
        shutil.copy(csv_file, dest)
        copied.append(dest)
        logger.info("Guardado: %s", dest)

    if not copied:
        logger.error("No se encontraron archivos .csv en %s", cache_path)
        raise RuntimeError(f"No se encontraron archivos .csv en {cache_path}")
    return copied


def main(force=False):
    ref_path = RAW_DIR / PERFORMANCE_FILE

    if ref_path.exists() and not force:
        modificado = date.fromtimestamp(ref_path.stat().st_mtime)
        if modificado == date.today():
            logger.info(
                "Se omite descarga: %s ya existe y es de hoy (usa force=True para forzar).",
                ref_path,
            )
            print(f"Ya existe una copia de hoy: {ref_path} (se omite descarga)")
            return [ref_path]

    try:
        files = download_dataset()
    except Exception:
        logger.exception("Error al descargar el dataset de Kaggle")
        raise

    for f in files:
        print(f"Guardado: {f}")
    return files


if __name__ == "__main__":
    main()
