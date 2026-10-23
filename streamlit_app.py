"""
Dashboard web del tracker de moneyline (Champions League, La Liga, Premier League).

Muestra en una pagina que se actualiza sola los partidos en juego, la
probabilidad del modelo contra el precio de Polymarket y la senal
BUENO / ESPERAR / NO para local, empate y visita. Usa la misma logica
que src/live/tracker.py (modelo anclado al precio de antes del partido).

Ejecucion local:
    streamlit run streamlit_app.py

En Streamlit Community Cloud se configura FOOTBALL_DATA_API_TOKEN en
Settings > Secrets (ver README).
"""

import html
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

from src.live.modelo import StrengthModel, live_probabilities
from src.live.tracker import (
    COMPETICIONES,
    DemoTracker,
    PolymarketMoneyline,
    evaluate,
    fetch_live_matches,
    match_lambdas,
    price_history,
)

HORA_MX = timezone(timedelta(hours=-6))  # CDMX no tiene horario de verano desde 2022
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
EMBLEMAS = {"CL": "https://crests.football-data.org/CL.png", "PD": "https://crests.football-data.org/laliga.png",
    "PL": "https://crests.football-data.org/PL.png",
}
DEMO = {
    "home": "Arsenal FC", "away": "FC Bayern München",
    "home_short": "Arsenal", "away_short": "Bayern",
    "home_crest": "https://crests.football-data.org/57.png",
    "away_crest": "https://crests.football-data.org/5.png",
}

# Colores de cada resultado (lineas de la grafica, barras y puntos)
COLORES_LADO = {"home": "#4f8cff", "draw": "#a3afc7", "away": "#ffb23f"}
# Rango de la grafica -> (horas hacia atras o None = todo, fidelidad en minutos)
RANGOS = {"Partido": (None, 1), "6H": (6, 1), "1D": (24, 5), "1S": (24 * 7, 60), "Todo": (None, 180)}

st.set_page_config(page_title="Moneyline en vivo", page_icon="⚽", layout="wide")

