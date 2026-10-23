"""
Dashboard web del tracker de moneyline (Champions League).

Muestra en una pagina que se actualiza sola (cada 30 s por default) los
partidos en juego, la probabilidad del modelo contra el precio de
Polymarket y la senal BUENO / ESPERAR / NO para local, empate y visita.
Usa la misma logica que src/live/tracker.py.

Ejecucion local:
    streamlit run streamlit_app.py

En Streamlit Community Cloud se configura FOOTBALL_DATA_API_TOKEN en
Settings > Secrets (ver README).
"""

from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

from src.live.modelo import StrengthModel, live_probabilities
from src.live.tracker import DemoTracker, PolymarketMoneyline, evaluate, fetch_live_matches, price_history

HORA_MX = timezone(timedelta(hours=-6))  # CDMX no tiene horario de verano desde 2022
COLORES_SENAL = {
    "BUENO": ("#15803d", "rgba(22,163,74,.16)"),
    "ESPERAR": ("#b45309", "rgba(217,119,6,.16)"),
    "NO": ("#b91c1c", "rgba(220,38,38,.14)"),
    "SIN MERCADO": ("#64748b", "rgba(100,116,139,.16)"),
}
# Colores de cada resultado en la grafica (como las lineas de Polymarket)
COLORES_LADO = {"home": "#2d6cdf", "draw": "#8b95a5", "away": "#f2a23a"}
# Rango de la grafica -> (horas hacia atras o None = todo, fidelidad en minutos)
RANGOS = {"Partido": (None, 1), "6H": (6, 1), "1D": (24, 5), "1S": (24 * 7, 60), "Todo": (None, 180)}

