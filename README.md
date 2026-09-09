# Datos Masivos — UEFA Champions League

## Descripción
Proyecto del curso de Datos Masivos que analiza partidos y tendencias de la UEFA Champions League combinando una fuente de datos en vivo (API de la temporada actual) con una fuente histórica (finales del torneo 1955-2023), con el objetivo de responder preguntas concretas sobre desempeño y localía en el torneo.

## Integrantes
- Pablo Rosendo — usuario de GitHub: 0260932
- Nombre integrante 2 — usuario de GitHub
- Nombre integrante 3 — usuario de GitHub
- Nombre integrante 4 — usuario de GitHub

## Pregunta principal
¿Cómo se compara el desempeño de los equipos en la temporada actual de la Champions League (fuente API) contra su historial en finales del torneo (fuente Kaggle)?

## Arquitectura
Fuente 1 (API football-data.org, temporada 2026) y Fuente 2 (Kaggle, finales históricas 1955-2023) alimentan data/raw/, que se analiza en notebooks/01_extraccion_y_diagnostico.ipynb.
- Extracción: scripts independientes en src/, uno por fuente.
- Credenciales: variables de entorno vía archivo .env (no versionado).
- Análisis: notebook en notebooks/01_extraccion_y_diagnostico.ipynb.

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
2. Edita .env y coloca tu FOOTBALL_DATA_API_TOKEN, KAGGLE_USERNAME y KAGGLE_KEY reales.

Luego descarga los datos:
1. python src/extract_api_football_data.py
2. python src/extract_kaggle_historical.py

## Ejecución
jupyter notebook notebooks/01_extraccion_y_diagnostico.ipynb

Si usas un entorno virtual, registra el kernel antes de abrir el notebook:
1. pip install ipykernel
2. python -m ipykernel install --user --name=datos-masivos-venv --display-name "Python (datos-masivos venv)"
3. Selecciónalo dentro de Jupyter.

## Resultados principales
Con los primeros 8 partidos finalizados de la fase de liga 2026, el equipo local promedió 2.25 goles por partido contra 1.62 del visitante (ver notebooks/01_extraccion_y_diagnostico.ipynb, sección "Primera evidencia", y la gráfica en docs/goles_local_vs_visitante.png).

## Estructura del repositorio
- data/raw/: datos crudos descargados por los scripts (ignorado en git)
- docs/: gráficas e imágenes generadas
- notebooks/01_extraccion_y_diagnostico.ipynb
- src/extract_api_football_data.py
- src/extract_kaggle_historical.py
- .env.example
- .gitignore
- requirements.txt
- README.md

## Limitaciones
- El plan gratuito de football-data.org limita el acceso a temporadas históricas completas y a 10 solicitudes por minuto.
- El dataset de Kaggle cubre solo finales (1955-2023) y una tabla de desempeño histórico acumulado, no todos los partidos de todas las rondas.
- La columna goals de la tabla histórica acumulada se lee mal por pandas (se interpreta como hora en vez de goles a favor y en contra); falta corregirla en el siguiente parcial.
- La muestra actual de partidos finalizados es pequeña (8 partidos), por lo que los resultados de la sección "Primera evidencia" son preliminares.
