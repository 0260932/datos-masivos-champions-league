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
from src.live.tracker import DemoTracker, PolymarketMoneyline, evaluate, fetch_live_matches

HORA_MX = timezone(timedelta(hours=-6))  # CDMX no tiene horario de verano desde 2022
COLORES_SENAL = {
    "BUENO": ("#15803d", "rgba(22,163,74,.16)"),
    "ESPERAR": ("#b45309", "rgba(217,119,6,.16)"),
    "NO": ("#b91c1c", "rgba(220,38,38,.14)"),
    "SIN MERCADO": ("#64748b", "rgba(100,116,139,.16)"),
}

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

def tarjeta(partido, filas, lambdas):
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

        hist = pd.DataFrame(estado["historial"].get(partido["match_id"], []))
        if len(hist) > 1 and hist["edge"].notna().any():
            base = alt.Chart(hist.dropna(subset=["edge"]))
            lineas = base.mark_line(point=True).encode(
                x=alt.X("hora:T", title=None, axis=alt.Axis(format="%H:%M")),
                y=alt.Y("edge_pts:Q", title="Ventaja (pts)"),
                color=alt.Color("equipo:N", title=None, legend=alt.Legend(orient="bottom")),
                tooltip=["equipo", "minuto", alt.Tooltip("edge_pts:Q", format="+.1f"),
                         alt.Tooltip("p_modelo:Q", format=".1%"), alt.Tooltip("ask:Q", format=".1%")],
            )
            umbral = alt.Chart(pd.DataFrame({"y": [edge_min * 100]})).mark_rule(
                strokeDash=[4, 4], color="#15803d"
            ).encode(y="y:Q")
            st.altair_chart((lineas + umbral).properties(height=180), width="stretch")


def registrar(partido, filas):
    ahora = datetime.now(HORA_MX).replace(tzinfo=None)
    hist = estado["historial"].setdefault(partido["match_id"], [])
    for f in filas:
        hist.append({
            "hora": ahora, "minuto": partido["minute"], "equipo": f["equipo"],
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
        tarjeta(partido, filas, lambdas)

    st.subheader("Avisos")
    if estado["avisos"]:
        for a in estado["avisos"][:15]:
            st.markdown(f"- {a}")
    else:
        st.caption("Todavia no hay avisos en esta sesion.")
    st.caption(f"Actualizado {datetime.now(HORA_MX):%H:%M:%S} (hora CDMX) · siguiente en {intervalo} s")


st.fragment(run_every=intervalo)(panel)()
