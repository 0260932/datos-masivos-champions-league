"""
Dashboard web del tracker de moneyline (10 ligas, ver COMPETICIONES).

Muestra en una pagina que se actualiza sola los partidos en juego, la
probabilidad del modelo contra el precio de Polymarket y la senal
BUENO / ESPERAR / NO para local, empate y visita. Usa la misma logica
que src/live/tracker.py (modelo anclado al precio de antes del partido,
actualizado en vivo con xG y con el ruido del mercado estimado con GARCH).

Ejecucion local:
    streamlit run streamlit_app.py

En Streamlit Community Cloud se configura FOOTBALL_DATA_API_TOKEN en
Settings > Secrets (ver README).
"""

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from src.live.modelo import StrengthModel
from src.models.xgboost_resultado import PrediccionResultado
from src.live.tracker import (
    COMPETICIONES,
    DemoTracker,
    PolymarketMoneyline,
    analizar_partido,
    fetch_all_live_matches,
    fetch_live_matches,
    price_history,
)

HORA_MX = timezone(timedelta(hours=-6))  # CDMX no tiene horario de verano desde 2022
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
TODAS = "TODAS"  # vista con los partidos de todas las ligas
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
# Intervalo (min) de la malla de la grafica por rango: suficientes puntos para ver los
# movimientos, pocos para que la curva no se vea serruchada.
PASO_GRAFICA = {"Partido": 2, "6H": 4, "1D": 15, "1S": 90, "Todo": 360}

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
.edge small{font-family:var(--body);font-size:.78rem;color:var(--muted);margin-left:6px;font-weight:600}
.edge.pos{color:var(--good)}.edge.neg{color:var(--bad)}
.bar{position:relative;height:8px;border-radius:99px;background:var(--line);margin:6px 0 10px}
.bar .m{position:absolute;left:0;top:0;bottom:0;border-radius:99px}
.bar .md{position:absolute;top:-4px;width:3px;height:16px;border-radius:2px;background:var(--text);transform:translateX(-1px)}
.row{display:flex;justify-content:space-between;gap:8px;font-size:.86rem;padding:2px 0;font-variant-numeric:tabular-nums}
.row em{font-style:normal;color:var(--muted);font-weight:500;margin-left:4px}
.bar .rg{position:absolute;top:-3px;height:14px;border-radius:4px;background:rgba(234,240,255,.16);
 border:1px solid rgba(234,240,255,.35)}
