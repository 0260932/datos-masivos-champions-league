"""Pruebas de src/validation/validators.py.

Cada validación se prueba dos veces: con datos válidos (debe pasar) y con un
error metido a propósito (debe detectarlo).
"""

import numpy as np
import pandas as pd

from src.validation import validators as v


def _df():
    return pd.DataFrame({
        "match_id": [1, 2, 3],
        "equipo": ["Arsenal", "Roma", "Napoli"],
        "goles": [0, 2, 5],
        "precio": [0.1, 0.5, 0.9],
        "status": ["FINISHED", "FINISHED", "TIMED"],
        "fecha": ["2026-09-08", "2026-09-15", "2026-09-22"],
    })


def test_columnas_obligatorias():
    assert v.validate_required_columns(_df(), ["match_id", "goles"]).ok
    r = v.validate_required_columns(_df(), ["match_id", "no_existe"])
    assert not r.ok and "no_existe" in r.detalle


def test_tipos_de_datos():
    assert v.validate_dtypes(_df(), {"match_id": "int", "precio": "float", "equipo": "str"}).ok
    assert not v.validate_dtypes(_df(), {"equipo": "numeric"}).ok
    assert not v.validate_dtypes(_df(), {"no_existe": "int"}).ok


def test_sin_faltantes():
    assert v.validate_no_missing(_df(), ["goles"]).ok
    df = _df()
    df.loc[1, "goles"] = np.nan
    r = v.validate_no_missing(df, ["goles"])
    assert not r.ok and "'goles': 1" in r.detalle


def test_detecta_duplicado_metido_a_proposito():
    assert v.validate_no_duplicates(_df(), subset=["match_id"]).ok
    con_duplicado = pd.concat([_df(), _df().iloc[[0]]], ignore_index=True)
    assert not v.validate_no_duplicates(con_duplicado, subset=["match_id"]).ok
    assert not v.validate_no_duplicates(con_duplicado).ok  # fila completa repetida


def test_rangos():
    assert v.validate_value_ranges(_df(), "precio", 0, 1).ok
    df = _df()
    df.loc[0, "precio"] = 1.3
    assert not v.validate_value_ranges(df, "precio", 0, 1).ok
    df.loc[0, "goles"] = -1
    assert not v.validate_value_ranges(df, "goles", min_value=0).ok


def test_rangos_ignoran_vacios():
    df = _df()
    df.loc[0, "precio"] = np.nan
    assert v.validate_value_ranges(df, "precio", 0, 1).ok


def test_valores_permitidos():
    assert v.validate_allowed_values(_df(), "status", ["FINISHED", "TIMED"]).ok
    assert not v.validate_allowed_values(_df(), "status", ["FINISHED"]).ok


def test_regla_por_fila():
    df = pd.DataFrame({"wins": [2, 1], "draws": [1, 0], "losses": [0, 1], "matches_played": [3, 3]})
    r = v.validate_row_rule(df, "w+d+l=mp", lambda d: d["wins"] + d["draws"] + d["losses"] == d["matches_played"])
    assert not r.ok and "1 de 2" in r.detalle
    df.loc[1, "matches_played"] = 2
    assert v.validate_row_rule(df, "w+d+l=mp", lambda d: d["wins"] + d["draws"] + d["losses"] == d["matches_played"]).ok


def test_consistencia_temporal():
    assert v.validate_temporal_consistency(_df(), "fecha", min_date="2026-01-01", max_date="2026-12-31").ok
    df = _df()
    df.loc[0, "fecha"] = "2099-01-01"  # fecha futura absurda
    assert not v.validate_temporal_consistency(df, "fecha").ok
    df.loc[0, "fecha"] = "no es fecha"
    r = v.validate_temporal_consistency(df, "fecha")
    assert not r.ok and "no interpretables" in r.detalle


def test_consistencia_temporal_con_timestamp_unix():
    df = pd.DataFrame({"t": [1783036810, 1792620804]})  # julio y octubre de 2026
    assert v.validate_temporal_consistency(df, "t", min_date="2026-01-01", max_date="2026-12-31").ok


def test_consistencia_entre_tablas():
    a = pd.DataFrame({"equipo": ["Arsenal", "Roma"]})
    b = pd.DataFrame({"equipo": ["Arsenal", "Roma", "Napoli"]})
    assert v.validate_cross_table_consistency(a, b, "equipo").ok
    a.loc[2] = ["Equipo Inventado"]
    r = v.validate_cross_table_consistency(a, b, "equipo")
    assert not r.ok and "Equipo Inventado" in r.detalle
    # Con tolerancia: 2 de 3 coinciden (66.7%) >= 60%
    assert v.validate_cross_table_consistency(a, b, "equipo", min_coverage=0.6).ok


def test_volumen_esperado():
    assert v.validate_expected_volume(_df(), min_rows=1, max_rows=10).ok
    assert not v.validate_expected_volume(_df(), min_rows=100).ok
    assert not v.validate_expected_volume(_df(), min_rows=1, max_rows=2).ok


def test_outcome_prices_suma_uno():
    ok = pd.DataFrame({"outcome_prices": ['["0.185", "0.815"]', '["0.0045", "0.9955"]']})
    assert v.validate_outcome_prices_sum(ok).ok
    mal = pd.DataFrame({"outcome_prices": ['["0.6", "0.6"]', "texto roto"]})
    r = v.validate_outcome_prices_sum(mal)
    assert not r.ok and "1 no interpretables" in r.detalle


def test_suma_por_grupo():
    df = pd.DataFrame({
        "tipo": ["campeon", "top_8"],
        "suma": [1.05, 8.6],
        "esperado": [1.0, 8.0],
    })
    assert v.validate_group_sum(df, "tipo", "suma", "esperado", relative_tolerance=0.10).ok
    df.loc[1, "suma"] = 12.0  # 50% por encima de lo esperado
    assert not v.validate_group_sum(df, "tipo", "suma", "esperado").ok
    df.loc[1, ["suma", "esperado"]] = [8.0, np.nan]  # grupo sin valor esperado
    r = v.validate_group_sum(df, "tipo", "suma", "esperado")
    assert not r.ok and "sin valor esperado" in r.detalle
