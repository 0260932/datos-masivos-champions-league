"""Pruebas de la plataforma en vivo (xG, ritmo, GARCH, senales y ligas), sin llamadas a internet."""

from datetime import datetime, timedelta, timezone

import numpy as np

from src.live import modelo, tracker
from src.live.xg_vivo import parse_summary, resumen_hasta, xg_tiro
from src.models.garch_mercado import malla_regular, volatilidad


# --------------------------------------------------------------------------------------
# xG por tiro (textos reales del comentario de ESPN)
# --------------------------------------------------------------------------------------
def test_xg_por_zona():
    assert xg_tiro("Attempt saved. Diego Gómez (Brighton) right footed shot from the centre of the box "
                   "is saved in the centre of the goal.") == 0.12
    assert xg_tiro("Attempt missed. Nobel Mendy (Hull City) header from the centre of the box is high.") == 0.08
    assert xg_tiro("Attempt missed. X (Hull City) left footed shot from outside the box misses.") == 0.03
    assert xg_tiro("Goal! X (Team) right footed shot from the six yard box to the bottom right corner.") == 0.30
    assert xg_tiro("Goal! X (Team) converts the penalty with a right footed shot.") == 0.76


def _summary_minimo():
    inicio = datetime(2026, 11, 22, 14, 0, tzinfo=timezone.utc)

    def jugada(tipo, equipo, minuto, texto):
        return {"text": texto, "play": {
            "type": {"type": tipo}, "team": {"displayName": equipo}, "clock": {"value": minuto * 60},
            "wallclock": (inicio + timedelta(minutes=minuto)).isoformat().replace("+00:00", "Z")}}

    return {
        "header": {"competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "1", "displayName": "Local FC"}},
            {"homeAway": "away", "team": {"id": "2", "displayName": "Visita FC"}},
        ]}]},
        "commentary": [
            jugada("shot-on-target", "Local FC", 10, "Attempt saved. A shot from the centre of the box."),
            jugada("goal", "Visita FC", 20, "Goal! B converts the penalty with a right footed shot."),
            jugada("shot-off-target", "Local FC", 60, "Attempt missed. C shot from outside the box."),
            jugada("foul", "Local FC", 61, "Foul by D."),
        ],
        "keyEvents": [{"type": {"type": "red-card"}, "team": {"id": "1"}, "clock": {"value": 3000},
                       "wallclock": (inicio + timedelta(minutes=50)).isoformat().replace("+00:00", "Z")}],
    }


def test_parse_summary_y_resumen():
    datos = parse_summary(_summary_minimo())
    assert len(datos["tiros"]) == 3  # la falta no cuenta
    final = resumen_hasta(datos)
    assert {k: final["home"][k] for k in ("xg", "npxg", "tiros", "a_puerta", "rojas")} == {
        "xg": 0.15, "npxg": 0.15, "tiros": 2, "a_puerta": 1, "rojas": 1}
    assert final["away"]["xg"] == 0.76 and final["away"]["npxg"] == 0.0  # el penal no cuenta en npxG
    assert resumen_hasta(datos, minuto=15)["home"]["tiros"] == 1


def test_geometria_del_tiro():
    from src.models.xg_modelo import geometria
    cerca, angulo_cerca = geometria(95, 50)   # ~5 m de frente
    lejos, angulo_lejos = geometria(75, 50)   # ~26 m de frente
    esquina, angulo_esquina = geometria(99, 95)  # junto al banderin
    assert cerca < lejos and angulo_cerca > angulo_lejos > angulo_esquina


def test_momentum_favorece_al_que_presiona_reciente():
    from src.live.xg_vivo import momentum_serie, presion
    datos = {"tiros": [
        {"lado": "away", "minuto": 10, "xg": 0.30, "penal": False},
        {"lado": "home", "minuto": 55, "xg": 0.20, "penal": False},
        {"lado": "home", "minuto": 58, "xg": 0.15, "penal": False},
    ], "corners": [{"lado": "home", "minuto": 59}], "rojas": []}
    assert presion(datos, "home", 60) > presion(datos, "away", 60)
    serie = dict(momentum_serie(datos, 60))
    assert serie[12] < 0 < serie[60]  # al inicio presiona la visita, al final el local


def test_momentum_ladea_el_ritmo():
    base = {"xg": 0.5, "npxg": 0.5, "rojas": 0}
    xg = {"home": dict(base, presion=0.9), "away": dict(base, presion=0.1)}
    lh, la = modelo.ritmo_en_vivo(1.2, 1.2, 40, xg, peso_momentum=1.0)
    lh0, la0 = modelo.ritmo_en_vivo(1.2, 1.2, 40, xg, peso_momentum=0.0)
    assert lh > lh0 and la < la0


# --------------------------------------------------------------------------------------
# Ritmo en vivo
# --------------------------------------------------------------------------------------
def test_ritmo_sin_xg_es_el_previo():
    assert modelo.ritmo_en_vivo(1.4, 1.1, 30, None) == (1.4, 1.1)


def test_ritmo_sube_con_ocasiones_y_baja_con_roja():
    sin_nada = {"xg": 0.0, "npxg": 0.0, "rojas": 0}
    domina = {"home": {"xg": 1.5, "npxg": 1.5, "rojas": 0}, "away": dict(sin_nada)}
    lh, la = modelo.ritmo_en_vivo(1.2, 1.2, 30, domina)
    assert lh > 1.2 > la
    roja = {"home": dict(sin_nada, rojas=1), "away": dict(sin_nada)}
    lh, la = modelo.ritmo_en_vivo(1.2, 1.2, 0, roja)
    assert np.isclose(lh, 1.2 * 0.70) and np.isclose(la, 1.2 * 1.20)


