"""
Fuente 3 (Entrega 2, obtenida mediante codigo): mercados publicos de
Polymarket relacionados con la UEFA Champions League.

Usa el endpoint publico de busqueda de Polymarket (Gamma API), que no
requiere credenciales ni API key:
    https://gamma-api.polymarket.com/public-search

Guarda dos copias crudas en data/raw/:
    - polymarket_events_raw.json  (respuesta cruda completa, trazabilidad)
    - polymarket_markets.csv      (version aplanada: un renglon por mercado)

Ejecucion:
    python -m src.ingestion.download_polymarket
    python -m src.ingestion.download_polymarket --force
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "data" / "raw"
LOGS_DIR = ROOT_DIR / "logs"
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
SEARCH_TERM = "Champions League"

RAW_JSON_FILE = RAW_DIR / "polymarket_events_raw.json"
MARKETS_CSV_FILE = RAW_DIR / "polymarket_markets.csv"

logger = logging.getLogger(__name__)


def fetch_events(query=SEARCH_TERM):
    logger.info("Buscando eventos de Polymarket con query=%r", query)
    response = requests.get(SEARCH_URL, params={"q": query}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    eventos = payload.get("events", [])
    logger.info("Recibidos %d eventos de Polymarket", len(eventos))
    return eventos


def flatten_markets(eventos, fetched_at):
    filas = []
    for ev in eventos:
        for m in ev.get("markets", []):
            filas.append({
                "fetched_at": fetched_at,
                "event_id": ev.get("id"),
                "event_title": ev.get("title"),
                "market_id": m.get("id"),
                "question": m.get("question"),
                "outcomes": m.get("outcomes"),
                "outcome_prices": m.get("outcomePrices"),
                "last_trade_price": m.get("lastTradePrice"),
                "best_bid": m.get("bestBid"),
                "best_ask": m.get("bestAsk"),
                "volume": m.get("volume"),
                "liquidity": m.get("liquidity"),
                "active": m.get("active"),
                "closed": m.get("closed"),
                "end_date": m.get("endDate"),
            })
    return pd.DataFrame(filas)


def main(force=False):
    if RAW_JSON_FILE.exists() and not force:
        modificado = date.fromtimestamp(RAW_JSON_FILE.stat().st_mtime)
        if modificado == date.today():
            logger.info(
                "Se omite descarga: %s ya existe y es de hoy (usa force=True para forzar).",
                RAW_JSON_FILE,
            )
            print(f"Ya existe una copia de hoy: {RAW_JSON_FILE} (se omite descarga)")
            return RAW_JSON_FILE, MARKETS_CSV_FILE

    try:
        eventos = fetch_events()
        fetched_at = datetime.now(timezone.utc).isoformat()

        with open(RAW_JSON_FILE, "w") as f:
            json.dump({"fetched_at": fetched_at, "events": eventos}, f, indent=2)
        logger.info("Guardado: %s", RAW_JSON_FILE)

        df = flatten_markets(eventos, fetched_at)
        df.to_csv(MARKETS_CSV_FILE, index=False)
        logger.info("Guardado: %s (%d filas)", MARKETS_CSV_FILE, len(df))
    except Exception:
        logger.exception("Error al descargar datos de Polymarket")
        raise

    print(f"Guardado: {RAW_JSON_FILE}")
    print(f"Guardado: {MARKETS_CSV_FILE} ({len(df)} filas)")
    print(df.head())
    return RAW_JSON_FILE, MARKETS_CSV_FILE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingestion de datos de Polymarket (Champions League)")
    parser.add_argument("--force", action="store_true", help="Forzar re-descarga aunque ya exista copia de hoy")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / "ingestion.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    main(force=args.force)
