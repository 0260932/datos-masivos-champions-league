"""
Punto de entrada de ingestion para los datos de Champions League.

Ejecucion:
    python -m src.ingestion.download_data
    python -m src.ingestion.download_data --force   # fuerza re-descarga aunque ya exista copia de hoy

Obtiene datos de las dos fuentes (API de football-data.org y dataset de
Kaggle), guarda copias crudas en data/raw/, evita descargas innecesarias
si ya existe una copia de hoy, y registra todo (incluyendo errores) en
logs/ingestion.log para trazabilidad. No requiere ni expone credenciales:
se leen desde variables de entorno (.env, no versionado).
"""

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "ingestion.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

from src.ingestion import extract_api_football_data, extract_kaggle_historical


def main(force=False):
    resultados = {"api_football_data": False, "kaggle_historical": False}

    logger.info("=== Inicio de ingestion de datos (Champions League) ===")

    try:
        extract_api_football_data.main(force=force)
        resultados["api_football_data"] = True
    except Exception:
        logger.exception("Fallo la ingestion de la fuente API (football-data.org)")

    try:
        extract_kaggle_historical.main(force=force)
        resultados["kaggle_historical"] = True
    except Exception:
        logger.exception("Fallo la ingestion de la fuente Kaggle")

    logger.info("=== Fin de ingestion. Resultado: %s ===", resultados)

    if not any(resultados.values()):
        logger.error("Ninguna fuente se pudo descargar.")
        sys.exit(1)

    return resultados


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingestion de datos de Champions League")
    parser.add_argument("--force", action="store_true", help="Forzar re-descarga aunque ya exista copia de hoy")
    args = parser.parse_args()
    main(force=args.force)
