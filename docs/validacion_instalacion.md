# Validación de instalación limpia

**Realizada por:** Mateo Galvan
**Fecha:** 11 de septiembre de 2026
**Sistema operativo:** macOS (MacBook Air de Mateo)
**Versión de Python:** Python 3.12.7

## Pasos que funcionaron sin problema
- Clonar el repositorio (git clone)
- Crear la rama de trabajo (git checkout -b)
- Crear el entorno virtual (python3 -m venv .venv) y activarlo
- cp .env.example .env
- Configurar las credenciales en .env
- Descarga de datos de football-data.org (src/extract_api_football_data.py) — 144 filas guardadas sin problema
- Descarga de datos históricos de Kaggle (src/extract_kaggle_historical.py) — ambos archivos descargados sin problema
- Registro del kernel de Jupyter (ipykernel install)
- Apertura y ejecución completa del notebook (Restart Kernel and Run All Cells), sin ninguna celda con error
- Generación de la gráfica "Primera evidencia" en docs/

## Problemas encontrados
- El comando "code" (para abrir VS Code desde terminal) no fue reconocido ("zsh: command not found: code"), porque VS Code no estaba instalado previamente en esta máquina y/o no se había agregado al PATH. Solución: se usó "open -e" como alternativa para abrir archivos de texto mientras se instalaba VS Code. Esto no está mencionado como requisito previo en el README.
- Al correr "pip install -r requirements.txt" por primera vez, el resolver de pip tardó mucho tiempo intentando encontrar una versión compatible de matplotlib (probó desde la 3.11.1 hasta la 3.7.2), y finalmente falló con un error de red: "pip._vendor.urllib3.exceptions.ReadTimeoutError: HTTPSConnectionPool(host='files.pythonhosted.org', port=443): Read timed out." Al reintentar el mismo comando una segunda vez, la instalación se completó sin problema. Parece haber sido un corte de red puntual, pero el tiempo de resolución de dependencias de matplotlib fue notablemente largo.

## Resultado final
- [x] El notebook corrió completo sin errores
- [x] La gráfica de "Primera evidencia" se generó correctamente
- [x] Los datos descargados tienen sentido (revisé algunas filas)

## Recomendaciones para el README
- Agregar VS Code como requisito previo recomendado (o aclarar que el comando "code" en terminal requiere instalarlo y agregarlo al PATH desde la paleta de comandos de VS Code).
- Considerar fijar versiones (pinning) en requirements.txt, ya que sin versiones exactas pip puede tardar mucho tiempo resolviendo dependencias compatibles (se observó con matplotlib).
- Aclarar en la sección "Resultados principales" que los números de goles promedio corresponden a un corte de datos específico (por ejemplo, "con N partidos finalizados al [fecha]"), ya que estos cambian conforme avanza la temporada. En esta validación, con 18 partidos finalizados, el promedio fue 2.56 (local) vs 1.28 (visitante), distinto de los 2.25 vs 1.62 reportados originalmente con 8 partidos.