# Estadio de noche: fondo azul marino, marcador en tipografia condensada de tablero,
# un color por resultado y verde/ambar/rojo solo para las senales.
st.markdown(
    """<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=Barlow:wght@400;500;600;700&display=swap');
:root{--bg:#0a1020;--surface:#111a2e;--raised:#17223b;--line:#22304f;--text:#eaf0ff;--muted:#8796b8;
--home:#4f8cff;--draw:#a3afc7;--away:#ffb23f;--good:#2fd17a;--warn:#f5b400;--bad:#ff5d5d;--live:#ff4757;
--display:'Barlow Condensed','Arial Narrow',sans-serif;--body:'Barlow',system-ui,sans-serif}
html,body,[data-testid="stAppViewContainer"],.stMarkdown,p,label,li{font-family:var(--body)}
[data-testid="stAppViewContainer"]{background:radial-gradient(1200px 500px at 50% -200px,#1a2a52 0%,var(--bg) 60%) var(--bg)}
header[data-testid="stHeader"]{background:transparent}
[data-testid="stToolbarActions"],#MainMenu,footer{display:none}
[data-testid="stSidebar"]{background:var(--surface);border-right:1px solid var(--line)}
.block-container{padding-top:2rem;max-width:1180px}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mc){background:var(--surface);border:1px solid var(--line)!important;
 border-radius:18px;box-shadow:0 18px 40px -24px rgba(0,0,0,.8)}
.brand{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand-logo{width:52px;height:52px;border-radius:14px;background:#fff;display:grid;place-items:center;flex:none}
.brand-logo img{width:38px;height:38px;object-fit:contain}
.brand h1{font-family:var(--display);font-weight:800;font-size:2.4rem;letter-spacing:.01em;margin:0;line-height:1;color:var(--text);padding:0}
.brand p{margin:2px 0 0;color:var(--muted);font-size:.92rem}
.brand-live{margin-left:auto;color:var(--muted);font-size:.85rem;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good);margin-right:6px;
 box-shadow:0 0 0 0 rgba(47,209,122,.6);animation:pulse 2s infinite}
.dot.live{background:var(--live);box-shadow:0 0 0 0 rgba(255,71,87,.6)}
@keyframes pulse{70%{box-shadow:0 0 0 8px rgba(0,0,0,0)}100%{box-shadow:0 0 0 0 rgba(0,0,0,0)}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0 6px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 16px}
.kpi span{display:block;color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.09em}
.kpi b{font-family:var(--display);font-size:1.9rem;font-weight:700;color:var(--text);font-variant-numeric:tabular-nums}
.kpi small{color:var(--muted);font-size:.85rem;margin-left:6px;white-space:nowrap}
.mc-top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:.72rem;
 font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-variant-numeric:tabular-nums}
.chip.live{color:var(--live);background:rgba(255,71,87,.12)}
.chip.pre{color:var(--home);background:rgba(79,140,255,.12)}
.chip.end{color:var(--muted);background:rgba(135,150,184,.14)}
.src{color:var(--muted);font-size:.78rem}
.sb{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;margin:14px 0 18px}
.team{display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;min-width:0}
.crest{width:64px;height:64px;border-radius:50%;background:var(--raised);border:1px solid var(--line);display:grid;
 place-items:center;position:relative;overflow:hidden;flex:none}
.crest img{width:44px;height:44px;object-fit:contain;position:absolute}
.crest i{font-style:normal;font-family:var(--display);font-weight:700;color:var(--muted)}
.tname{font-family:var(--display);font-weight:700;font-size:1.35rem;color:var(--text);line-height:1.05}
.score{font-family:var(--display);font-weight:800;font-size:3.6rem;color:var(--text);line-height:1;
 font-variant-numeric:tabular-nums;display:flex;gap:14px;align-items:center}
.score span{color:var(--muted);font-weight:500;font-size:2.4rem}
.score.pre{font-size:1.5rem;color:var(--muted);font-weight:600;flex-direction:column;gap:0;text-align:center}
.outs{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.out{background:var(--raised);border:1px solid var(--line);border-radius:14px;padding:12px 14px;position:relative}
.out.s-BUENO{border-color:rgba(47,209,122,.7);box-shadow:0 0 0 1px rgba(47,209,122,.35),0 0 24px -8px rgba(47,209,122,.6)}
.out-h{display:flex;align-items:center;gap:8px;min-width:0}
.out-h .nm{font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.mini{width:22px;height:22px;object-fit:contain;flex:none}
.xdraw{width:22px;height:22px;border-radius:50%;background:var(--line);color:var(--text);font-family:var(--display);
 font-weight:700;font-size:.8rem;display:grid;place-items:center;flex:none}
.sig{padding:2px 8px;border-radius:999px;font-size:.66rem;font-weight:800;letter-spacing:.08em;flex:none}
.sig.s-BUENO{color:#06240f;background:var(--good)}
.sig.s-ESPERAR{color:var(--warn);background:rgba(245,180,0,.13)}
.sig.s-NO{color:var(--bad);background:rgba(255,93,93,.12)}
.sig.s-CERRADO,.sig.s-SIN{color:var(--muted);background:rgba(135,150,184,.14)}
.edge{font-family:var(--display);font-weight:700;font-size:2rem;margin:6px 0 2px;font-variant-numeric:tabular-nums;color:var(--text)}
.edge small{font-size:.95rem;color:var(--muted);margin-left:4px;font-weight:600}
.edge.pos{color:var(--good)}.edge.neg{color:var(--bad)}
.bar{position:relative;height:8px;border-radius:99px;background:var(--line);margin:6px 0 10px}
.bar .m{position:absolute;left:0;top:0;bottom:0;border-radius:99px}
.bar .md{position:absolute;top:-4px;width:3px;height:16px;border-radius:2px;background:var(--text);transform:translateX(-1px)}
.row{display:flex;justify-content:space-between;font-size:.86rem;padding:2px 0;font-variant-numeric:tabular-nums}
.row span{color:var(--muted)}.row b{color:var(--text);font-weight:600}
.pm-head{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;margin:20px 0 0}
.pm-lider{font-family:var(--display);font-size:2.1rem;font-weight:800;color:var(--text);line-height:1;font-variant-numeric:tabular-nums}
.pm-lider small{font-family:var(--body);font-size:.9rem;font-weight:600;color:var(--muted);margin-left:8px}
.pm-chip{display:inline-flex;align-items:center;gap:6px;font-size:.85rem;color:var(--muted);font-variant-numeric:tabular-nums}
.pm-chip b{color:var(--text)}
.pm-dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.legend{color:var(--muted);font-size:.78rem;margin-top:-6px}
.legend i{display:inline-block;width:18px;border-top:2px solid var(--muted);vertical-align:middle;margin:0 6px 0 10px}
.legend i.d{border-top-style:dashed}
.sec{font-family:var(--display);font-weight:700;font-size:1.4rem;color:var(--text);margin:26px 0 10px;letter-spacing:.02em}
.feed{display:flex;flex-direction:column;gap:8px}
.ev{display:flex;gap:12px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 14px}
.ev .t{font-family:var(--display);font-weight:700;color:var(--good);font-size:1.05rem;flex:none;font-variant-numeric:tabular-nums}
.ev .tx{color:var(--text);font-size:.9rem;min-width:0}
.ev .tx span{color:var(--muted)}
.empty{background:var(--surface);border:1px dashed var(--line);border-radius:16px;padding:22px;color:var(--muted)}
.empty b{color:var(--text);font-family:var(--display);font-size:1.4rem;display:block;margin-bottom:4px}
.fx{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:10px;background:var(--surface);
 border:1px solid var(--line);border-radius:12px;padding:10px 14px}
.fx .h,.fx .a{display:flex;align-items:center;gap:8px;min-width:0;color:var(--text);font-weight:600}
.fx .a{justify-content:flex-end;text-align:right}
.fx .h span,.fx .a span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fx .k{font-family:var(--display);font-weight:700;color:var(--muted);text-align:center;font-size:.95rem}
.foot{color:var(--muted);font-size:.78rem;margin-top:18px}
@media (max-width:760px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:640px){.crest{width:48px;height:48px}.crest img{width:34px;height:34px}
 .tname{font-size:1.05rem}.score{font-size:2.6rem}.brand h1{font-size:1.8rem}.brand-live{margin-left:0;width:100%}}
</style>""",
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
def polymarket(competicion):
    return PolymarketMoneyline(competicion)


@st.cache_data(ttl=20, show_spinner=False)
def partidos_en_vivo(minutos_previa, competicion):
    return fetch_live_matches(minutos_previa, competicion)


@st.cache_data(ttl=300, show_spinner=False)
def proximos_partidos(competicion):
    return fetch_live_matches(7 * 24 * 60, competicion)


@st.cache_data(ttl=20, show_spinner=False)
def precios_polymarket(match_id, home, away, utc_date, competicion):
    partido = {"match_id": match_id, "home": home, "away": away, "utc_date": utc_date}
    return polymarket(competicion).prices(partido)


@st.cache_data(ttl=30, show_spinner=False)
def historial_polymarket(token_id, start_ts, fidelity):
    return price_history(token_id, start_ts, fidelity)


def fmt_ml(v):
    return "–" if v is None else f"{v:+d}"


def fmt_pct(v):
    return "–" if v is None else f"{v:.1%}"


def fecha_mx(dt):
    local = dt.astimezone(HORA_MX)
    return f"{DIAS[local.weekday()]} {local.day} {MESES[local.month - 1]} · {local:%H:%M}"


def esc(texto):
    return html.escape(str(texto or ""))


def nombre_corto(partido, lado):
    return partido.get(f"{lado}_short") or partido[lado]


def escudo(partido, lado):
    iniciales = "".join(p[0] for p in nombre_corto(partido, lado).split()[:2]).upper()
    img = f"<img src='{esc(partido.get(lado + '_crest'))}' alt=''>" if partido.get(f"{lado}_crest") else ""
    return f"<div class='crest'><i>{esc(iniciales)}</i>{img}</div>"


def mini_escudo(partido, lado):
    if lado == "draw":
        return "<span class='xdraw'>X</span>"
    url = partido.get(f"{lado}_crest")
    return f"<img class='mini' src='{esc(url)}' alt=''>" if url else "<span class='xdraw'>·</span>"


# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Ajustes")
    competicion = st.selectbox("Competición", list(COMPETICIONES), format_func=lambda c: COMPETICIONES[c][0])
    edge_min = st.slider("Ventaja mínima para BUENO (puntos)", 2, 15, 5) / 100
    intervalo = st.select_slider("Actualizar cada", [5, 15, 30, 60, 120], value=30, format_func=lambda s: f"{s} s")
    horas_previa = st.slider("Incluir partidos que empiezan en las próximas (horas)", 0, 48, 2)
    rango = st.segmented_control("Rango de la gráfica", list(RANGOS), default="Partido") or "Partido"
    demo = st.toggle("Modo demo (partido simulado)", help="Simula un partido para ver cómo se comporta el tablero.")
    st.caption(
        "Antes del partido el modelo copia el precio de Polymarket. Durante el partido lo mueve "
        "con el marcador y el tiempo restante (Poisson). BUENO = modelo − precio de compra ≥ ventaja mínima: "
        "el mercado todavía no reacciona a lo que pasó en la cancha."
    )

estado = st.session_state
estado.setdefault("historial", {})
estado.setdefault("ultima_senal", {})
estado.setdefault("avisos", [])
if demo and "demo" not in estado:
    estado["demo"] = DemoTracker(edge_min, False, DEMO["home"], DEMO["away"])
    estado["demo"].partido.update(DEMO)
if not demo:
    estado.pop("demo", None)

nombre_comp = COMPETICIONES[competicion][0]


# ---------------------------------------------------------------------------
# Piezas de la pagina
# ---------------------------------------------------------------------------

def encabezado(lista):
    en_vivo = sum(1 for p, _ in lista if p["status"] in ("IN_PLAY", "PAUSED", "LIVE"))
    buenos = [f for _, _, filas in estado.get("_filas", []) for f in filas if f["senal"] == "BUENO"]
    mejor = max(
        ((p, f) for p, _, filas in estado.get("_filas", []) for f in filas if f["edge"] is not None),
        key=lambda par: par[1]["edge"], default=None,
    )
    punto = "<span class='dot live'></span>" if en_vivo else "<span class='dot'></span>"
    mejor_txt = "–" if mejor is None else f"{mejor[1]['edge'] * 100:+.1f}"
    if mejor is None:
        mejor_eq = ""
    else:
        mejor_eq = "Empate" if mejor[1]["lado"] == "draw" else esc(nombre_corto(mejor[0], mejor[1]["lado"]))
    st.markdown(
        "<div class='brand'>"
        f"<div class='brand-logo'><img src='{EMBLEMAS[competicion]}' alt=''></div>"
        f"<div><h1>Moneyline en vivo</h1><p>{esc(nombre_comp)} · modelo vs. Polymarket</p></div>"
        f"<div class='brand-live'>{punto}Actualizado {datetime.now(HORA_MX):%H:%M:%S} · cada {intervalo} s</div>"
        "</div>"
        "<div class='kpis'>"
        f"<div class='kpi'><span>Partidos</span><b>{len(lista)}</b><small>{en_vivo} en vivo</small></div>"
        f"<div class='kpi'><span>Señales BUENO</span><b>{len(buenos)}</b><small>ahora</small></div>"
        f"<div class='kpi'><span>Mayor ventaja</span><b>{mejor_txt}</b><small>{mejor_eq}</small></div>"
        f"<div class='kpi'><span>Avisos de la sesión</span><b>{len(estado['avisos'])}</b></div>"
        "</div>",
        unsafe_allow_html=True,
    )


def chip_estado(partido):
    if partido["status"] in ("TIMED", "SCHEDULED"):
        return f"<span class='chip pre'>Previa · {fecha_mx(partido['utc_date'])}</span>"
    if partido["status"] == "PAUSED":
        return "<span class='chip live'><span class='dot live'></span>Medio tiempo</span>"
    if partido["status"] == "FINISHED":
        return "<span class='chip end'>Final</span>"
    return f"<span class='chip live'><span class='dot live'></span>En vivo · {partido['minute']}'</span>"


def tarjeta(partido, filas, lambdas, fuente, precios):
    if partido["status"] in ("TIMED", "SCHEDULED"):
        centro = f"<div class='score pre'>vs<small>{partido['utc_date'].astimezone(HORA_MX):%H:%M}</small></div>"
    else:
        centro = f"<div class='score'>{partido['home_goals']}<span>–</span>{partido['away_goals']}</div>"
    origen = "Modelo anclado al precio de antes del partido" if fuente == "mercado" else "Modelo con estadísticas del proyecto"

    tiles = []
    for f in filas:
        lado = f["lado"]
        nombre = "Empate" if lado == "draw" else nombre_corto(partido, lado)
        clase_sig = "SIN" if f["senal"] == "SIN MERCADO" else f["senal"]
        if f["edge"] is None:
            edge = "<div class='edge'>–</div>"
        else:
            signo = "pos" if f["edge"] >= edge_min else ("neg" if f["edge"] <= -edge_min else "")
            edge = f"<div class='edge {signo}'>{f['edge'] * 100:+.1f}<small>pts</small></div>"
        ancho = 0 if f["ask"] is None else f["ask"] * 100
        tiles.append(
            f"<div class='out s-{clase_sig}'>"
            f"<div class='out-h'>{mini_escudo(partido, lado)}<span class='nm'>{esc(nombre)}</span>"
            f"<span class='sig s-{clase_sig}'>{esc(f['senal'])}</span></div>"
            f"{edge}"
            f"<div class='bar'><div class='m' style='width:{ancho:.1f}%;background:{COLORES_LADO[lado]}'></div>"
            f"<div class='md' style='left:{f['p_modelo'] * 100:.1f}%'></div></div>"
            f"<div class='row'><span>Precio de compra</span><b>{fmt_pct(f['ask'])}</b></div>"
            f"<div class='row'><span>Modelo</span><b>{fmt_pct(f['p_modelo'])}</b></div>"
            f"<div class='row'><span>Momio merc. / justo</span><b>{fmt_ml(f['ml_mercado'])} / {fmt_ml(f['ml_justo'])}</b></div>"
            f"<div class='row'><span>¼ Kelly</span><b>{f['kelly_25']:.1%}</b></div>"
            "</div>"
        )

    with st.container(border=True):
        st.markdown(
            "<div class='mc'>"
            f"<div class='mc-top'>{chip_estado(partido)}<span class='src'>{origen} · goles esperados "
            f"{lambdas[0]:.2f}–{lambdas[1]:.2f}</span></div>"
            "<div class='sb'>"
            f"<div class='team'>{escudo(partido, 'home')}<div class='tname'>{esc(nombre_corto(partido, 'home'))}</div></div>"
            f"{centro}"
            f"<div class='team'>{escudo(partido, 'away')}<div class='tname'>{esc(nombre_corto(partido, 'away'))}</div></div>"
            "</div>"
            f"<div class='outs'>{''.join(tiles)}</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        grafica_polymarket(partido, filas, precios)


def serie_mercado(partido, precios, lado):
    """Historial del precio del resultado: de Polymarket si hay token, si no el visto en esta sesion."""
    token = precios.get(lado, {}).get("token_id")
    if token:
        horas, fidelidad = RANGOS[rango]
        ahora = datetime.now(timezone.utc)
        if rango == "Partido":
            # en juego: desde 1 h antes del inicio; en previa: las ultimas 24 h
            if partido["utc_date"] <= ahora:
                inicio = partido["utc_date"] - timedelta(hours=1)
            else:
                inicio = ahora - timedelta(hours=24)
        elif horas is not None:
            inicio = ahora - timedelta(hours=horas)
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
    orden = ("home", "draw", "away")
    nombres = {lado: ("Empate" if lado == "draw" else nombre_corto(partido, lado)) for lado in orden}
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
            f"{esc(nombres[lado])} <b>{actuales[nombres[lado]]:.0%}</b></span>"
            for lado in orden if nombres[lado] in actuales
        )
        st.markdown(
            f"<div class='pm-head'><span class='pm-lider'>{actuales[lider]:.0%}<small>{esc(lider)}</small></span>"
            f"{chips}</div>",
            unsafe_allow_html=True,
        )

    colores = alt.Scale(domain=[nombres[l] for l in orden], range=[COLORES_LADO[l] for l in orden])
    base = alt.Chart(datos).encode(
        x=alt.X("hora:T", title=None,
                axis=alt.Axis(format="%d/%m" if rango in ("1S", "Todo") else "%H:%M",
                              grid=False, domain=False, tickCount=6,
                              labelColor="#8796b8", tickColor="#22304f")),
        y=alt.Y("p:Q", title=None, scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format=".0%", orient="right", values=[0, 0.25, 0.5, 0.75, 1],
                              gridDash=[2, 4], gridColor="#22304f", domain=False, ticks=False,
                              labelColor="#8796b8")),
        color=alt.Color("serie:N", scale=colores, legend=None),
    )
    linea_mercado = base.transform_filter(alt.datum.fuente == "Mercado").mark_line(
        interpolate="basis", strokeWidth=2.6, strokeCap="round", strokeJoin="round"
    )
    linea_modelo = base.transform_filter(alt.datum.fuente == "Modelo").mark_line(
        interpolate="basis", strokeDash=[5, 4], strokeWidth=1.6, opacity=0.9
    )
    # separa las etiquetas finales que quedan encimadas (minimo 6 puntos entre una y otra)
    ultimo = ultimo.sort_values("p").copy()
    posiciones = []
    for p in ultimo["p"]:
        posiciones.append(max(p, posiciones[-1] + 0.06) if posiciones else p)
    ultimo["p_etiqueta"] = posiciones
    etiquetas = alt.Chart(ultimo).mark_text(
        align="left", dx=6, fontWeight="bold", fontSize=13, font="Barlow Condensed"
    ).encode(
        x="hora:T", y="p_etiqueta:Q", text=alt.Text("p:Q", format=".0%"),
        color=alt.Color("serie:N", scale=colores, legend=None),
    )
    # crosshair: linea vertical + puntos en el momento mas cercano al mouse
    cerca = alt.selection_point(nearest=True, on="pointerover", fields=["hora"], empty=False, clear="pointerout")
    selector = alt.Chart(datos).mark_rule(opacity=0).encode(x="hora:T").add_params(cerca)
    regla = alt.Chart(datos).mark_rule(color="#8796b8", strokeWidth=1).encode(x="hora:T").transform_filter(cerca)
    puntos = base.mark_point(filled=True, size=50).transform_filter(cerca).encode(
        tooltip=[alt.Tooltip("serie:N", title="Resultado"), alt.Tooltip("fuente:N", title="Serie"),
                 alt.Tooltip("p:Q", title="Prob.", format=".1%"),
                 alt.Tooltip("hora:T", title="Hora", format="%d/%m %H:%M")]
    )
    grafica = (linea_mercado + linea_modelo + etiquetas + selector + regla + puntos).properties(
        height=250, background="transparent"
    ).configure_view(strokeWidth=0)
    st.altair_chart(grafica, width="stretch", theme=None)
    st.markdown(
        "<div class='legend'><i></i>Precio en Polymarket<i class='d'></i>Probabilidad del modelo</div>",
        unsafe_allow_html=True,
    )