.mc{scroll-margin-top:80px}
.mc:target{animation:flash 2.4s ease-out}
@keyframes flash{0%{box-shadow:0 0 0 3px var(--good)}100%{box-shadow:0 0 0 0 transparent}}
.liga{font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.share{color:var(--muted);font-size:.75rem;text-decoration:none;border-bottom:1px dotted var(--muted)}
.vivo{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;background:var(--raised);
 border:1px solid var(--line);border-radius:12px;padding:8px 14px;margin:0 0 12px;font-size:.86rem;color:var(--muted);
 font-variant-numeric:tabular-nums}
.vivo b{color:var(--text)}
.vivo .t{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text);font-weight:700}
.chip.warn{color:var(--warn);background:rgba(245,180,0,.13)}
.stats{display:grid;gap:8px;margin:0 0 12px}
.st{display:grid;grid-template-columns:48px 1fr 48px;align-items:center;gap:10px;font-variant-numeric:tabular-nums}
.st b{color:var(--text);font-weight:700;font-size:.9rem}
.st b:last-child{text-align:right}
.st .c{display:flex;flex-direction:column;gap:3px;text-align:center;color:var(--muted);font-size:.76rem}
.st .duo{display:flex;height:6px;border-radius:99px;overflow:hidden;background:var(--line);gap:2px}
.st .duo i{display:block;height:100%}
.mom-t{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text);font-weight:700;margin:4px 0 -6px}
a.ev{text-decoration:none;color:inherit}
a.ev:hover{border-color:var(--muted)}
.tabla{border-collapse:collapse;margin:.4rem 0 .8rem;font-size:.85rem;font-variant-numeric:tabular-nums}
.tabla th,.tabla td{border-bottom:1px solid var(--line);padding:4px 10px;text-align:right;color:var(--text)}
.tabla th{color:var(--muted);font-weight:600}
.tabla td:first-child,.tabla th:first-child{text-align:left}
.why{margin-top:10px;padding-top:10px;border-top:1px solid var(--line);font-size:.82rem;line-height:1.45;color:var(--muted)}
.why b{display:block;color:var(--text);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
.razones{display:flex;flex-wrap:wrap;gap:5px}
.razones span{background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:2px 9px;
 font-size:.76rem;color:var(--text);white-space:nowrap;font-variant-numeric:tabular-nums}
.razones span.clave{border-color:currentColor;font-weight:700}
.s-BUENO .razones span.clave{color:var(--good)}
.s-NO .razones span.clave{color:var(--bad)}
.s-ESPERAR .razones span.clave{color:var(--warn)}
.why details{margin-top:6px}
.why summary{cursor:pointer;font-size:.74rem;color:var(--muted);list-style:none}
.why summary::-webkit-details-marker{display:none}
.why summary::before{content:"▸ "}
.why details[open] summary::before{content:"▾ "}
.why details p{margin:6px 0 0;font-size:.8rem;line-height:1.45}
.metodo{color:var(--muted);font-size:.92rem;line-height:1.55;max-width:75ch}
.metodo b{color:var(--text)}
.metodo ol{padding-left:1.2rem;margin:.4rem 0}
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
def xgboost():
    return PrediccionResultado()


@st.cache_resource
def polymarket(competicion):
    return PolymarketMoneyline(competicion)


@st.cache_data(ttl=20, show_spinner=False)
def partidos_en_vivo(minutos_previa, competicion):
    if competicion == TODAS:
        return fetch_all_live_matches(minutos_previa)
    return fetch_live_matches(minutos_previa, competicion)


@st.cache_data(ttl=300, show_spinner=False)
def proximos_partidos(competicion):
    return partidos_en_vivo(7 * 24 * 60, competicion)


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


def nombre_liga(codigo):
    return "Todas las ligas" if codigo == TODAS else COMPETICIONES[codigo][0]


def ancla(partido):
    return f"partido-{partido['match_id']}"


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
    opciones = [TODAS, *COMPETICIONES]
    liga_link = st.query_params.get("liga")
    competicion = st.selectbox(
        "Competición", opciones, index=opciones.index(liga_link) if liga_link in opciones else 0,
        format_func=nombre_liga,
    )
    intervalo = st.select_slider("Actualizar cada", [5, 15, 30, 60, 120], value=30, format_func=lambda s: f"{s} s")
    horas_previa = st.slider("Incluir partidos que empiezan en las próximas (horas)", 0, 48, 2)
    rango = st.segmented_control("Rango de la gráfica", list(RANGOS), default="Partido") or "Partido"
    demo = st.toggle("Modo demo (partido simulado)", help="Simula un partido para ver cómo se comporta el tablero.")
    st.caption(
        "La ventaja mínima no se configura: la calcula el modelo en cada momento según su propia "
        "incertidumbre. Abre «Cómo decide la plataforma» para ver el método."
    )

estado = st.session_state
estado.setdefault("historial", {})
estado.setdefault("ultima_senal", {})
estado.setdefault("avisos", [])
if demo and "demo" not in estado:
    estado["demo"] = DemoTracker(False, DEMO["home"], DEMO["away"])
    estado["demo"].partido.update(DEMO)
if not demo:
    estado.pop("demo", None)

nombre_comp = nombre_liga(competicion)
partido_link = st.query_params.get("partido")


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
        + (
            "<div class='brand-logo' style='font-size:1.9rem'>⚽</div>" if competicion == TODAS
            else f"<div class='brand-logo'><img src='{COMPETICIONES[competicion][3]}' alt=''></div>"
        ) +
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
    if partido["status"] == "SIN_DATOS":
        return "<span class='chip end'>Empezó · sin marcador en vivo</span>"
    if partido["status"] == "FINISHED":
        return "<span class='chip end'>Final</span>"
    return f"<span class='chip live'><span class='dot live'></span>En vivo · {partido['minute']}'</span>"


def tira_vivo(partido, analisis):
    """xG en vivo, tiros a puerta, rojas y ruido del mercado (GARCH) del partido."""
    xg, filas = analisis["xg"], analisis["filas"]
    partes = []
    if xg:
        local, visita = esc(nombre_corto(partido, "home")), esc(nombre_corto(partido, "away"))
        total = xg["home"].get("presion", 0) + xg["away"].get("presion", 0)
        if total >= 0.05:
            domina = local if xg["home"]["presion"] >= xg["away"]["presion"] else visita
            parte = max(xg["home"]["presion"], xg["away"]["presion"]) / total
            partes.append(f"<span class='t'>Momentum</span><span>presiona <b>{domina}</b> ({parte:.0%} de la presión reciente)</span>")
    ruidos = [f for f in filas if f.get("ruido_metodo")]
    if ruidos:
        maximo = max(f["ruido"] for f in ruidos)
        metodo = "GARCH" if any(f["ruido_metodo"] == "garch" for f in ruidos) else "EWMA"
        partes.append(f"<span>Ruido del mercado ({metodo}) <b>±{maximo * 100:.1f} pts</b></span>")
    if any(f["agitado"] for f in filas):
        partes.append("<span class='chip warn'>Mercado agitado</span>")
    return f"<div class='vivo'>{''.join(partes)}</div>" if partes else ""


def etiquetas_razones(razones):
    """Etiquetas del "Por que"; la ultima (el veredicto) va resaltada con el color de la senal."""
    return "<div class='razones'>" + "".join(
        f"<span class='{'clave' if i == len(razones) - 1 else ''}'>{esc(r)}</span>" for i, r in enumerate(razones)
    ) + "</div>"


def panel_estadisticas(partido, xg):
    """Comparacion local vs visita como en las apps de futbol: xG, posesion, tiros, a puerta, corners."""
    if not xg:
        return ""
    est = xg.get("estadisticas") or {}
    filas = [("Goles esperados (xG)", xg["home"]["xg"], xg["away"]["xg"], "{:.2f}")]
    if est.get("home", {}).get("posesion") is not None:
        filas.append(("Posesión", est["home"]["posesion"], est["away"]["posesion"], "{:.0f}%"))
    filas += [
        ("Tiros", xg["home"]["tiros"], xg["away"]["tiros"], "{:.0f}"),
        ("Tiros a puerta", xg["home"]["a_puerta"], xg["away"]["a_puerta"], "{:.0f}"),
        ("Córners", xg["home"].get("corners", 0), xg["away"].get("corners", 0), "{:.0f}"),
    ]
    if xg["home"]["rojas"] or xg["away"]["rojas"]:
        filas.append(("Rojas", xg["home"]["rojas"], xg["away"]["rojas"], "{:.0f}"))
    html_filas = []
    for nombre, a, b, fmt in filas:
        total = (a or 0) + (b or 0)
        pa = 50 if total == 0 else 100 * (a or 0) / total
        html_filas.append(
            f"<div class='st'><b>{fmt.format(a or 0)}</b><div class='c'>{nombre}<div class='duo'>"
            f"<i style='width:{pa:.0f}%;background:{COLORES_LADO['home']}'></i>"
            f"<i style='width:{100 - pa:.0f}%;background:{COLORES_LADO['away']}'></i></div></div>"
            f"<b>{fmt.format(b or 0)}</b></div>"
        )
    return f"<div class='stats'>{''.join(html_filas)}</div>"


def grafica_momentum(partido, xg):
    """Momentum del partido: presion reciente local (arriba) contra visita (abajo), minuto a minuto."""
    serie = (xg or {}).get("momentum") or []
    if len(serie) < 2:
        return
    datos = pd.DataFrame(serie, columns=["minuto", "momentum"])
    datos["lado"] = np.where(datos["momentum"] >= 0, nombre_corto(partido, "home"), nombre_corto(partido, "away"))
    tope = max(0.15, float(datos["momentum"].abs().max()))
    colores = alt.Scale(domain=[nombre_corto(partido, "home"), nombre_corto(partido, "away")],
                        range=[COLORES_LADO["home"], COLORES_LADO["away"]])
    barras = alt.Chart(datos).mark_bar(width=3).encode(
        x=alt.X("minuto:Q", title=None, scale=alt.Scale(domain=[0, 95]),
                axis=alt.Axis(values=[0, 15, 30, 45, 60, 75, 90], labelColor="#8796b8", grid=False, domain=False,
                              tickColor="#22304f", labelExpr="datum.value == 45 ? 'MT' : datum.value + \"'\"")),
        y=alt.Y("momentum:Q", title=None, scale=alt.Scale(domain=[-tope, tope]), axis=None),
        color=alt.Color("lado:N", scale=colores, legend=None),
        tooltip=[alt.Tooltip("minuto:Q", title="Minuto"), alt.Tooltip("lado:N", title="Presiona"),
                 alt.Tooltip("momentum:Q", title="Presión", format="+.2f")],
    )
    cero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#22304f").encode(y="y:Q")
    medio = alt.Chart(pd.DataFrame({"x": [45]})).mark_rule(color="#8796b8", strokeDash=[2, 3]).encode(x="x:Q")
    st.markdown("<div class='mom-t'>Momentum</div>", unsafe_allow_html=True)
    st.altair_chart((barras + cero + medio).properties(height=120, background="transparent")
                    .configure_view(strokeWidth=0), width="stretch", theme=None)


def tarjeta(partido, analisis, precios):
    filas, lambdas, fuente = analisis["filas"], analisis["lambdas"], analisis["fuente"]
    if partido["status"] in ("TIMED", "SCHEDULED"):
        centro = f"<div class='score pre'>vs<small>{partido['utc_date'].astimezone(HORA_MX):%H:%M}</small></div>"
    else:
        centro = f"<div class='score'>{partido['home_goals']}<span>–</span>{partido['away_goals']}</div>"
    origen = "Modelo anclado al precio de antes del partido" if fuente == "mercado" else "Modelo con estadísticas del proyecto"
    liga = partido.get("competicion")
    etiqueta_liga = f"<span class='liga'>{esc(COMPETICIONES[liga][0])}</span> " if liga in COMPETICIONES else ""
    compartir = (
        f"<a class='share' href='?liga={liga}&partido={partido['match_id']}' target='_self'>Link del partido</a>"
        if liga in COMPETICIONES else ""
    )

    tiles = []
    for f in filas:
        lado = f["lado"]
        nombre = "Empate" if lado == "draw" else nombre_corto(partido, lado)
        clase_sig = "SIN" if f["senal"].startswith("SIN") else f["senal"]
        if f["edge"] is None:
            edge = "<div class='edge'>–</div>"
        else:
            signo = {"BUENO": "pos", "NO": "neg"}.get(f["senal"], "")
            edge = (
                f"<div class='edge {signo}'>{f['edge'] * 100:+.1f}<small>pts · exige "
                f"{f['umbral'] * 100:.1f}</small></div>"
            )
        ancho = 0 if f["ask"] is None else f["ask"] * 100
        ev = "–" if f["ev"] is None else f"{f['ev'] * 100:+.0f}¢"
        tiles.append(
            f"<div class='out s-{clase_sig}'>"
            f"<div class='out-h'>{mini_escudo(partido, lado)}<span class='nm'>{esc(nombre)}</span>"
            f"<span class='sig s-{clase_sig}'>{esc(f['senal'])}</span></div>"
            f"{edge}"
            f"<div class='bar'><div class='m' style='width:{ancho:.1f}%;background:{COLORES_LADO[lado]}'></div>"
            f"<div class='rg' style='left:{f['p_bajo'] * 100:.1f}%;width:{(f['p_alto'] - f['p_bajo']) * 100:.1f}%'></div>"
            f"<div class='md' style='left:{f['p_modelo'] * 100:.1f}%'></div></div>"
            f"<div class='row'><span>Precio de compra</span><b>{fmt_pct(f['ask'])}</b></div>"
            f"<div class='row'><span>Modelo (rango)</span><b>{f['p_modelo']:.1%} "
            f"<em>{f['p_bajo']:.0%}–{f['p_alto']:.0%}</em></b></div>"
            f"<div class='row'><span>Ganancia esperada / $1</span><b>{ev}</b></div>"
            f"<div class='row'><span>Momio merc. / justo</span><b>{fmt_ml(f['ml_mercado'])} / {fmt_ml(f['ml_justo'])}</b></div>"
            f"<div class='row'><span>Apuesta (% banca)</span><b>{f['kelly_25']:.1%}</b></div>"
            f"<div class='why'><b>Por qué</b>{etiquetas_razones(f['razones'])}"
            f"<details><summary>Ver explicación</summary><p>{esc(f['porque'])}</p></details></div>"
            "</div>"
        )

    with st.container(border=True):
        st.markdown(
            f"<div class='mc' id='{ancla(partido)}'>"
            f"<div class='mc-top'><span>{etiqueta_liga}{chip_estado(partido)}</span><span class='src'>{origen} · goles esperados "
            f"{lambdas[0]:.2f}–{lambdas[1]:.2f} · marcador: {esc(partido.get('fuente_marcador', '–'))} {compartir}</span></div>"
            "<div class='sb'>"
            f"<div class='team'>{escudo(partido, 'home')}<div class='tname'>{esc(nombre_corto(partido, 'home'))}</div></div>"
            f"{centro}"
            f"<div class='team'>{escudo(partido, 'away')}<div class='tname'>{esc(nombre_corto(partido, 'away'))}</div></div>"
            "</div>"
            f"{tira_vivo(partido, analisis)}"
            f"{panel_estadisticas(partido, analisis['xg'])}"
            "</div>",
            unsafe_allow_html=True,
        )
        grafica_momentum(partido, analisis["xg"])
        st.markdown(f"<div class='outs'>{''.join(tiles)}</div>", unsafe_allow_html=True)
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


def suavizar(serie, paso_min):
    """Serie (hora, p) en malla regular de paso_min y sin el parpadeo de compra/venta.

    El precio minuto a minuto de Polymarket brinca entre la mejor oferta de compra
    y la de venta; graficado tal cual se ve serruchado. Se toma el ultimo precio de
    cada intervalo, se aplica una mediana movil de 3 puntos (quita picos sueltos
    sin desplazar los cambios reales, como un gol) y el ultimo punto conserva el
    precio exacto.
    """
    if len(serie) < 3:
        return serie
    s = serie.sort_values("hora").set_index("hora")["p"]
    malla = s.resample(f"{paso_min}min").last().ffill()
    suave = malla.rolling(3, min_periods=1).median()
    suave.iloc[-1] = s.iloc[-1]
    return suave.reset_index()


def grafica_polymarket(partido, filas, precios):
    orden = ("home", "draw", "away")
    nombres = {lado: ("Empate" if lado == "draw" else nombre_corto(partido, lado)) for lado in orden}
    paso = PASO_GRAFICA[rango]
    partes = []
    for lado in orden:
        mercado = suavizar(serie_mercado(partido, precios, lado), paso)
        if not mercado.empty:
            mercado["serie"], mercado["fuente"] = nombres[lado], "Mercado"
            partes.append(mercado)
        modelo_hist = [h for h in estado["historial"].get(partido["match_id"], []) if h["lado"] == lado]
        if len(modelo_hist) >= 2:
            modelo_df = suavizar(pd.DataFrame([{"hora": h["hora"], "p": h["p_modelo"]} for h in modelo_hist]), paso)
            modelo_df["serie"], modelo_df["fuente"] = nombres[lado], "Modelo"
            partes.append(modelo_df)
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
        interpolate="monotone", strokeWidth=2.6, strokeCap="round", strokeJoin="round"
    )
    linea_modelo = base.transform_filter(alt.datum.fuente == "Modelo").mark_line(
        interpolate="monotone", strokeDash=[5, 4], strokeWidth=1.6, opacity=0.9
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
                "ask": f["ask"], "edge": f["edge"], "ml": f["ml_mercado"], "razones": f["razones"],
            })
            st.toast(f"Buen momento: moneyline de {f['equipo']} · [ver partido](#{ancla(partido)})", icon="✅")
        estado["ultima_senal"][clave] = f["senal"]


