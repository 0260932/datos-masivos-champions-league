"""
Pipeline de procesamiento de Polymarket: raw -> interim -> processed.

Uso:
    python -m src.processing.polymarket                         # usa el historial en caché si existe
    python -m src.processing.polymarket --refrescar-historial   # vuelve a pedir el historial a la API

Entradas (data/raw/):
    polymarket_markets.csv, polymarket_events_raw.json  -> generados por src.ingestion.download_polymarket
    polymarket_prices_history.csv                        -> lo genera ESTE script (caché del endpoint
                                                            público CLOB /prices-history)
    UCL_Finals_1955-2023.csv, UCL_AllTime_Performance_Table.csv -> referencia histórica

Salidas:
    data/interim/polymarket/mercados_limpios.csv
    data/interim/polymarket/historial_precios.csv
    data/processed/polymarket/tipo_mercado=<tipo>/mercados.csv   (particionado por tipo de mercado)
    data/processed/polymarket/resumen_eventos.csv
    data/processed/polymarket/movimientos_significativos.csv
    data/processed/polymarket/referencia_equipos.csv

AVISO ACADÉMICO
---------------
Este módulo es un ejercicio académico de la materia Datos Masivos (Universidad
Panamericana). Las columnas `prob_referencia` y `diferencia_probabilidad`
(equivalente al "valor esperado" por acción de $1 bajo un modelo ingenuo) son
resultado de un modelo académico con datos históricos limitados. NO es asesoría
financiera, NO usa información privilegiada, NO es una recomendación de apuesta
y NO debe usarse para apostar dinero real. El resultado es descriptivo, para que
una persona lo revise; el código no toma ninguna decisión de "apostar o no".

Los "movimientos significativos" del precio solo indican CUÁNDO cambió el precio,
no POR QUÉ cambió (para eso haría falta contexto externo: partidos, lesiones, noticias).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

AVISO_CORTO = (
    "Ejercicio académico (Datos Masivos, UP): 'prob_referencia' y 'diferencia_probabilidad' "
    "provienen de un modelo ingenuo con datos históricos limitados. No es asesoría financiera "
    "ni recomendación de apuesta; no debe usarse para apostar dinero real."
)

# --------------------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim" / "polymarket"
PROCESSED = ROOT / "data" / "processed" / "polymarket"

MARKETS_CSV = RAW / "polymarket_markets.csv"
EVENTS_JSON = RAW / "polymarket_events_raw.json"
HISTORY_CSV = RAW / "polymarket_prices_history.csv"
FINALS_CSV = RAW / "UCL_Finals_1955-2023.csv"
ALLTIME_CSV = RAW / "UCL_AllTime_Performance_Table.csv"

# --------------------------------------------------------------------------------------
# Parámetros (decisiones documentadas en docs/decisions.md)
# --------------------------------------------------------------------------------------
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"
HISTORY_PARAMS = {"interval": "max", "fidelity": "1440"}  # todo el historial, 1 punto por día
PAUSA_ENTRE_PETICIONES_S = 0.15

UMBRAL_MIN_PP = 0.02  # piso: 2 puntos de probabilidad
K_DESVIACIONES = 3    # o 3 desviaciones estándar de los cambios diarios de ESE mercado

# Picos fantasma: en mercados poco negociados el precio salta a ~0.5 (valor por defecto sin
# ofertas) y regresa días después. Se elimina un punto si se aleja >= 0.20 de la mediana móvil
# centrada de 5 días de su mercado. Un cambio real y persistente sobrevive a este filtro.
VENTANA_MEDIANA_DIAS = 5
UMBRAL_ANOMALIA = 0.20

# Mesetas en 0.5: hay periodos de varios días en los que casi todos los mercados de un mismo
# evento marcan 0.50 a la vez (p. ej. 34 de 36 en "League Phase Winner" el 2026-09-10): el
# mercado se quedó sin libro de órdenes y Polymarket muestra el valor por defecto. Se descarta
# un punto si vale 0.50 +/- TOLERANCIA_DEFECTO y la mediana típica del mercado (sin esos puntos)
# está a >= UMBRAL_ANOMALIA de 0.50.
PRECIO_DEFECTO = 0.5
TOLERANCIA_DEFECTO = 0.01

# Fallas sistémicas por evento: cuando el libro de órdenes se vacía, el precio mostrado es el
# punto medio entre una oferta casi en 0 y una lejana (p. ej. 0.47), no exactamente 0.5. Una
# noticia real mueve a uno o pocos equipos; una falla mueve a gran parte del evento el mismo día.
# Si en un (evento, fecha) al menos esta proporción de mercados se aleja >= UMBRAL_ANOMALIA de su
# precio típico, esos puntos se descartan.
PROPORCION_FALLA_EVENTO = 0.25

# Los primeros días de un mercado son ajustes del precio inicial, no reacciones a noticias.
DIAS_APERTURA_EXCLUIDOS = 3

# (texto en event_title, tipo_mercado, número de equipos que "ganan" ese mercado)
TIPOS_MERCADO = [
    ("2027 Champion", "campeon", 1),
    ("League Phase Winner", "ganador_fase_liga", 1),
    ("Top 8", "top_8", 8),
    ("9th-24th", "puesto_9_24", 16),
    ("Round of 16", "octavos", 16),
]

PATRON_EQUIPO = r"^Will (.+?) (?:win|finish|advance|reach|make|qualify)\b"

# Nombres alternativos con los que un equipo de Polymarket puede aparecer en otras fuentes.
ALIAS = {
    "Inter Milan": ["Internazionale", "Inter", "FC Internazionale Milano"],
    "Bayern Munich": ["Bayern München", "FC Bayern München", "Bayern"],
    "Atlético Madrid": ["Atletico Madrid", "Club Atlético de Madrid"],
    "Paris Saint-Germain": ["PSG", "Paris SG", "Paris Saint Germain"],
    "Sporting CP": ["Sporting Lisbon", "Sporting Clube de Portugal", "Sporting"],
    "Slavia Prague": ["Slavia Praha", "SK Slavia Praha"],
    "Slovan Bratislava": ["ŠK Slovan Bratislava"],
    "LASK": ["LASK Linz"],
    "Como": ["Como 1907"],
    "Real Betis": ["Real Betis Balompié", "Betis"],
    "AEK Athens": ["PAE AEK", "AEK", "AEK Athen"],
    "Feyenoord": ["Feyenoord Rotterdam"],
    "Lens": ["Racing Club de Lens", "RC Lens"],
    "Lille": ["Lille OSC", "LOSC Lille"],
    "PSV Eindhoven": ["PSV"],
    "Napoli": ["SSC Napoli"],
    "Roma": ["AS Roma"],
    "Bodø/Glimt": ["FK Bodø/Glimt", "Bodo/Glimt"],
    "Sabah": ["Sabah FK"],
    "Viking": ["Viking FK"],
    "Shakhtar Donetsk": ["FK Shakhtar Donetsk"],
    "Fenerbahçe": ["Fenerbahçe SK"],
    "Galatasaray": ["Galatasaray SK"],
    "Villarreal": ["Villarreal CF"],
}

PALABRAS_RUIDO = {
    "fc", "cf", "afc", "sk", "fk", "kv", "sc", "club", "cp", "osc",
    "ac", "as", "ssc", "rc", "de", "pae", "losc",
}


# --------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------
def normalizar_nombre(nombre: object) -> str:
    """Minúsculas, sin acentos, sin notas al pie ni palabras genéricas (FC, CF, SK...)."""
    if not isinstance(nombre, str):
        return ""
    s = re.sub(r"\[.*?\]|\(.*?\)", "", nombre)
    for original, reemplazo in {"ø": "o", "Ø": "O", "æ": "ae", "ß": "ss", "ł": "l"}.items():
        s = s.replace(original, reemplazo)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return " ".join(t for t in s.split() if t not in PALABRAS_RUIDO)


def claves_equipo(equipo: str) -> set[str]:
    return {normalizar_nombre(n) for n in [equipo, *ALIAS.get(equipo, [])]} - {""}


def _parse_lista(valor: object) -> list | None:
    """outcomes / outcome_prices / clobTokenIds vienen como texto con formato de lista JSON."""
    if isinstance(valor, list):
        return valor
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        resultado = json.loads(valor)
    except json.JSONDecodeError:
        return None
    return resultado if isinstance(resultado, list) else None


def _precio_yes(fila: pd.Series) -> float:
    outcomes, precios = fila["outcomes_list"], fila["prices_list"]
    if not outcomes or not precios or len(outcomes) != len(precios) or "Yes" not in outcomes:
        return np.nan
    try:
        return float(precios[outcomes.index("Yes")])
    except (TypeError, ValueError):
        return np.nan


def _tipo_mercado(titulo: str) -> tuple[str, float]:
    for texto, tipo, esperados in TIPOS_MERCADO:
        if isinstance(titulo, str) and texto.lower() in titulo.lower():
            return tipo, float(esperados)
    return "otro", np.nan


# --------------------------------------------------------------------------------------
# 1) raw -> interim: limpieza de mercados
# --------------------------------------------------------------------------------------
def limpiar_mercados(raw: pd.DataFrame) -> pd.DataFrame:
    columnas = [
        "fetched_at", "event_id", "event_title", "market_id", "question",
        "outcomes", "outcome_prices", "last_trade_price", "best_bid", "best_ask",
        "volume", "liquidity", "active", "closed", "end_date",
    ]
    df = raw[columnas].copy()
    logger.info("Mercados crudos: %d", len(df))

    # Conversión de tipos
    df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True, errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], utc=True, errors="coerce")
    for col in ["last_trade_price", "best_bid", "best_ask", "volume", "liquidity"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["active", "closed"]:
        df[col] = df[col].astype(str).str.strip().str.lower().eq("true")
    df["event_id"] = pd.to_numeric(df["event_id"], errors="coerce").astype("Int64")
    df["market_id"] = pd.to_numeric(df["market_id"], errors="coerce").astype("Int64")

    df["outcomes_list"] = df["outcomes"].apply(_parse_lista)
    df["prices_list"] = df["outcome_prices"].apply(_parse_lista)
    df["prob_implicita"] = df.apply(_precio_yes, axis=1)

    # Mercados "de relleno" (Team A..L, "any other team"): inactivos, precio 0.5 por defecto,
    # sin volumen. Inflan artificialmente la suma de probabilidades del evento -> se eliminan.
    relleno = ~df["active"]
    logger.info("Mercados de relleno (active=False) eliminados: %d", int(relleno.sum()))
    df = df[~relleno].copy()

    sin_precio = df["prob_implicita"].isna()
    if sin_precio.any():
        logger.warning("Mercados sin precio 'Yes' interpretable eliminados: %d", int(sin_precio.sum()))
        df = df[~sin_precio].copy()

    # Duplicados: se conserva la descarga más reciente de cada mercado
    antes = len(df)
    df = df.sort_values("fetched_at").drop_duplicates("market_id", keep="last")
    logger.info("Duplicados por market_id eliminados: %d", antes - len(df))

    # Valores faltantes
    #  - best_bid vacío = nadie ofrece comprar el "Yes" (mercado casi sin demanda). Se deja vacío
    #    y se marca, en lugar de inventar un valor.
    #  - last_trade_price vacío = no hay operaciones registradas. Se deja vacío.
    #  - volume vacío = sin volumen registrado -> 0, marcado como imputado.
    df["sin_ofertas_compra"] = df["best_bid"].isna()
    df["volumen_imputado"] = df["volume"].isna()
    df["volume"] = df["volume"].fillna(0.0)

    # Estandarización de texto
    df["event_title"] = df["event_title"].str.strip()
    df["question"] = df["question"].str.strip().str.replace(r"\s+", " ", regex=True)
    df["equipo"] = df["question"].str.extract(PATRON_EQUIPO, expand=False).str.strip()
    df["equipo_clave"] = df["equipo"].apply(normalizar_nombre)
    sin_equipo = df["equipo"].isna().sum()
    if sin_equipo:
        logger.warning("Preguntas de las que no se pudo extraer equipo: %d", sin_equipo)

    tipos = df["event_title"].apply(_tipo_mercado)
    df["tipo_mercado"] = tipos.str[0]
    df["ganadores_esperados"] = tipos.str[1]

    df = df.drop(columns=["outcomes_list", "prices_list"])
    logger.info("Mercados limpios: %d", len(df))
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------------------
# 2) Historial de precios (endpoint público CLOB /prices-history)
# --------------------------------------------------------------------------------------
def _tokens_yes() -> dict[int, str]:
    """market_id -> token id del resultado 'Yes', tomado de polymarket_events_raw.json."""
    crudo = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    eventos = crudo["events"] if isinstance(crudo, dict) else crudo
    tokens: dict[int, str] = {}
    for evento in eventos:
        for mercado in evento.get("markets", []):
            ids = _parse_lista(mercado.get("clobTokenIds"))
            outcomes = _parse_lista(mercado.get("outcomes"))
            if not ids or not outcomes or len(ids) != len(outcomes) or "Yes" not in outcomes:
                continue
            tokens[int(mercado["id"])] = str(ids[outcomes.index("Yes")])
    return tokens


def _pedir_historial(sesion: requests.Session, token: str, intentos: int = 3) -> list | None:
    for intento in range(1, intentos + 1):
        try:
            r = sesion.get(CLOB_HISTORY_URL, params={"market": token, **HISTORY_PARAMS}, timeout=30)
            if r.status_code == 200:
                return r.json().get("history", [])
            logger.warning("HTTP %s para token %s... (intento %d)", r.status_code, token[:12], intento)
        except (requests.RequestException, ValueError) as error:
            logger.warning("Error para token %s... (intento %d): %s", token[:12], intento, error)
        time.sleep(1.5 * intento)
    return None


def obtener_historial(market_ids: list[int], refrescar: bool = False) -> pd.DataFrame:
    if HISTORY_CSV.exists() and not refrescar:
        logger.info("Usando historial en caché: %s (usa --refrescar-historial para actualizar)", HISTORY_CSV)
        return pd.read_csv(HISTORY_CSV, dtype={"token_id_yes": str})

    tokens = _tokens_yes()
    sesion = requests.Session()
    descargado_en = datetime.now(timezone.utc).isoformat()
    filas, fallos = [], 0
    logger.info("Descargando historial diario de %d mercados...", len(market_ids))
    for i, market_id in enumerate(market_ids, start=1):
        token = tokens.get(int(market_id))
        if token is None:
            logger.warning("Mercado %s sin token 'Yes' en el JSON crudo", market_id)
            continue
        historia = _pedir_historial(sesion, token)
        if historia is None:
            fallos += 1
            continue
        filas.extend(
            {"market_id": int(market_id), "token_id_yes": token, "t": p.get("t"),
             "p": p.get("p"), "fetched_at": descargado_en}
            for p in historia
        )
        if i % 25 == 0:
            logger.info("  ...%d/%d mercados", i, len(market_ids))
        time.sleep(PAUSA_ENTRE_PETICIONES_S)

    hist = pd.DataFrame(filas, columns=["market_id", "token_id_yes", "t", "p", "fetched_at"])
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    hist.to_csv(HISTORY_CSV, index=False)
    logger.info("Historial guardado: %s (%d puntos, %d mercados con error)", HISTORY_CSV, len(hist), fallos)
    return hist


def limpiar_historial(hist: pd.DataFrame, evento_por_mercado: pd.Series | None = None) -> pd.DataFrame:
    """evento_por_mercado: serie market_id -> event_id, para detectar fallas que afectan a todo un evento."""
    h = hist.copy()
    h["t"] = pd.to_numeric(h["t"], errors="coerce")
    h["p"] = pd.to_numeric(h["p"], errors="coerce")
    h = h.dropna(subset=["market_id", "t", "p"])
    h = h[(h["p"] >= 0) & (h["p"] <= 1)].copy()
    h["market_id"] = h["market_id"].astype("int64")
    h["fecha_hora"] = pd.to_datetime(h["t"], unit="s", utc=True)
    h["fecha"] = h["fecha_hora"].dt.strftime("%Y-%m-%d")

    # Un punto por mercado y día: el último del día ("cierre diario")
    h = h.sort_values(["market_id", "t"]).drop_duplicates(["market_id", "fecha"], keep="last")

    # Eliminación de mesetas en el precio por defecto (ver PRECIO_DEFECTO)
    en_defecto = (h["p"] - PRECIO_DEFECTO).abs() <= TOLERANCIA_DEFECTO
    mediana_tipica = h["p"].where(~en_defecto).groupby(h["market_id"]).transform("median")
    meseta = en_defecto & ((mediana_tipica - PRECIO_DEFECTO).abs() >= UMBRAL_ANOMALIA)
    logger.info("Puntos en precio por defecto 0.5 (mercado sin órdenes) eliminados: %d en %d mercados",
                int(meseta.sum()), h.loc[meseta, "market_id"].nunique())
    h = h[~meseta].copy()

    # Eliminación de fallas sistémicas por evento (ver PROPORCION_FALLA_EVENTO)
    if evento_por_mercado is not None:
        h["event_id"] = h["market_id"].map(evento_por_mercado)
        tipico = h.groupby("market_id")["p"].transform("median")
        desviado = (h["p"] - tipico).abs() >= UMBRAL_ANOMALIA
        n_mercados_evento = h.groupby("event_id")["market_id"].transform("nunique")
        n_desviados_dia = desviado.groupby([h["event_id"], h["fecha"]]).transform("sum")
        falla = desviado & (n_desviados_dia / n_mercados_evento >= PROPORCION_FALLA_EVENTO)
        logger.info("Puntos en fallas sistémicas por evento eliminados: %d en %d mercados (%d evento-días)",
                    int(falla.sum()), h.loc[falla, "market_id"].nunique(),
                    h.loc[falla, ["event_id", "fecha"]].drop_duplicates().shape[0])
        h = h[~falla].drop(columns=["event_id"]).copy()

    # Eliminación de picos fantasma aislados (ver UMBRAL_ANOMALIA)
    mediana_movil = h.groupby("market_id")["p"].transform(
        lambda s: s.rolling(VENTANA_MEDIANA_DIAS, center=True, min_periods=1).median()
    )
    anomalia = (h["p"] - mediana_movil).abs() >= UMBRAL_ANOMALIA
    logger.info("Puntos de historial anómalos (picos fantasma) eliminados: %d en %d mercados",
                int(anomalia.sum()), h.loc[anomalia, "market_id"].nunique())
    h = h[~anomalia].copy()

    # Cambio diario en PUNTOS de probabilidad (no porcentual: el porcentual exagera
    # movimientos mínimos en equipos con probabilidad casi nula, p. ej. 0.001 -> 0.002 = +100%).
    h["dia_desde_apertura"] = h.groupby("market_id").cumcount()
    h["es_periodo_apertura"] = h["dia_desde_apertura"] < DIAS_APERTURA_EXCLUIDOS
    h["cambio_pp"] = h.groupby("market_id")["p"].diff().round(6)
    cambios_evaluables = h["cambio_pp"].where(~h["es_periodo_apertura"])
    desviacion = cambios_evaluables.groupby(h["market_id"]).transform("std")
    h["umbral_pp"] = np.maximum(UMBRAL_MIN_PP, K_DESVIACIONES * desviacion.fillna(0)).round(6)
    h["es_movimiento_significativo"] = (cambios_evaluables.abs() >= h["umbral_pp"]).fillna(False)

    # El último punto del día de hoy corresponde a un día que aún no termina
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    h["dia_incompleto"] = h["fecha"].eq(hoy)
    return h.reset_index(drop=True)


def resumir_historial(h: pd.DataFrame) -> pd.DataFrame:
    resumen = h.groupby("market_id").agg(
        n_dias_historial=("p", "size"),
        precio_min_hist=("p", "min"),
        precio_max_hist=("p", "max"),
        n_movimientos_significativos=("es_movimiento_significativo", "sum"),
    )
    resumen["rango_precio_hist"] = (resumen["precio_max_hist"] - resumen["precio_min_hist"]).round(6)

    con_cambio = h[~h["es_periodo_apertura"]].dropna(subset=["cambio_pp"]).copy()
    con_cambio["abs_cambio"] = con_cambio["cambio_pp"].abs()
    idx = con_cambio.groupby("market_id")["abs_cambio"].idxmax()
    mayor = (
        con_cambio.loc[idx, ["market_id", "fecha", "cambio_pp"]]
        .rename(columns={"fecha": "fecha_mayor_cambio", "cambio_pp": "mayor_cambio_diario_pp"})
        .set_index("market_id")
    )
    return resumen.join(mayor).reset_index()


# --------------------------------------------------------------------------------------
# 3) Probabilidad de referencia (modelo académico ingenuo)
# --------------------------------------------------------------------------------------
def construir_referencia(equipos: list[str]) -> pd.DataFrame:
    """
    Para cada equipo:
      - titulos_historicos: finales ganadas 1955-2023 (UCL_Finals_1955-2023.csv)
      - partidos_historicos / victorias_historicas / winrate_historico (tabla histórica de Kaggle)
      - prob_referencia_campeon: proporción de títulos con suavizado de Laplace (+1 por equipo),
        normalizada entre los equipos participantes para que sume 1.
    Es una referencia deliberadamente simple: la historia no refleja la forma actual del equipo.
    """
    finales = pd.read_csv(FINALS_CSV)
    # Algunas finales se jugaron dos veces (p. ej. 1973-74: empate 1-1 y desempate 4-0) y aparecen
    # en dos filas. Se cuenta un solo título por temporada: la última fila (el desempate).
    antes = len(finales)
    finales = finales.drop_duplicates("Season", keep="last")
    if antes != len(finales):
        logger.info("Finales repetidas (desempates) consolidadas: %d fila(s)", antes - len(finales))
    titulos_por_clave = finales["Winners"].dropna().map(normalizar_nombre).value_counts()

    historica = pd.read_csv(ALLTIME_CSV)
    historica.columns = [c.strip() for c in historica.columns]
    historica["clave"] = historica["Team"].map(normalizar_nombre)
    historica["M."] = pd.to_numeric(historica["M."], errors="coerce")
    historica["W"] = pd.to_numeric(historica["W"], errors="coerce")
    historica = historica.drop_duplicates("clave").set_index("clave")

    filas, sin_historial = [], []
    for equipo in equipos:
        claves = claves_equipo(equipo)
        titulos = int(sum(titulos_por_clave.get(k, 0) for k in claves))
        coincidencias = [k for k in claves if k in historica.index]
        if coincidencias:
            fila = historica.loc[coincidencias[0]]
            partidos, victorias, nombre_fuente = fila["M."], fila["W"], fila["Team"]
        else:
            partidos = victorias = np.nan
            nombre_fuente = None
            sin_historial.append(equipo)
        filas.append({
            "equipo": equipo,
            "nombre_en_tabla_historica": nombre_fuente,
            "titulos_historicos": titulos,
            "partidos_historicos": partidos,
            "victorias_historicas": victorias,
        })

    ref = pd.DataFrame(filas)
    ref["winrate_historico"] = (ref["victorias_historicas"] / ref["partidos_historicos"]).round(4)
    suavizado = ref["titulos_historicos"] + 1
    ref["prob_referencia_campeon"] = (suavizado / suavizado.sum()).round(6)

    logger.info("Títulos históricos asignados a participantes: %d", int(ref["titulos_historicos"].sum()))
    if sin_historial:
        logger.warning("Equipos sin coincidencia en la tabla histórica (winrate vacío): %s", sin_historial)
    return ref


# --------------------------------------------------------------------------------------
# 4) interim -> processed: variables y agregaciones
# --------------------------------------------------------------------------------------
def crear_variables(mercados: pd.DataFrame, resumen_hist: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    df = mercados.merge(resumen_hist, on="market_id", how="left")
    df = df.merge(
        ref[["equipo", "titulos_historicos", "winrate_historico", "prob_referencia_campeon"]],
        on="equipo", how="left",
    )
    df["spread"] = (df["best_ask"] - df["best_bid"]).round(6)

    # AVISO ACADÉMICO: prob_referencia sólo se define para el evento "campeon", donde es comparable
    # con el precio del mercado. diferencia_probabilidad = prob_referencia - prob_implicita, que
    # equivale al valor esperado por acción "Yes" de $1 bajo este modelo ingenuo:
    #   VE = p_ref*(1 - p) - (1 - p_ref)*p = p_ref - p
    # Es un dato descriptivo de un ejercicio académico, NO una recomendación de apuesta.
    es_campeon = df["tipo_mercado"].eq("campeon")
    df["prob_referencia"] = df["prob_referencia_campeon"].where(es_campeon)
    df["diferencia_probabilidad"] = (df["prob_referencia"] - df["prob_implicita"]).round(6)
    return df.drop(columns=["prob_referencia_campeon"])


def resumir_eventos(df: pd.DataFrame) -> pd.DataFrame:
    res = (
        df.groupby(["event_id", "event_title", "tipo_mercado"], dropna=False)
        .agg(
            n_mercados=("market_id", "size"),
            suma_prob_implicita=("prob_implicita", "sum"),
            ganadores_esperados=("ganadores_esperados", "first"),
            spread_promedio=("spread", "mean"),
            volumen_total=("volume", "sum"),
            liquidez_total=("liquidity", "sum"),
            n_movimientos_significativos=("n_movimientos_significativos", "sum"),
        )
        .reset_index()
    )
    # Si la suma de probabilidades se aleja de lo esperado, los precios del evento no son del
    # todo coherentes entre sí (margen, spreads, mercados sin liquidez). Es una medida
    # descriptiva; NO implica que exista una oportunidad de arbitraje aprovechable.
    res["desviacion_suma_vs_esperado"] = (res["suma_prob_implicita"] - res["ganadores_esperados"]).round(4)
    return res


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Procesamiento de datos de Polymarket")
    parser.add_argument("--refrescar-historial", action="store_true",
                        help="Vuelve a descargar el historial de precios aunque exista la caché")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.warning(AVISO_CORTO)

    INTERIM.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    # raw -> interim
    mercados = limpiar_mercados(pd.read_csv(MARKETS_CSV))
    mercados.to_csv(INTERIM / "mercados_limpios.csv", index=False)

    evento_por_mercado = mercados.set_index(mercados["market_id"].astype(int))["event_id"]
    historial = limpiar_historial(obtener_historial(mercados["market_id"].astype(int).tolist(),
                                                    refrescar=args.refrescar_historial),
                                  evento_por_mercado)
    historial.to_csv(INTERIM / "historial_precios.csv", index=False)
    faltantes = set(mercados["market_id"].astype(int)) - set(historial["market_id"])
    if faltantes:
        logger.warning("Mercados sin historial de precios: %d", len(faltantes))

    # interim -> processed
    referencia = construir_referencia(sorted(mercados["equipo"].dropna().unique()))
    referencia.to_csv(PROCESSED / "referencia_equipos.csv", index=False)

    final = crear_variables(mercados, resumir_historial(historial), referencia)
    for tipo, grupo in final.groupby("tipo_mercado"):
        carpeta = PROCESSED / f"tipo_mercado={tipo}"
        carpeta.mkdir(parents=True, exist_ok=True)
        grupo.to_csv(carpeta / "mercados.csv", index=False)

    eventos = resumir_eventos(final)
    eventos.to_csv(PROCESSED / "resumen_eventos.csv", index=False)

    movimientos = historial[historial["es_movimiento_significativo"]].merge(
        mercados[["market_id", "equipo", "tipo_mercado", "question"]], on="market_id", how="left"
    )
    movimientos.to_csv(PROCESSED / "movimientos_significativos.csv", index=False)

    logger.info("Listo. Mercados procesados: %d | puntos de historial: %d | movimientos significativos: %d",
                len(final), len(historial), len(movimientos))
    print(eventos[["tipo_mercado", "n_mercados", "suma_prob_implicita", "ganadores_esperados",
                   "desviacion_suma_vs_esperado", "n_movimientos_significativos"]].to_string(index=False))
    logger.warning(AVISO_CORTO)


if __name__ == "__main__":
    main()