def test_xg_adelanta_la_probabilidad():
    """Con 0-0 al 60', el equipo que genera las ocasiones debe subir antes de anotar."""
    xg = {"home": {"xg": 1.8, "npxg": 1.8, "rojas": 0}, "away": {"xg": 0.1, "npxg": 0.1, "rojas": 0}}
    sin = modelo.live_probabilities(1.3, 1.3, 60, 0, 0)
    con = modelo.live_probabilities(*modelo.ritmo_en_vivo(1.3, 1.3, 60, xg), 60, 0, 0)
    assert con[0] > sin[0] and con[2] < sin[2]


# --------------------------------------------------------------------------------------
# GARCH del mercado
# --------------------------------------------------------------------------------------
def test_garch_respaldo_con_pocos_datos():
    resultado = volatilidad([0.50, 0.52, 0.51, 0.53])
    assert resultado["metodo"] == "ewma" and resultado["ruido"] > 0


def test_garch_ignora_el_salto_de_un_gol():
    rng = np.random.default_rng(0)
    precios = list(0.45 + rng.normal(0, 0.003, 150).cumsum() * 0.2)
    tras_gol = volatilidad(precios + [0.08])
    assert tras_gol["agitado"] and tras_gol["ruido"] <= 0.10


def test_malla_regular():
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    puntos = [(t0, 0.5), (t0 + timedelta(minutes=12), 0.6)]
    assert malla_regular(puntos, 5) == [0.5, 0.5, 0.5, 0.6]


# --------------------------------------------------------------------------------------
# Senales y ligas
# --------------------------------------------------------------------------------------
def _partido(minuto, gl=0, gv=0, status="IN_PLAY"):
    return {"home": "Local FC", "away": "Visita FC", "status": status, "minute": minuto,
            "home_goals": gl, "away_goals": gv}


def test_senal_bueno_y_mercado_agitado():
    precios = {"home": {"ask": 0.30}, "draw": {"ask": 0.30}, "away": {"ask": 0.40}}
    filas = tracker.evaluate(_partido(80, 1, 0), (1.3, 1.1), precios)
    assert filas[0]["senal"] == "BUENO"  # gana 1-0 al 80' y el mercado lo vende en 30%
    agitado = {"home": {"ruido": 0.02, "agitado": True, "metodo": "garch"}}
    filas = tracker.evaluate(_partido(80, 1, 0), (1.3, 1.1), precios, vol=agitado)
    assert filas[0]["senal"] == "ESPERAR"


def test_interruptor_del_xg_en_la_senal():
    """Con usar_xg la senal usa p_con_xg; sin el, la del modelo sin xG. Ambas se reportan."""
    precios = {"home": {"ask": 0.30}, "draw": {"ask": 0.30}, "away": {"ask": 0.40}}
    xg = {"home": {"xg": 2.0, "npxg": 2.0, "rojas": 0, "presion": 0.8},
          "away": {"xg": 0.1, "npxg": 0.1, "rojas": 0, "presion": 0.1}}
    sin = tracker.evaluate(_partido(60), (1.3, 1.1), precios, xg, usar_xg=False)
    assert sin[0]["p_modelo"] == sin[0]["p_sin_xg"] < sin[0]["p_con_xg"]
    con = tracker.evaluate(_partido(60), (1.3, 1.1), precios, xg, usar_xg=True)
    assert con[0]["p_modelo"] == con[0]["p_con_xg"]
    assert tracker.USAR_XG_EN_SENAL  # la plataforma predice con xG y momentum


def test_factores_explican_la_probabilidad():
    """Previa + lo que suma cada factor = probabilidad del modelo; la decision viene en una frase."""
    precios = {"home": {"ask": 0.30}, "draw": {"ask": 0.30}, "away": {"ask": 0.40}}
    xg = {"home": {"xg": 1.8, "npxg": 1.8, "rojas": 0, "presion": 0.9},
          "away": {"xg": 0.2, "npxg": 0.2, "rojas": 1, "presion": 0.1}}
    for f in tracker.evaluate(_partido(55), (1.3, 1.1), precios, xg, usar_xg=True):
        assert abs(f["p_previa"] + sum(c for _, c in f["factores"]) - f["p_modelo"]) < 1e-9
        assert [n for n, _ in f["factores"]] == ["Marcador y reloj", "xG", "Rojas", "Momentum"]
        assert f["veredicto"]


def test_sin_marcador_no_da_senal():
    precios = {"home": {"ask": 0.30}, "draw": {"ask": 0.30}, "away": {"ask": 0.40}}
    filas = tracker.evaluate(_partido(30, status=tracker.SIN_DATOS), (1.3, 1.1), precios)
    assert {f["senal"] for f in filas} == {"SIN MARCADOR"}


def test_todas_las_ligas_completas():
    assert len(tracker.COMPETICIONES) == 10
    for codigo, (nombre, tag, liga_espn, emblema) in tracker.COMPETICIONES.items():
        assert nombre and tag and "." in liga_espn and emblema.startswith("https://"), codigo
