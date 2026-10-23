# Datos Masivos — UEFA Champions League

## Descripción
Proyecto del curso de Datos Masivos que analiza partidos y tendencias de la UEFA Champions League combinando una fuente de datos en vivo (API de la temporada actual) con una fuente histórica (finales del torneo 1955-2023), con el objetivo de responder preguntas concretas sobre desempeño y localía en el torneo.

## Integrantes
- Pablo Rosendo — usuario de GitHub: 0260932
- Mateo Flores Sanchez — usuario de GitHub: mateofsan
- Ramón Carús Blazquez — usuario de GitHub: Ramoncb8
- Nombre integrante 4 — usuario de GitHub

## Pregunta principal
¿Cómo se compara el desempeño de los equipos en la temporada actual de la Champions League (fuente API) contra su historial en finales del torneo (fuente Kaggle)?

## Arquitectura
Fuente 1 (API football-data.org, temporada actual), Fuente 2 (Kaggle, históricos 1955-2023) y Fuente 3 (Polymarket, mercados públicos de predicción, agregada en la Entrega 2) alimentan `data/raw/`. Desde ahí, un pipeline de procesamiento por capas limpia y transforma los datos:
- **Ingestión:** `src/ingestion/`, un script por fuente, ejecutables por
  terminal (ej. `python -m src.ingestion.download_data`). Evitan
  descargas innecesarias (omiten si ya existe copia de hoy) y registran
  todo en `logs/ingestion.log`.
- **Procesamiento:** `src/processing/` (limpieza, estandarización,
  variables).
- **Validación:** `src/validation/` (columnas, tipos, nulos, duplicados,
  rangos, consistencia).
- **Credenciales:** variables de entorno vía archivo `.env` (no
  versionado). Polymarket no requiere credenciales (API pública).
- **Análisis:** notebooks en `notebooks/`.
- **Decisiones del equipo documentadas en** `docs/decisions.md`.

