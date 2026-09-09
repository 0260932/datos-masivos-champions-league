"""
Fuente 1 (obtenida mediante código): API de football-data.org
Descarga partidos de la UEFA Champions League (código de competición "CL").

Requiere la variable de entorno FOOTBALL_DATA_API_TOKEN (ver .env.example).
Usa rutas relativas a la raíz del repo, no rutas personales de ninguna
computadora.
"""

import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

API_BASE_URL = "https://api.football-data.org/v4"
COMPETITION_CODE = "CL"  # UEFA Champions League


def get_api_token():
    load_dotenv(ROOT_DIR / ".env")
    token = os.getenv("FOOTBALL_DATA_API_TOKEN")
    if not token:
        raise RuntimeError(
            "No se encontró FOOTBALL_DATA_API_TOKEN. Revisa tu archivo .env."
        )
    return token


def fetch_matches():
    token = get_api_token()
    headers = {"X-Auth-Token": token}
    url = f"{API_BASE_URL}/competitions/{COMPETITION_CODE}/matches"

    response = requests.get(url, headers=headers, timeout=30)
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
    return pd.DataFrame(rows)


def main():
    df = fetch_matches()
    out_path = RAW_DIR / "champions_league_matches_api.csv"
    df.to_csv(out_path, index=False)
    print(f"Guardado: {out_path} ({len(df)} filas)")
    print(df.head())
    print(df.dtypes)


if __name__ == "__main__":
    main()