def avisos():
    st.markdown("<div class='sec'>Avisos</div>", unsafe_allow_html=True)
    if not estado["avisos"]:
        st.markdown(
            "<div class='empty'>Aquí aparece cada momento en que un resultado pasa a BUENO, "
            "con el minuto y los precios de ese instante. Un clic en el aviso lleva a su partido.</div>",
            unsafe_allow_html=True,
        )
        return
    items = []
    for a in estado["avisos"][:15]:
        p = a["partido"]
        items.append(
            f"<a class='ev' href='#{ancla(p)}' title='Ir al partido'>"
            f"<span class='t'>{a['minuto']}'</span>{mini_escudo(p, a['lado'])}"
            f"<div class='tx'><b>{esc(a['equipo'])}</b> · {esc(' · '.join(a['razones']))}. "
            f"Momio {fmt_ml(a['ml'])}."
            f"<br><span>{a['hora']} · {esc(nombre_corto(p, 'home'))} vs {esc(nombre_corto(p, 'away'))} · "
            "clic para ir al partido</span></div>"
            "</a>"
        )
    st.markdown(f"<div class='feed'>{''.join(items)}</div>", unsafe_allow_html=True)


def tabla_xgboost():
    m = xgboost().metricas
    if not m or not m.get("cortes"):
        return ""
    filas = "".join(
        f"<tr><td>{c['temporada_prueba']}</td><td>{c['temporadas_entrenamiento']}</td><td>{c['partidos_prueba']}</td>"
        f"<td>{c['exactitud']:.1%}</td><td>{c['exactitud_linea_base']:.1%}</td></tr>"
        for c in m["cortes"]
    )
    return (
        "<table class='tabla'><tr><th>Temporada predicha</th><th>Temporadas de entrenamiento</th>"
        "<th>Partidos</th><th>XGBoost</th><th>Clase más común</th></tr>"
        f"{filas}<tr><td><b>Promedio</b></td><td></td><td></td><td><b>{m['exactitud']:.1%}</b></td>"
        f"<td><b>{m['exactitud_linea_base']:.1%}</b></td></tr></table>"
    )