st.set_page_config(page_title="Moneyline UCL en vivo", page_icon="⚽", layout="wide")
st.markdown(
    """
    <style>
    .pill {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.78rem;
           font-weight:700; letter-spacing:.04em; font-variant-numeric:tabular-nums}
    .live {color:#dc2626; background:rgba(220,38,38,.12)}
    .pre {color:#2563eb; background:rgba(37,99,235,.12)}
    .marcador {font-size:1.6rem; font-weight:800; font-variant-numeric:tabular-nums}
    .lado {font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; opacity:.7}
    .equipo {font-size:1.05rem; font-weight:700; margin-bottom:.35rem}
    .fila {display:flex; justify-content:space-between; font-variant-numeric:tabular-nums;
           font-size:.92rem; padding:1px 0}
    .fila span:first-child {opacity:.7}
    .grande {font-size:1.5rem; font-weight:800; font-variant-numeric:tabular-nums}
    .pm-head {display:flex; flex-wrap:wrap; gap:6px 18px; align-items:baseline; margin:.6rem 0 .2rem}
    .pm-lider {font-size:1.9rem; font-weight:800; font-variant-numeric:tabular-nums; line-height:1}
    .pm-lider small {font-size:.95rem; font-weight:600; opacity:.65; margin-left:6px}
    .pm-chip {display:inline-flex; align-items:center; gap:6px; font-size:.85rem;
              font-variant-numeric:tabular-nums}
    .pm-dot {width:9px; height:9px; border-radius:50%; display:inline-block}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Recursos compartidos y cache (para no pasarse del limite de la API:
# 10 solicitudes/min en el plan gratuito de football-data.org)
# ---------------------------------------------------------------------------

@st.cache_resource
def modelo():
    return StrengthModel()


@st.cache_resource
def polymarket():
    return PolymarketMoneyline()


@st.cache_data(ttl=20, show_spinner=False)
def partidos_en_vivo(minutos_previa):
    return fetch_live_matches(minutos_previa)


@st.cache_data(ttl=20, show_spinner=False)
def precios_polymarket(match_id, home, away, utc_date):
    partido = {"match_id": match_id, "home": home, "away": away, "utc_date": utc_date}
    return polymarket().prices(partido)


@st.cache_data(ttl=30, show_spinner=False)
def historial_polymarket(token_id, start_ts, fidelity):
    return price_history(token_id, start_ts, fidelity)


def fmt_ml(v):
    return "–" if v is None else f"{v:+d}"


def fmt_pct(v):
    return "–" if v is None else f"{v:.1%}"


# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Ajustes")
    edge_min = st.slider("Ventaja minima para BUENO (puntos)", 2, 15, 5) / 100
    intervalo = st.select_slider("Actualizar cada", [15, 30, 60, 120], value=30, format_func=lambda s: f"{s} s")
    horas_previa = st.slider("Incluir partidos que empiezan en las proximas (horas)", 0, 48, 2)
    rango = st.segmented_control("Rango de la grafica", list(RANGOS), default="Partido") or "Partido"
    demo = st.toggle("Modo demo (partido simulado)", help="Simula un partido para ver como se comporta el tablero.")
    st.caption(
        "Modelo: goles esperados por equipo (estadisticas del proyecto + tabla historica) "
        "y Poisson sobre el tiempo restante. Mercado: precio de compra (best ask) del "
        "moneyline del partido en Polymarket. BUENO = modelo − mercado ≥ ventaja minima."
    )

st.title("Moneyline UCL en vivo")
st.caption("Champions League · modelo vs. Polymarket · se actualiza solo")

estado = st.session_state
estado.setdefault("historial", {})
estado.setdefault("ultima_senal", {})
estado.setdefault("avisos", [])
if demo and "demo" not in estado:
    estado["demo"] = DemoTracker(edge_min, False, "Arsenal FC", "FC Bayern München")
if not demo:
    estado.pop("demo", None)


# ---------------------------------------------------------------------------
# Panel que se refresca solo
# ---------------------------------------------------------------------------

def tarjeta(partido, filas, lambdas, precios):
    with st.container(border=True):
        izq, der = st.columns([3, 1])
        with izq:
            st.markdown(
                f"<div class='marcador'>{partido['home']} {partido['home_goals']} – "
                f"{partido['away_goals']} {partido['away']}</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"Goles esperados antes del partido: {lambdas[0]:.2f} – {lambdas[1]:.2f}")
        with der:
            if partido["status"] in ("TIMED", "SCHEDULED"):
                hora = partido["utc_date"].astimezone(HORA_MX).strftime("%H:%M")
                pill = f"<span class='pill pre'>PREVIA · {hora}</span>"
            elif partido["status"] == "PAUSED":
                pill = "<span class='pill live'>MEDIO TIEMPO</span>"
            elif partido["status"] == "FINISHED":
                pill = "<span class='pill pre'>FINAL</span>"
            else:
                pill = f"<span class='pill live'>● EN VIVO · {partido['minute']}'</span>"
            st.markdown(f"<div style='text-align:right'>{pill}</div>", unsafe_allow_html=True)

        etiquetas = {"home": "Local", "draw": "Empate", "away": "Visita"}
        for col, f in zip(st.columns(3), filas):
            texto, fondo = COLORES_SENAL[f["senal"]]
            edge = "–" if f["edge"] is None else f"{f['edge'] * 100:+.1f} pts"
            col.markdown(
                f"""
                <div class='lado'>{etiquetas[f['lado']]}</div>
                <div class='equipo'>{f['equipo']}</div>
                <span class='pill' style='color:{texto}; background:{fondo}'>{f['senal']}</span>
                <div class='grande'>{edge}</div>
                <div class='fila'><span>Modelo</span><span>{fmt_pct(f['p_modelo'])}</span></div>
                <div class='fila'><span>Mercado</span><span>{fmt_pct(f['ask'])}</span></div>
                <div class='fila'><span>Momio justo / merc.</span>
                    <span>{fmt_ml(f['ml_justo'])} / {fmt_ml(f['ml_mercado'])}</span></div>
                <div class='fila'><span>¼ Kelly</span><span>{f['kelly_25']:.1%}</span></div>
                """,
                unsafe_allow_html=True,
            )

        grafica_polymarket(partido, filas, precios)


def serie_mercado(partido, precios, lado):
    """Historial del precio del resultado: de Polymarket si hay token, si no el visto en esta sesion."""
    token = precios.get(lado, {}).get("token_id")
    if token:
        horas, fidelidad = RANGOS[rango]
        if rango == "Partido":
            # en juego: desde 1 h antes del inicio; en previa: las ultimas 24 h
            inicio = min(partido["utc_date"] - timedelta(hours=1), datetime.now(timezone.utc) - timedelta(hours=24))
            if partido["utc_date"] <= datetime.now(timezone.utc):
                inicio = partido["utc_date"] - timedelta(hours=1)
        elif horas is not None:
            inicio = datetime.now(timezone.utc) - timedelta(hours=horas)
        else:
            inicio = None
        try:
            puntos = historial_polymarket(token, None if inicio is None else inicio.timestamp(), fidelidad)
        except Exception:
            puntos = []
        if puntos:
            return pd.DataFrame(
                [{"hora": t.astimezone(HORA_MX).replace(tzinfo=None), "p": p} for t, p in puntos]
            )
    vistos = [
        h for h in estado["historial"].get(partido["match_id"], [])
        if h["lado"] == lado and h["ask"] is not None
    ]
    return pd.DataFrame([{"hora": h["hora"], "p": h["ask"]} for h in vistos])


def grafica_polymarket(partido, filas, precios):
    nombres = {f["lado"]: f["equipo"] for f in filas}
    orden = ("home", "draw", "away")
    partes = []
    for lado in orden:
        mercado = serie_mercado(partido, precios, lado)
        if not mercado.empty:
            mercado["serie"], mercado["fuente"] = nombres[lado], "Mercado"
            partes.append(mercado)
        modelo_hist = [h for h in estado["historial"].get(partido["match_id"], []) if h["lado"] == lado]
        if modelo_hist:
            partes.append(pd.DataFrame([
                {"hora": h["hora"], "p": h["p_modelo"], "serie": nombres[lado], "fuente": "Modelo"}
                for h in modelo_hist
            ]))
    if not partes:
        return
    datos = pd.concat(partes, ignore_index=True)

    # el ultimo precio de mercado se estira hasta "ahora" para que la linea llegue al borde
    ultimo = datos[datos["fuente"] == "Mercado"].sort_values("hora").groupby("serie").tail(1).copy()
    ultimo["hora"] = datetime.now(HORA_MX).replace(tzinfo=None)
    datos = pd.concat([datos, ultimo], ignore_index=True)

    # Encabezado tipo Polymarket: % del favorito y chips con el % actual de cada resultado
    actuales = dict(zip(ultimo["serie"], ultimo["p"]))
    if actuales:
        lider = max(actuales, key=actuales.get)
        chips = "".join(
            f"<span class='pm-chip'><span class='pm-dot' style='background:{COLORES_LADO[lado]}'></span>"
            f"{nombres[lado]} <b>{actuales[nombres[lado]]:.0%}</b></span>"
            for lado in orden if nombres[lado] in actuales
        )
        st.markdown(
            f"<div class='pm-head'><span class='pm-lider'>{actuales[lider]:.0%}<small>{lider}</small></span>"
            f"{chips}</div>",
            unsafe_allow_html=True,
        )

    colores = alt.Scale(domain=[nombres[l] for l in orden], range=[COLORES_LADO[l] for l in orden])
    base = alt.Chart(datos).encode(
        x=alt.X("hora:T", title=None,
                axis=alt.Axis(format="%H:%M", grid=False, domain=False, tickCount=6, labelOpacity=0.6)),
        y=alt.Y("p:Q", title=None, scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format=".0%", orient="right", values=[0, 0.25, 0.5, 0.75, 1],
                              gridDash=[2, 3], gridOpacity=0.35, domain=False, ticks=False, labelOpacity=0.6)),
        color=alt.Color("serie:N", scale=colores, legend=None),
    )
    linea_mercado = base.transform_filter(alt.datum.fuente == "Mercado").mark_line(
        interpolate="step-after", strokeWidth=2.2
    )
    linea_modelo = base.transform_filter(alt.datum.fuente == "Modelo").mark_line(
        strokeDash=[5, 4], strokeWidth=1.6, opacity=0.85
    )
    # separa las etiquetas finales que quedan encimadas (minimo 6 puntos entre una y otra)
    ultimo = ultimo.sort_values("p").copy()
    posiciones = []
    for p in ultimo["p"]:
        posiciones.append(max(p, posiciones[-1] + 0.06) if posiciones else p)
    ultimo["p_etiqueta"] = posiciones
    etiquetas = alt.Chart(ultimo).mark_text(align="left", dx=6, fontWeight="bold", fontSize=12).encode(
        x="hora:T", y="p_etiqueta:Q", text=alt.Text("p:Q", format=".0%"),
        color=alt.Color("serie:N", scale=colores, legend=None),
    )
    # crosshair: linea vertical + puntos en el momento mas cercano al mouse
    cerca = alt.selection_point(nearest=True, on="pointerover", fields=["hora"], empty=False, clear="pointerout")
    selector = alt.Chart(datos).mark_rule(opacity=0).encode(x="hora:T").add_params(cerca)
    regla = alt.Chart(datos).mark_rule(color="#8b95a5", strokeWidth=1).encode(x="hora:T").transform_filter(cerca)
    puntos = base.mark_point(filled=True, size=45).transform_filter(cerca).encode(
        tooltip=[alt.Tooltip("serie:N", title="Resultado"), alt.Tooltip("fuente:N", title="Serie"),
                 alt.Tooltip("p:Q", title="Prob.", format=".1%"),
                 alt.Tooltip("hora:T", title="Hora", format="%H:%M")]
    )
    grafica = (linea_mercado + linea_modelo + etiquetas + selector + regla + puntos).properties(
        height=260
    ).configure_view(strokeWidth=0)
    st.altair_chart(grafica, width="stretch")
    st.caption("Línea continua: precio en Polymarket · punteada: probabilidad del modelo")


def registrar(partido, filas):
    ahora = datetime.now(HORA_MX).replace(tzinfo=None)
    hist = estado["historial"].setdefault(partido["match_id"], [])
    for f in filas:
        hist.append({
            "hora": ahora, "minuto": partido["minute"], "equipo": f["equipo"], "lado": f["lado"],
            "edge": f["edge"], "edge_pts": None if f["edge"] is None else f["edge"] * 100,
            "p_modelo": f["p_modelo"], "ask": f["ask"],
        })
        clave = (partido["match_id"], f["lado"])
        if f["senal"] == "BUENO" and estado["ultima_senal"].get(clave) != "BUENO":
            msg = (
                f"{ahora:%H:%M} · min {partido['minute']}' · {f['equipo']} "
                f"({partido['home']} vs {partido['away']}): modelo {f['p_modelo']:.1%} vs "
                f"mercado {f['ask']:.1%}, ML {fmt_ml(f['ml_mercado'])}"
            )
            estado["avisos"].insert(0, msg)
            st.toast(f"Buen momento: moneyline de {f['equipo']}", icon="✅")
        estado["ultima_senal"][clave] = f["senal"]


def panel():
    if demo:
        partido, precios = estado["demo"].step()
        lista = [(partido, precios)]
        if partido["status"] == "FINISHED":
            estado.pop("demo")  # en la siguiente vuelta empieza otro partido
            estado["historial"].pop("demo", None)
    else:
        try:
            lista = []
            for p in partidos_en_vivo(horas_previa * 60):
                try:
                    precios = precios_polymarket(p["match_id"], p["home"], p["away"], p["utc_date"])
                except Exception:
                    precios = {}
                lista.append((p, precios))
        except Exception as e:
            st.error(f"No se pudo consultar football-data.org: {e}. Se reintenta en {intervalo} s.")
            return

    if not lista:
        st.info(
            "No hay partidos de Champions en juego en este momento. El tablero revisa de nuevo "
            f"cada {intervalo} s; activa el modo demo en la barra lateral para ver un partido simulado."
        )

    for partido, precios in lista:
        lambdas = modelo().expected_goals(partido["home"], partido["away"])
        probs = live_probabilities(*lambdas, partido["minute"], partido["home_goals"], partido["away_goals"])
        filas = evaluate(partido, probs, precios, edge_min)
        registrar(partido, filas)
        tarjeta(partido, filas, lambdas, precios)

    st.subheader("Avisos")
    if estado["avisos"]:
        for a in estado["avisos"][:15]:
            st.markdown(f"- {a}")
    else:
        st.caption("Todavia no hay avisos en esta sesion.")
    st.caption(f"Actualizado {datetime.now(HORA_MX):%H:%M:%S} (hora CDMX) · siguiente en {intervalo} s")


st.fragment(run_every=intervalo)(panel)()
