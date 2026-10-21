# Decisiones del proyecto

## Decisión
Ampliar el alcance de la Entrega 2 a dos fuentes de datos: profundizar
Champions League (más temporadas históricas y predicción de equipos
nuevos) y agregar Polymarket (mercados públicos de predicción) como
tercera fuente.

## Razón
El profesor dio una lista de 10 ideas de retroalimentación tras la
Entrega 1, que en realidad mezclaban dos proyectos distintos (Champions
League y Polymarket). El equipo decidió trabajar ambos en vez de
descartar uno, usando la nueva arquitectura por capas (raw/interim/processed)
para mantenerlos organizados dentro del mismo repositorio.

## Alternativas consideradas
- Quedarnos solo con Champions League y profundizar únicamente ahí.
- Cambiar el proyecto por completo a Polymarket y dejar Champions League
  como el entregable ya cerrado del primer parcial.

## Consecuencias
- Se divide el trabajo en 4 partes con un issue por tarea principal
  (ver issues #4-#7 en GitHub): arquitectura/ingestión, procesamiento de
  Champions League, procesamiento de Polymarket, y
  validaciones/pruebas/documentación.
- La ingestión de Polymarket usa el endpoint público de búsqueda de la
  Gamma API (`https://gamma-api.polymarket.com/public-search`), que no
  requiere credenciales.
- Cualquier análisis relacionado con Polymarket (valor esperado,
  detección de oportunidades) se documentará explícitamente como
  ejercicio académico, no como asesoría financiera ni recomendación real
  de apuestas.

---

## Decisión
Separar la ingestión del procesamiento en capas (`raw` → `interim` →
`processed`), y mover la corrección de la columna `goals` (hecha en
Entrega 1 dentro del script de ingestión de Kaggle) a la capa de
procesamiento.

## Razón
La Entrega 2 exige una arquitectura por capas explícita. Mezclar
descarga y limpieza en el mismo script no permite distinguir datos
crudos de datos transformados, y dificulta la trazabilidad.

## Alternativas consideradas
Dejar la limpieza dentro del script de ingestión, como en la Entrega 1.

## Consecuencias
- `src/ingestion/extract_kaggle_historical.py` ahora solo descarga
  copias crudas a `data/raw/`.
- La función de corrección de `goals` queda pendiente de reconstruirse
  en `src/processing/` (tarea de Integrante 2, issue #5).

## Procesamiento Champions League (Entrega 2, Integrante 2)

- **Columna `goals` corrupta (UCL_AllTime_Performance_Table.csv):** Excel
  interpreto "goals_for:goals_against" como tiempo [h]:mm:ss, acarreando
  a horas cuando goals_against > 60. Se reconstruyo resolviendo
  k = (H - MM - Dif) / 61, goals_against = 60k + MM, goals_for = H - k.
  Verificado en el 100% de las 354 filas (k entero no negativo,
  goals_for - goals_against == Dif).
- **Columna `Pt.` descartada:** identica a `Dif` en el 100% de las filas
  (dato duplicado/mal etiquetado en la fuente).
- **Equipos sin historial reciente:** se marcan con `tiene_historial_reciente=0`
  y se les asigna como respaldo el promedio general de goles a
  favor/en contra de todos los equipos en la tabla historica de Kaggle.
- **Formato de exportacion:** CSV (no Parquet) para `data/interim/` y
  `data/processed/`, por simplicidad; la comparacion formal de
  desempeno entre formatos la hace el Integrante 4.
- **Temporada historica adicional:** se agrego la temporada 2022-23
  completa (fase de grupos + eliminatorias, 125 partidos), obtenida via
  busqueda asistida por IA en RSSSF (https://rsssf.org/ec/ec202223.html).
  Se eligio 1 temporada (no 3-5) para priorizar datos bien verificados
  sobre cantidad.