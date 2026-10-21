"""
Fuente 1 (obtenida mediante codigo): API de football-data.org
Descarga partidos de la UEFA Champions League (codigo de competicion "CL").

Requiere la variable de entorno FOOTBALL_DATA_API_TOKEN (ver .env.example).
Usa rutas relativas a la raiz del repo, no rutas personales de ninguna
computadora.

Ejecucion:
    python -m src.ingestion.download_data
"""

import logging
import os
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "data" / "raw"
LOGS_DIR = ROOT_DIR / "logs"
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

API_BASE_URL = "https://api.football-data.org/v4"
COMPETITION_CODE = "CL"  # UEFA Champions League

logger = logging.getLogger(__name__)


def get_api_token():
    load_dotenv(ROOT_DIR / ".env")
    token = os.getenv("FOOTBALL_DATA_API_TOKEN")
    if not token:
        raise RuntimeError(
            "No se encontro FOOTBALL_DATA_API_TOKEN. Revisa tu archivo .env."
        )
    return token


def fetch_matches(season=None):
    token = get_api_token()
    headers = {"X-Auth-Token": token}
    url = f"{API_BASE_URL}/competitions/{COMPETITION_CODE}/matches"
    params = {}
    if season is not None:
        params["season"] = season

    logger.info("Solicitando partidos a la API (season=%s)", season)
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    matches = payload.get("matches", [])
    rows = []
    for m in matches:
        rows.append({
            "match_id": m.get("id"),
            "season": m.get("season", {}).get("startDate", "")[:4],
            "stage": m.get("stage"),
            "matchday": m.get("matchday"),
            "utc_date": m.get("utcDate"),
            "status": m.get("status"),
            "home_team": m.get("homeTeam", {}).get("name"),
            "away_team": m.get("awayTeam", {}).get("name"),
            "home_score": m.get("score", {}).get("fullTime", {}).get("home"),
            "away_score": m.get("score", {}).get("fullTime", {}).get("away"),
        })
    logger.info("Recibidos %d partidos de la API", len(rows))
    return pd.DataFrame(rows)


def main(force=False):
    out_path = RAW_DIR / "champions_league_matches_api.csv"

    if out_path.exists() and not force:
        modificado = date.fromtimestamp(out_path.stat().st_mtime)
        if modificado == date.today():
            logger.info(
                "Se omite descarga: %s ya existe y es de hoy (usa force=True para forzar).",
                out_path,
            )
            print(f"Ya existe una copia de hoy: {out_path} (se omite descarga)")
            return out_path

    try:
        df = fetch_matches()
    except Exception:
        logger.exception("Error al descargar partidos de la API de football-data.org")
        raise

    df.to_csv(out_path, index=False)
    logger.info("Guardado: %s (%d filas)", out_path, len(df))
    print(f"Guardado: {out_path} ({len(df)} filas)")
    print(df.head())
    print(df.dtypes)
    return out_path


if __name__ == "__main__":
    main()
