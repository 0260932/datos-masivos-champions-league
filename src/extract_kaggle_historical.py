"""
Fuente 2 (descarga automatizada): dataset historico de Kaggle
"UEFA Champions League Historical Dataset 1955-2023"
https://www.kaggle.com/datasets/fardifaalam170041060/champions-league-dataset-1955-2023

Se descarga por codigo con kagglehub, usando credenciales por variables
de entorno (KAGGLE_USERNAME / KAGGLE_KEY), no un archivo kaggle.json
con rutas personales dentro del repositorio.

Correccion de la columna "goals" (UCL_AllTime_Performance_Table.csv):
el formato esperado es "goles_a_favor:goles_en_contra" (ej. "15:24"),
pero en el archivo original de Kaggle muchos valores fueron convertidos
a formato de hora (ej. "1068:535" quedo como "1076:55:00"), probablemente
al abrirse en Excel: cada 60 goles en contra se convirtieron en 1 unidad
sumada a los goles a favor. Como la columna "Dif" (diferencia de goles)
si es confiable, se usa para revertir esa conversion.
El archivo original en data/raw no se modifica; la version corregida
se guarda en data/processed.
"""

import os
import shutil
from pathlib import Path

import kagglehub
import pandas as pd
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

KAGGLE_DATASET = "fardifaalam170041060/champions-league-dataset-1955-2023"
PERFORMANCE_FILE = "UCL_AllTime_Performance_Table.csv"
PERFORMANCE_CLEAN_FILE = "UCL_AllTime_Performance_Table_clean.csv"


def download_dataset():
    load_dotenv(ROOT_DIR / ".env")
    if not os.getenv("KAGGLE_USERNAME") or not os.getenv("KAGGLE_KEY"):
        raise RuntimeError(
            "Faltan KAGGLE_USERNAME / KAGGLE_KEY en tu .env."
        )

    cache_path = Path(kagglehub.dataset_download(KAGGLE_DATASET))

    copied = []
    for csv_file in cache_path.glob("*.csv"):
        dest = RAW_DIR / csv_file.name
        shutil.copy(csv_file, dest)
        copied.append(dest)

    if not copied:
        raise RuntimeError(f"No se encontraron archivos .csv en {cache_path}")
    return copied


def fix_goals_column(df):
    """Separa "goals" en goals_for / goals_against revirtiendo la conversion a hora."""
    partes = df["goals"].str.extract(r"^(\d+):(\d+)(?::00)?$")
    if partes.isna().any().any():
        malos = df.loc[partes.isna().any(axis=1), "goals"].tolist()
        raise ValueError(f"Valores de goals con formato inesperado: {malos}")

    primero = partes[0].astype(int)
    segundo = partes[1].astype(int)
    dif = pd.to_numeric(df["Dif"], errors="raise")

    # Cada "hora" que se movio suma 1 al primer numero y resta 60 al segundo,
    # asi que la resta crece en 61 por cada una.
    exceso = primero - segundo - dif
    if ((exceso % 61 != 0) | (exceso < 0)).any():
        raise ValueError("No se pudo reconstruir goals en algunas filas; revisar el archivo.")
    horas_movidas = exceso // 61

    df = df.copy()
    df["goals_for"] = primero - horas_movidas
    df["goals_against"] = segundo + 60 * horas_movidas
    return df


def clean_performance_table():
    raw_path = RAW_DIR / PERFORMANCE_FILE
    df = pd.read_csv(raw_path, dtype={"goals": str})
    fixed = fix_goals_column(df)

    # Reemplaza "goals" por goals_for y goals_against en la misma posicion
    columnas = []
    for col in df.columns:
        if col == "goals":
            columnas += ["goals_for", "goals_against"]
        else:
            columnas.append(col)
    fixed = fixed[columnas]

    dest = PROCESSED_DIR / PERFORMANCE_CLEAN_FILE
    fixed.to_csv(dest, index=False)
    return dest


def main():
    files = download_dataset()
    for f in files:
        print(f"Guardado: {f}")

    clean_file = clean_performance_table()
    print(f"Guardado (goals corregido): {clean_file}")


if __name__ == "__main__":
    main()