def registrar(partido, filas):
    ahora = datetime.now(HORA_MX).replace(tzinfo=None)
    hist = estado["historial"].setdefault(partido["match_id"], [])
    for f in filas:
        hist.append({
            "hora": ahora, "minuto": partido["minute"], "equipo": f["equipo"], "lado": f["lado"],
            "edge": f["edge"], "p_modelo": f["p_modelo"], "ask": f["ask"],
        })
        clave = (partido["match_id"], f["lado"])
        if f["senal"] == "BUENO" and estado["ultima_senal"].get(clave) != "BUENO":
            estado["avisos"].insert(0, {
                "hora": f"{ahora:%H:%M}", "minuto": partido["minute"], "lado": f["lado"],
                "partido": dict(partido), "equipo": f["equipo"], "p_modelo": f["p_modelo"],
                "ask": f["ask"], "edge": f["edge"], "ml": f["ml_mercado"],
            })
            st.toast(f"Buen momento: moneyline de {f['equipo']}", icon="✅")
        estado["ultima_senal"][clave] = f["senal"]


def avisos():
    st.markdown("<div class='sec'>Avisos</div>", unsafe_allow_html=True)
    if not estado["avisos"]:
        st.markdown(
            "<div class='empty'>Aquí aparece cada momento en que un resultado pasa a BUENO, "
            "con el minuto y los precios de ese instante.</div>",
            unsafe_allow_html=True,
        )
        return
    items = []
    for a in estado["avisos"][:15]:
        p = a["partido"]
        items.append(
            "<div class='ev'>"
            f"<span class='t'>{a['minuto']}'</span>{mini_escudo(p, a['lado'])}"
            f"<div class='tx'><b>{esc(a['equipo'])}</b> · modelo {a['p_modelo']:.1%} vs precio {a['ask']:.1%} "
            f"(<b>{a['edge'] * 100:+.1f} pts</b>, momio {fmt_ml(a['ml'])})"
            f"<br><span>{a['hora']} · {esc(nombre_corto(p, 'home'))} vs {esc(nombre_corto(p, 'away'))}</span></div>"
            "</div>"
        )
    st.markdown(f"<div class='feed'>{''.join(items)}</div>", unsafe_allow_html=True)