## Requisitos
- Python 3.10 o superior
- Cuenta gratuita en football-data.org (https://www.football-data.org/client/register) para el token de API
- Cuenta gratuita en Kaggle (https://www.kaggle.com/), sección Settings, Create Legacy API Key

## Instalación
1. git clone https://github.com/0260932/datos-masivos-champions-league.git
2. cd datos-masivos-champions-league
3. python3 -m venv .venv
4. source .venv/bin/activate
5. pip install -r requirements.txt

## Obtención de datos
Las credenciales nunca se suben al repositorio. Configúralas así:
1. cp .env.example .env
2. Edita .env y coloca tu FOOTBALL_DATA_API_TOKEN, KAGGLE_USERNAME y KAGGLE_KEY reales (Polymarket no necesita credenciales).

Luego descarga los datos:
1. python -m src.ingestion.download_data
2. python -m src.ingestion.download_polymarket

Ambos comandos guardan copias crudas en data/raw/, evitan volver a descargar si ya existe una copia de hoy (usa --force para forzar), y registran el proceso en logs/ingestion.log.

## Ejecución
jupyter notebook notebooks/01_extraccion_y_diagnostico.ipynb

Si usas un entorno virtual, registra el kernel antes de abrir el notebook:
1. pip install ipykernel
2. python -m ipykernel install --user --name=datos-masivos-venv --display-name "Python (datos-masivos venv)"
3. Selecciónalo dentro de Jupyter.

## Tracker en vivo de moneyline

`src/live/tracker.py` sigue los partidos de Champions en juego y cada 30 s indica, para local / empate / visita, si es buen momento para entrar al moneyline:

1. Marcador y minuto desde football-data.org (usa el mismo `FOOTBALL_DATA_API_TOKEN`).
2. Probabilidad 1X2 con `src/live/modelo.py`: goles esperados por equipo (estadísticas de `data/processed/`, con la tabla histórica como prior y ajuste por localía) y Poisson sobre el tiempo restante.
3. Precio de compra (best ask) del mercado moneyline del partido en Polymarket (API pública).
4. Señal `BUENO` si modelo − precio ≥ `--edge-min` (default 5 puntos); `ESPERAR` si la ventaja es positiva pero menor; `NO` si el mercado cobra más de lo que da el modelo. Avisa (con beep) solo cuando un resultado pasa a `BUENO`.

```
python -m src.live.tracker                 # loop cada 30 s, Ctrl+C para detener
python -m src.live.tracker --intervalo 60 --edge-min 0.08
python -m src.live.tracker --una-vez       # una sola pasada
python -m src.live.tracker --demo          # partido simulado, sin APIs
```

Requiere haber corrido antes `python -m src.processing.champions_league`. Cada evaluación se guarda en `data/interim/live/senales_<fecha>.csv` y los avisos en `logs/live_tracker.log`. El modelo es simple (no considera tarjetas rojas, alineaciones ni cambios); úsalo como referencia contra el mercado, no como garantía.

### Dashboard web (Streamlit)

`streamlit_app.py` muestra lo mismo en una página que se actualiza sola: tarjeta por partido con marcador, señal por resultado, gráfica de la ventaja en el tiempo y avisos. Tiene modo demo en la barra lateral.

```
streamlit run streamlit_app.py
```

Para abrirlo desde internet (gratis) con Streamlit Community Cloud:
1. Sube el repo a GitHub (tu fork). `data/live/modelo/` sí se versiona: es el snapshot de estadísticas que usa el modelo en la nube. Actualízalo con `python -m src.live.modelo` después de reprocesar datos.
2. En share.streamlit.io, entra con tu cuenta de GitHub, "Create app", elige el repo, la rama y `streamlit_app.py`.
3. En "Advanced settings" > Secrets pon: `FOOTBALL_DATA_API_TOKEN = "tu_token"`.
4. Deploy. Te da un link `https://<nombre>.streamlit.app` que abre en cualquier navegador o celular.

## Resultados principales
Con los primeros 8 partidos finalizados de la fase de liga 2026, el equipo local promedió 2.25 goles por partido contra 1.62 del visitante (ver notebooks/01_extraccion_y_diagnostico.ipynb, sección "Primera evidencia", y la gráfica en docs/goles_local_vs_visitante.png).

## Estructura del repositorio
- data/raw/: datos crudos descargados por los scripts de ingestión (ignorado en git)
- data/interim/: datos parcialmente limpiados/transformados (ignorado en git)
- data/processed/: datos listos para análisis (ignorado en git)
- docs/: decisiones (decisions.md), validaciones, gráficas e imágenes generadas
- logs/: logs de ejecución (ignorado en git, excepto la carpeta)
- notebooks/01_extraccion_y_diagnostico.ipynb
- notebooks/02_modelo_prediccion_xgboost.ipynb
- src/ingestion/: scripts de descarga (uno por fuente)
- src/processing/: pipeline de limpieza y transformación
- src/live/: tracker en vivo de moneyline (modelo + loop)
- streamlit_app.py: dashboard web del tracker
- data/live/modelo/: snapshot de estadísticas para el modelo en la nube
- src/validation/: validaciones de calidad de datos
- tests/: pruebas automáticas
- .env.example
- .gitignore
- requirements.txt
- README.md

## Limitaciones
- El plan gratuito de football-data.org limita el acceso a temporadas históricas completas y a 10 solicitudes por minuto.
- El dataset de Kaggle cubre solo finales (1955-2023) y una tabla de desempeño histórico acumulado, no todos los partidos de todas las rondas.
- La columna goals de la tabla histórica acumulada viene con errores desde el archivo original de Kaggle (no es un problema de pandas): 113 de 354 filas tienen formato de hora (ej. "1076:55:00") porque los valores "goles a favor:goles en contra" fueron convertidos a horas, probablemente al abrir el archivo en Excel. Se corrigió en src/extract_kaggle_historical.py usando la columna Dif para revertir la conversión, y se separó en goals_for y goals_against. La corrección se verificó en las 354 filas (goals_for - goals_against = Dif). Detalle en la sección "Diagnóstico inicial" del notebook.
- En la misma tabla, la columna Pt. es idéntica a Dif en las 354 filas, por lo que no representa puntos y no debe usarse como tal.
- La muestra actual de partidos finalizados es pequeña (8 partidos), por lo que los resultados de la sección "Primera evidencia" son preliminares.
