"""Pruebas de la capa de procesamiento, con mini-datasets de resultado conocido."""

import numpy as np
import pandas as pd

from src.processing import champions_league as cl
from src.processing import polymarket as pm


# --------------------------------------------------------------------------------------
# Champions League
# --------------------------------------------------------------------------------------
def _tabla_historica_cruda():
    """Dos filas con la forma del CSV de Kaggle, incluida la columna "goals" corrupta.

    Real Madrid: 1068 goles a favor y 535 en contra. Excel leyó "1068:535" como tiempo:
    535 minutos = 8 h 55 min, así que se guardó como "1076:55:00" (1068 + 8 horas acarreadas).
    Equipo chico: 3 a favor y 7 en contra, sin acarreo -> "3:07:00".
    """
    return pd.DataFrame({
        "Team": ["Real Madrid", "Equipo Chico"],
        "M.": [486, 4],
        "W": [291, 1],
        "D": [85, 0],
        "L": [110, 3],
        "goals": ["1076:55:00", "3:07:00"],
        "Dif": [533, -4],
        "Pt.": [533, -4],
    })


def test_reconstruccion_goals_con_acarreo():
    df = cl.clean_historical_performance(_tabla_historica_cruda())
    real = df[df["team"] == "Real Madrid"].iloc[0]
    assert real["goals_for"] == 1068
    assert real["goals_against"] == 535


def test_reconstruccion_goals_sin_acarreo():
    df = cl.clean_historical_performance(_tabla_historica_cruda())
    chico = df[df["team"] == "Equipo Chico"].iloc[0]
    assert chico["goals_for"] == 3
    assert chico["goals_against"] == 7


def test_historico_descarta_pt_y_renombra_columnas():
    df = cl.clean_historical_performance(_tabla_historica_cruda())
    assert list(df.columns) == [
        "team", "matches_played", "wins", "draws", "losses",
        "goals_for", "goals_against", "goal_difference",
    ]


def _partidos():
    return pd.DataFrame({
        "match_id": [1, 2, 3, 4],
        "season": [2026, 2026, 2026, 2026],
        "stage": "LEAGUE_STAGE",
        "matchday": [1, 2, 3, 4],
        "utc_date": ["2026-09-08", "2026-09-15", "2026-09-22", "2026-12-08"],
        "status": ["FINISHED", "FINISHED", "FINISHED", "TIMED"],
        "home_team": ["A", "B", "A", "B"],
        "away_team": ["B", "A", "C", "C"],
        "home_score": [2, 1, 0, np.nan],
        "away_score": [0, 1, 3, np.nan],
    })


def test_clean_api_matches_quita_duplicados_y_conserva_timed():
    duplicado = pd.concat([_partidos(), _partidos().iloc[[0]]], ignore_index=True)
    df = cl.clean_api_matches(duplicado)
    assert len(df) == 4  # el match_id repetido se elimina
    assert (df["status"] == "TIMED").sum() == 1  # el partido programado no se borra


def test_estadisticas_por_equipo():
    stats = cl.build_team_season_stats(_partidos()).set_index("team")

    # A: ganó 2-0 a B, empató 1-1 con B, perdió 0-3 con C (el partido TIMED no cuenta)
    assert stats.loc["A", "matches_played"] == 3
    assert stats.loc["A", "wins"] == 1
    assert stats.loc["A", "goals_for_avg"] == (2 + 1 + 0) / 3
    assert stats.loc["A", "goals_against_avg"] == (0 + 1 + 3) / 3
    assert stats.loc["A", "win_pct"] == 1 / 3
    # C solo jugó un partido terminado (el TIMED contra B no cuenta)
    assert stats.loc["C", "matches_played"] == 1
    assert stats.loc["C", "win_pct"] == 1.0


def test_equipos_sin_historial_reciente_usan_promedio_general():
    stats = cl.build_team_season_stats(_partidos())
    historico = pd.DataFrame({
        "team": ["A", "Z"],
        "matches_played": [10, 10],
        "goals_for": [15, 25],
        "goals_against": [20, 20],
    })
    resultado = cl.add_new_team_feature(stats, historico)
    z = resultado[resultado["team"] == "Z"].iloc[0]
    assert z["tiene_historial_reciente"] == 0
    assert z["goals_for_avg"] == 40 / 20  # promedio de todos los equipos históricos
    assert pd.isna(z["win_pct"])


# --------------------------------------------------------------------------------------
# Polymarket
# --------------------------------------------------------------------------------------
def test_normalizar_nombre():
    assert pm.normalizar_nombre("FK Bodø/Glimt") == "bodo glimt"
    assert pm.normalizar_nombre("Club Brugge KV") == "brugge"
    assert pm.normalizar_nombre("Atlético Madrid") == "atletico madrid"
    assert pm.normalizar_nombre(None) == ""


def test_tipo_mercado():
    assert pm._tipo_mercado("UEFA Champions League: 2027 Champion") == ("campeon", 1.0)
    assert pm._tipo_mercado("UEFA Champions League: Team to Finish Top 8 in League Phase") == ("top_8", 8.0)
    tipo, esperados = pm._tipo_mercado("Algo que no reconoce")
    assert tipo == "otro" and np.isnan(esperados)


def _mercados_crudos():
    base = {
        "fetched_at": "2026-10-21T22:16:10+00:00", "event_id": 659671,
        "event_title": "UEFA Champions League: 2027 Champion", "outcomes": '["Yes", "No"]',
        "last_trade_price": 0.18, "best_ask": 0.19, "liquidity": 100.0, "closed": "False",
        "end_date": "2027-05-30T23:59:00Z",
    }
    filas = [
        {**base, "market_id": 1, "question": "Will Arsenal win the 2026-27 UEFA Champions League?",
         "outcome_prices": '["0.14", "0.86"]', "best_bid": 0.13, "volume": 500.0, "active": "True"},
        {**base, "market_id": 2, "question": "Will Roma win the 2026-27 UEFA Champions League?",
         "outcome_prices": '["0.02", "0.98"]', "best_bid": np.nan, "volume": np.nan, "active": "True"},
        # Mercado "de relleno": inactivo, precio por defecto 0.5
        {**base, "market_id": 3, "question": "Will Team A win the 2026-27 UEFA Champions League?",
         "outcome_prices": '["0.5", "0.5"]', "best_bid": np.nan, "volume": np.nan, "active": "False"},
    ]
    return pd.DataFrame(filas)


def test_limpiar_mercados_quita_relleno_y_marca_faltantes():
    df = pm.limpiar_mercados(_mercados_crudos()).set_index("market_id")

    assert 3 not in df.index  # el mercado inactivo se elimina
    assert df.loc[1, "prob_implicita"] == 0.14
    assert df.loc[1, "equipo"] == "Arsenal"
    # best_bid vacío se conserva vacío y se marca; volume vacío se imputa como 0 y se marca
    assert df.loc[2, "sin_ofertas_compra"] and pd.isna(df.loc[2, "best_bid"])
    assert df.loc[2, "volumen_imputado"] and df.loc[2, "volume"] == 0
    assert not df.loc[1, "sin_ofertas_compra"]