def sin_partidos():
    st.markdown(
        f"<div class='empty'><b>No hay partidos de {esc(nombre_comp)} en juego</b>"
        f"El tablero revisa de nuevo cada {intervalo} s. Activa el modo demo en la barra lateral "
        "para ver un partido simulado.</div>",
        unsafe_allow_html=True,
    )
    try:
        proximos = sorted(proximos_partidos(competicion), key=lambda p: p["utc_date"])[:6]
    except Exception:
        proximos = []
    if proximos:
        filas = "".join(
            "<div class='fx'>"
            f"<div class='h'>{mini_escudo(p, 'home')}<span>{esc(nombre_corto(p, 'home'))}</span></div>"
            f"<div class='k'>{fecha_mx(p['utc_date'])}</div>"
            f"<div class='a'><span>{esc(nombre_corto(p, 'away'))}</span>{mini_escudo(p, 'away')}</div>"
            "</div>"
            for p in proximos
        )
        st.markdown(
            f"<div class='sec'>Próximos partidos</div><div class='feed'>{filas}</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Panel que se refresca solo
# ---------------------------------------------------------------------------

def panel():
    if demo:
        partido, precios = estado["demo"].step()
        lista = [(partido, precios)]
        if partido["status"] == "FINISHED":
            estado.pop("demo")  # en la siguiente vuelta empieza otro partido
    else:
        try:
            lista = []
            for p in partidos_en_vivo(horas_previa * 60, competicion):
                try:
                    precios = precios_polymarket(p["match_id"], p["home"], p["away"], p["utc_date"], competicion)
                except Exception:
                    precios = {}
                lista.append((p, precios))
        except Exception as e:
            st.error(f"No se pudo consultar football-data.org ({e}). Se reintenta en {intervalo} s.")
            return

    calculados = []
    for partido, precios in lista:
        lambdas, fuente = match_lambdas(modelo(), partido, precios)
        probs = live_probabilities(*lambdas, partido["minute"], partido["home_goals"], partido["away_goals"])
        filas = evaluate(partido, probs, precios, edge_min)
        registrar(partido, filas)
        calculados.append((partido, precios, filas, lambdas, fuente))
    estado["_filas"] = [(p, pr, f) for p, pr, f, _, _ in calculados]

    encabezado(lista)
    if not lista:
        sin_partidos()
    for partido, precios, filas, lambdas, fuente in calculados:
        tarjeta(partido, filas, lambdas, fuente, precios)
    avisos()
    st.markdown(
        "<div class='foot'>Datos: football-data.org (marcador) y Polymarket (precios). "
        "Las señales son una referencia del modelo, no una garantía.</div>",
        unsafe_allow_html=True,
    )
    if demo and "demo" not in estado:
        estado["historial"].pop("demo", None)


st.fragment(run_every=intervalo)(panel)()