@st.cache_data(ttl=3600, show_spinner=False)
def resultados_backtest():
    path = Path(__file__).parent / "data" / "live" / "modelo" / "backtest_xg.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def seccion_backtest():
    r = resultados_backtest()
    if not r:
        return ""
    ad, br, se = r["adelanto"], r["brier"], r["senales"]
    filas_ad = "".join(
        f"<tr><td>{h.replace('min', ' min')}</td>"
        f"<td>{(ad['con_xg'].get(h) or {}).get('correlacion') or 0:.2f}</td>"
        f"<td>{(ad['sin_xg'].get(h) or {}).get('correlacion') or 0:.2f}</td>"
        f"<td>{(ad['con_xg'].get(h) or {}).get('acierto_direccion') or 0:.0%}</td>"
        f"<td>{(ad['sin_xg'].get(h) or {}).get('acierto_direccion') or 0:.0%}</td></tr>"
        for h in ad["con_xg"]
    )
    filas_br = "".join(
        f"<tr><td>{c.replace('min_', '')}'</td><td>{v['modelo_xg']:.3f}</td><td>{v['modelo_sin_xg']:.3f}</td>"
        f"<td>{v['mercado']:.3f}</td></tr>"
        for c, v in br.items()
    )

    def fila_senal(nombre, x):
        if not x.get("senales"):
            return f"<tr><td>{nombre}</td><td>0</td><td>–</td><td>–</td></tr>"
        return (f"<tr><td>{nombre}</td><td>{x['senales']}</td><td>{x['aciertos']:.0%}</td>"
                f"<td>{x['ganancia_por_dolar'] * 100:+.0f}¢</td></tr>")

    garch = r.get("garch") or {}
    texto_garch = (
        f" Ruido del mercado: pérdida QLIKE {garch['qlike_garch']:.2f} con GARCH contra "
        f"{garch['qlike_constante']:.2f} con varianza constante ({garch['observaciones']} pronósticos; menor es mejor)."
        if garch else ""
    )
    return (
        f"<div class='metodo'><b>Prueba histórica</b> ({r['partidos']} partidos de los últimos {r['ventana_dias']} días, "
        f"{r['minutos_evaluados']} minutos de juego; datos de ESPN con 1 min de atraso y compra al precio del minuto "
        "siguiente). ¿La diferencia entre el modelo y el precio anticipa hacia dónde se mueve el mercado?"
        "<table class='tabla'><tr><th>Horizonte</th><th>Correlación con xG</th><th>Sin xG</th>"
        f"<th>Acierto de dirección con xG</th><th>Sin xG</th></tr>{filas_ad}</table>"
        "Error del pronóstico del resultado final (Brier, menor es mejor):"
        "<table class='tabla'><tr><th>Minuto</th><th>Modelo con xG</th><th>Sin xG</th><th>Mercado</th></tr>"
        f"{filas_br}</table>Señales BUENO simuladas ($1 por señal):"
        "<table class='tabla'><tr><th>Modelo</th><th>Señales</th><th>Aciertos</th><th>Ganancia por $1</th></tr>"
        f"{fila_senal('Con xG y momentum (el que usa la plataforma)', se['con_xg'])}{fila_senal('Sin xG', se['sin_xg'])}</table>"
        f"{texto_calibracion(r)}{texto_garch} Conclusión: con el xG por posición y el momentum el modelo mejora "
        "después del minuto 30 y se adelanta un poco más al mercado, pero el precio de Polymarket sigue pronosticando "
        "mejor y las señales no le ganaron en estos partidos. Detalle en notebooks/03_backtest_xg_mercado.ipynb.</div>"
    )


