"""Pruebas de la capa de ingestión.

No llaman a ninguna API real: usan respuestas de ejemplo fijas (diccionarios),
para que las pruebas corran sin internet, sin credenciales y siempre igual.
"""

from unittest.mock import MagicMock, patch

import pandas as pd

from src.ingestion import download_polymarket, extract_api_football_data

# Respuesta de ejemplo con la misma forma que la de football-data.org (/v4/competitions/CL/matches)
PAYLOAD_API = {
    "matches": [
        {
            "id": 575323,
            "season": {"startDate": "2026-07-01"},
            "stage": "LEAGUE_STAGE",
            "matchday": 1,
            "utcDate": "2026-09-08T16:45:00Z",
            "status": "FINISHED",
            "homeTeam": {"name": "Club Brugge KV"},
            "awayTeam": {"name": "Aston Villa FC"},
            "score": {"fullTime": {"home": 2, "away": 3}},
        },
        {
            "id": 575400,
            "season": {"startDate": "2026-07-01"},
            "stage": "LEAGUE_STAGE",
            "matchday": 6,
            "utcDate": "2026-12-08T20:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Arsenal FC"},
            "awayTeam": {"name": "AS Roma"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
    ]
}

COLUMNAS_PARTIDOS = [
    "match_id", "season", "stage", "matchday", "utc_date",
    "status", "home_team", "away_team", "home_score", "away_score",
]


def _respuesta_falsa(payload):
    respuesta = MagicMock()
    respuesta.json.return_value = payload
    respuesta.raise_for_status.return_value = None
    return respuesta


def test_fetch_matches_produce_columnas_esperadas():
    with patch.object(extract_api_football_data, "get_api_token", return_value="token-de-prueba"), \
         patch.object(extract_api_football_data.requests, "get", return_value=_respuesta_falsa(PAYLOAD_API)):
        df = extract_api_football_data.fetch_matches()

    assert list(df.columns) == COLUMNAS_PARTIDOS
    assert len(df) == 2


def test_fetch_matches_parsea_valores():
    with patch.object(extract_api_football_data, "get_api_token", return_value="token-de-prueba"), \
         patch.object(extract_api_football_data.requests, "get", return_value=_respuesta_falsa(PAYLOAD_API)):
        df = extract_api_football_data.fetch_matches()

    primero = df.iloc[0]
    assert primero["match_id"] == 575323
    assert primero["season"] == "2026"  # año de inicio de la temporada (startDate[:4])
    assert primero["home_team"] == "Club Brugge KV"
    assert primero["home_score"] == 2 and primero["away_score"] == 3
    # Un partido programado (TIMED) aún no tiene marcador
    assert df.iloc[1]["status"] == "TIMED"
    assert df[["home_score", "away_score"]].iloc[1].isna().all()


def test_fetch_matches_respuesta_vacia():
    with patch.object(extract_api_football_data, "get_api_token", return_value="token-de-prueba"), \
         patch.object(extract_api_football_data.requests, "get", return_value=_respuesta_falsa({})):
        df = extract_api_football_data.fetch_matches()
    assert df.empty


# Respuesta de ejemplo con la forma de la Gamma API de Polymarket (/public-search)
EVENTOS_POLYMARKET = [
    {
        "id": "659671",
        "title": "UEFA Champions League: 2027 Champion",
        "markets": [
            {"id": "2772166", "question": "Will Paris Saint-Germain win the 2026-27 UEFA Champions League?",
             "outcomes": '["Yes", "No"]', "outcomePrices": '["0.185", "0.815"]', "lastTradePrice": 0.18,
             "bestBid": 0.18, "bestAsk": 0.19, "volume": "1164081.2", "liquidity": "502271.57",
             "active": True, "closed": False, "endDate": "2027-05-30T23:59:00Z"},
            {"id": "2772190", "question": "Will Sporting CP win the 2026-27 UEFA Champions League?",
             "outcomes": '["Yes", "No"]', "outcomePrices": '["0.0045", "0.9955"]',
             "active": True, "closed": False},
        ],
    },
    {"id": "988995", "title": "UEFA Champions League: 2027 2nd Place", "markets": []},
]


def test_flatten_markets_un_renglon_por_mercado():
    df = download_polymarket.flatten_markets(EVENTOS_POLYMARKET, fetched_at="2026-10-21T22:16:10+00:00")

    assert len(df) == 2  # el segundo evento no tiene mercados
    assert {"event_id", "event_title", "market_id", "question", "outcome_prices", "best_bid"} <= set(df.columns)
    assert (df["event_title"] == "UEFA Champions League: 2027 Champion").all()
    assert df.loc[0, "outcome_prices"] == '["0.185", "0.815"]'


def test_flatten_markets_campos_faltantes_quedan_vacios():
    df = download_polymarket.flatten_markets(EVENTOS_POLYMARKET, fetched_at="2026-10-21T22:16:10+00:00")
    # El segundo mercado no trae bestBid ni volume: deben quedar vacíos, no inventarse
    assert pd.isna(df.loc[1, "best_bid"])
    assert pd.isna(df.loc[1, "volume"])
