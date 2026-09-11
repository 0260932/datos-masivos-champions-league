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

El segundo script, además de descargar los CSV a data/raw/, genera data/processed/UCL_AllTime_Performance_Table_clean.csv con la columna goals corregida (ver Limitaciones).

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
- data/processed/: datos corregidos generados por los scripts (ignorado en git)
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
- La columna goals de la tabla histórica acumulada viene con errores desde el archivo original de Kaggle (no es un problema de pandas): 113 de 354 filas tienen formato de hora (ej. "1076:55:00") porque los valores "goles a favor:goles en contra" fueron convertidos a horas, probablemente al abrir el archivo en Excel. Se corrigió en src/extract_kaggle_historical.py usando la columna Dif para revertir la conversión, y se separó en goals_for y goals_against. La corrección se verificó en las 354 filas (goals_for - goals_against = Dif). Detalle en la sección "Diagnóstico inicial" del notebook.
- En la misma tabla, la columna Pt. es idéntica a Dif en las 354 filas, por lo que no representa puntos y no debe usarse como tal.
- La muestra actual de partidos finalizados es pequeña (8 partidos), por lo que los resultados de la sección "Primera evidencia" son preliminares.