def texto_calibracion(r):
    c = r.get("calibracion")
    if not c:
        return ""
    return (
        f" Pesos elegidos con los {c['partidos_ajuste']} partidos más antiguos: ritmo previo = "
        f"{c['elegido']['peso_previo']} min, momentum = {c['elegido']['peso_momentum']}. En los "
        f"{c['partidos_prueba']} más recientes (no usados para elegirlos), Brier con xG y momentum "
        f"{c['brier_prueba']['con_xg_momentum']:.3f} contra {c['brier_prueba']['sin_xg']:.3f} sin xG."
    )


def metodo():
    with st.expander("Cómo decide la plataforma"):
        st.markdown(
            "<div class='metodo'><ol>"
            "<li><b>Punto de partida.</b> Antes del silbatazo se toma el precio de Polymarket de local, "
            "empate y visita, y se buscan los goles esperados de cada equipo que reproducen esos "
            "precios. El mercado sabe más que cualquier estadística sobre quién es favorito, así que "
            "el modelo arranca igual que él. Si un partido no tiene mercado, se usan las estadísticas "
            "procesadas del proyecto (goles a favor y en contra por equipo).</li>"
            "<li><b>xG y momentum.</b> Cada tiro recibe su xG con un modelo propio (regresión logística sobre "
            "la distancia y el ángulo a la portería, cabezazo y penal) entrenado con 28,009 tiros de 1,004 "
            "partidos de ESPN de las 10 ligas. El ritmo de goles de cada equipo mezcla lo esperado antes del "
            "partido con su xG sin penales, y el momentum (presión reciente: xG y córners, con decaimiento de "
            "unos 5 minutos) lo ladea hacia el equipo que está presionando. Los pesos se eligieron en la "
            "prueba histórica con los partidos más antiguos. Una roja baja 30% el ritmo del que queda con uno "
            "menos y sube 20% el del rival.</li>"
            "<li><b>En vivo.</b> Los goles que faltan se modelan como Poisson sobre el tiempo restante y se "
            "suman al marcador. Así se obtiene la probabilidad de cada resultado minuto a minuto.</li>"
            "<li><b>Incertidumbre.</b> El ritmo de goles cambia durante un partido, así que el cálculo se "
            "repite con el ritmo de cada equipo entre 20% más lento y 20% más rápido. Eso da el rango "
            "(barra clara) alrededor del modelo (marca blanca).</li>"
            "<li><b>Ruido del mercado (GARCH).</b> Un GARCH(1,1) sobre los cambios del precio de cada "
            "resultado en Polymarket (cada 5 min, últimas 12 h) pronostica cuánto puede moverse el precio "
            "solo en los próximos 2 minutos. Si el precio acaba de dar un salto fuera de lo normal "
            "(más de 3 desviaciones y al menos 2 pts), el mercado está agitado y no se da BUENO en esa vuelta. "
            "En la prueba histórica el GARCH no pronosticó la volatilidad mejor que una varianza constante, así "
            "que en la práctica casi siempre manda el mínimo de 2 pts.</li>"
            "<li><b>Ventaja exigida.</b> Distancia entre el modelo y su escenario más bajo, más el ruido del "
            "mercado (mínimo 2 pts). La pone el modelo: es grande al inicio y se achica al final.</li>"
            "<li><b>Señal.</b> BUENO si modelo − precio de compra ≥ ventaja exigida y el mercado no está "
            "agitado. ESPERAR si el modelo está arriba del precio pero no por suficiente. NO si el precio "
            "es más caro de lo que el modelo cree.</li>"
            "<li><b>Cuánto.</b> Ganancia esperada por $1 = probabilidad del modelo ÷ precio − 1. La apuesta "
            "sugerida es ¼ de Kelly con el escenario bajo, con tope de 5% de la banca. Ojo: en la prueba "
            "histórica las señales BUENO perdieron dinero (ver abajo); el mercado de Polymarket resultó difícil "
            "de batir.</li>"
            "<li><b>Marcador y estadísticas.</b> El calendario, los nombres y los escudos vienen de "
            "football-data.org; el marcador, el minuto, los tiros y las rojas, de ESPN, porque el plan "
            "gratuito de football-data.org no actualiza los partidos en juego. Si ninguna fuente trae el "
            "marcador, el partido aparece sin señal.</li>"
            "</ol></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='metodo'><b>Modelo XGBoost del proyecto</b> (notebook 02, "
            "<code>src/models/xgboost_resultado.py</code>). Predice local / empate / visita antes del "
            "partido con el promedio de goles a favor y en contra y el porcentaje de victorias de cada "
            "equipo en temporadas anteriores. Se validó hacia adelante: para cada temporada se entrenó solo "
            "con las anteriores. Quedó prácticamente igual que predecir siempre la clase más común, así "
            "que la plataforma no lo usa para decidir; el punto de partida es el precio del mercado."
            f"{tabla_xgboost()}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(seccion_backtest(), unsafe_allow_html=True)
        st.markdown(
            "<div class='metodo'>Código del proyecto que usa: conexión a football-data.org (src/ingestion), "
            "estadísticas por equipo (src/processing/champions_league.py), normalización de nombres, alias e "
            "historial de precios de Polymarket (src/processing/polymarket.py), el XGBoost (src/models) y el "
            "GARCH del mercado (src/models/garch_mercado.py).</div>",
            unsafe_allow_html=True,
        )


def sin_partidos():
    st.markdown(
        f"<div class='empty'><b>No hay partidos de {esc(nombre_comp)} en juego</b>"
        f"El tablero revisa de nuevo cada {intervalo} s. Activa el modo demo en la barra lateral "
        "para ver un partido simulado.</div>",
        unsafe_allow_html=True,
    )
    try:
        proximos = sorted(proximos_partidos(competicion), key=lambda p: p["utc_date"])[:8]
    except Exception:
        proximos = []
    if proximos:
        filas = "".join(
            "<div class='fx'>"
            f"<div class='h'>{mini_escudo(p, 'home')}<span>{esc(nombre_corto(p, 'home'))}</span></div>"
            f"<div class='k'>{fecha_mx(p['utc_date'])}"
            + (f"<br><span class='liga'>{esc(COMPETICIONES[p['competicion']][0])}</span>" if competicion == TODAS else "")
            + "</div>"
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
                    precios = precios_polymarket(p["match_id"], p["home"], p["away"], p["utc_date"], p["competicion"])
                except Exception:
                    precios = {}
                lista.append((p, precios))
        except Exception as e:
            st.error(f"No se pudo consultar football-data.org ({e}). Se reintenta en {intervalo} s.")
            return

    # el partido del link compartido (?partido=ID) va primero
    lista.sort(key=lambda par: str(par[0]["match_id"]) != str(partido_link))
    calculados = []
    for partido, precios in lista:
        analisis = analizar_partido(modelo(), partido, precios)
        registrar(partido, analisis["filas"])
        calculados.append((partido, precios, analisis))
    estado["_filas"] = [(p, pr, a["filas"]) for p, pr, a in calculados]

    encabezado(lista)
    if not lista:
        sin_partidos()
    for partido, precios, analisis in calculados:
        tarjeta(partido, analisis, precios)
    avisos()
    metodo()
    st.markdown(
        "<div class='foot'>Datos: football-data.org (calendario y escudos), ESPN (marcador en vivo) y Polymarket (precios). "
        "Las señales son una referencia del modelo, no una garantía.</div>",
        unsafe_allow_html=True,
    )
    if demo and "demo" not in estado:
        estado["historial"].pop("demo", None)


st.fragment(run_every=intervalo)(panel)()
