"""Validaciones reutilizables de calidad de datos (Entrega 2, Integrante 4).

Cada función recibe un DataFrame y devuelve un ``ResultadoValidacion`` con:
  - ``nombre``: qué se validó,
  - ``ok``: True si pasó, False si falló,
  - ``detalle``: explicación breve con los números reales encontrados.

Las funciones NO lanzan excepciones cuando los datos están mal: devuelven
``ok=False``. Así ``run_validations.py`` puede correr todas las validaciones
y reportar todas las fallas juntas, en lugar de detenerse en la primera.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterable

import pandas as pd


@dataclass
class ResultadoValidacion:
    nombre: str
    ok: bool
    detalle: str = ""

    def __str__(self) -> str:
        estado = "OK   " if self.ok else "FALLA"
        return f"[{estado}] {self.nombre}: {self.detalle}"


def _ejemplos(valores: Iterable, n: int = 5) -> str:
    """Muestra hasta n ejemplos de valores problemáticos, para el reporte."""
    valores = list(valores)
    texto = ", ".join(str(v) for v in valores[:n])
    return texto + (f" (y {len(valores) - n} más)" if len(valores) > n else "")


# --------------------------------------------------------------------------------------
# Estructura
# --------------------------------------------------------------------------------------
def validate_required_columns(df: pd.DataFrame, required_columns: Iterable[str]) -> ResultadoValidacion:
    """Todas las columnas obligatorias existen."""
    faltantes = sorted(set(required_columns) - set(df.columns))
    nombre = "columnas obligatorias"
    if faltantes:
        return ResultadoValidacion(nombre, False, f"faltan columnas: {faltantes}")
    return ResultadoValidacion(nombre, True, f"{len(set(required_columns))} columnas presentes")


# Tipos "lógicos" en lugar de tipos exactos de pandas: un CSV puede leer una columna
# entera como int64 o como float64 (si tiene vacíos), y ambas son numéricas válidas.
_CHEQUEO_TIPO: dict[str, Callable[[pd.Series], bool]] = {
    "int": pd.api.types.is_integer_dtype,
    "float": pd.api.types.is_float_dtype,
    "numeric": pd.api.types.is_numeric_dtype,
    "bool": pd.api.types.is_bool_dtype,
    "str": lambda s: pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s),
    "datetime": pd.api.types.is_datetime64_any_dtype,
}


def validate_dtypes(df: pd.DataFrame, expected_dtypes: dict[str, str]) -> ResultadoValidacion:
    """expected_dtypes: {columna: tipo lógico}, con tipo en int/float/numeric/bool/str/datetime."""
    errores = []
    for col, tipo in expected_dtypes.items():
        if col not in df.columns:
            errores.append(f"{col} (no existe)")
        elif not _CHEQUEO_TIPO[tipo](df[col]):
            errores.append(f"{col} es {df[col].dtype}, se esperaba {tipo}")
    nombre = "tipos de datos"
    if errores:
        return ResultadoValidacion(nombre, False, "; ".join(errores))
    return ResultadoValidacion(nombre, True, f"{len(expected_dtypes)} columnas con el tipo esperado")


def validate_expected_volume(df: pd.DataFrame, min_rows: int, max_rows: int | None = None) -> ResultadoValidacion:
    """El número de filas está dentro de lo esperado (detecta descargas vacías o duplicadas)."""
    n = len(df)
    rango = f"[{min_rows}, {max_rows if max_rows is not None else '∞'}]"
    ok = n >= min_rows and (max_rows is None or n <= max_rows)
    return ResultadoValidacion("volumen de filas", ok, f"{n} filas, esperado {rango}")


# --------------------------------------------------------------------------------------
# Completitud y unicidad
# --------------------------------------------------------------------------------------
def validate_no_missing(df: pd.DataFrame, columns: Iterable[str]) -> ResultadoValidacion:
    """Las columnas clave no tienen valores faltantes."""
    columns = list(columns)
    nulos = {c: int(df[c].isna().sum()) for c in columns if c in df.columns and df[c].isna().any()}
    nombre = f"sin faltantes en {columns}"
    if nulos:
        return ResultadoValidacion(nombre, False, f"faltantes por columna: {nulos}")
    return ResultadoValidacion(nombre, True, "0 faltantes")


def validate_no_duplicates(df: pd.DataFrame, subset: list[str] | None = None) -> ResultadoValidacion:
    """No hay filas repetidas (completas, o según las columnas de ``subset``)."""
    duplicados = df[df.duplicated(subset=subset, keep=False)]
    nombre = f"sin duplicados por {subset if subset else 'fila completa'}"
    if len(duplicados):
        llave = duplicados[subset].drop_duplicates() if subset else duplicados.head(5)
        ejemplos = _ejemplos(llave.astype(str).agg(" | ".join, axis=1))
        return ResultadoValidacion(nombre, False, f"{len(duplicados)} filas repetidas, p. ej.: {ejemplos}")
    return ResultadoValidacion(nombre, True, "0 duplicados")


# --------------------------------------------------------------------------------------
# Rangos y valores permitidos
# --------------------------------------------------------------------------------------
def validate_value_ranges(df: pd.DataFrame, column: str, min_value=None, max_value=None) -> ResultadoValidacion:
    """Los valores de ``column`` están en [min_value, max_value]. Los vacíos no se evalúan aquí."""
    valores = pd.to_numeric(df[column], errors="coerce").dropna()
    fuera = pd.Series(False, index=valores.index)
    if min_value is not None:
        fuera |= valores < min_value
    if max_value is not None:
        fuera |= valores > max_value
    nombre = f"rango de {column} en [{min_value}, {max_value}]"
    if fuera.any():
        return ResultadoValidacion(
            nombre, False, f"{int(fuera.sum())} valores fuera de rango, p. ej.: {_ejemplos(valores[fuera])}"
        )
    if valores.empty:
        return ResultadoValidacion(nombre, True, "sin valores que evaluar (todos vacíos)")
    return ResultadoValidacion(nombre, True, f"min={valores.min():.4g}, max={valores.max():.4g}")


def validate_allowed_values(df: pd.DataFrame, column: str, allowed: Iterable) -> ResultadoValidacion:
    """Solo aparecen valores de una lista conocida (p. ej. status FINISHED/TIMED)."""
    allowed = set(allowed)
    inesperados = sorted(set(df[column].dropna()) - allowed, key=str)
    nombre = f"valores permitidos en {column}"
    if inesperados:
        return ResultadoValidacion(nombre, False, f"valores no esperados: {_ejemplos(inesperados)}")
    return ResultadoValidacion(nombre, True, f"todos en {sorted(allowed, key=str)}")


def validate_row_rule(
    df: pd.DataFrame,
    nombre: str,
    condicion: Callable[[pd.DataFrame], pd.Series],
    columnas_ejemplo: list[str] | None = None,
) -> ResultadoValidacion:
    """Regla de negocio fila por fila. ``condicion(df)`` devuelve True donde la fila es válida.

    Ejemplo: ``lambda d: d["wins"] + d["draws"] + d["losses"] == d["matches_played"]``.
    ``columnas_ejemplo``: columnas a mostrar de las filas que fallan, para el reporte.
    """
    validas = condicion(df).fillna(False).astype(bool)
    malas = (~validas).sum()
    if malas:
        detalle = f"{int(malas)} de {len(df)} filas no cumplen"
        if columnas_ejemplo:
            ejemplos = df.loc[~validas, columnas_ejemplo].drop_duplicates().astype(str).agg(" | ".join, axis=1)
            detalle += f"; p. ej. ({' | '.join(columnas_ejemplo)}): {_ejemplos(ejemplos)}"
        return ResultadoValidacion(nombre, False, detalle)
    return ResultadoValidacion(nombre, True, f"{len(df)} filas cumplen")


# --------------------------------------------------------------------------------------
# Consistencia temporal
# --------------------------------------------------------------------------------------
def _a_fecha(serie: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(serie):  # timestamp Unix en segundos (historial Polymarket)
        return pd.to_datetime(serie, unit="s", utc=True, errors="coerce")
    return pd.to_datetime(serie, utc=True, errors="coerce", format="mixed")


def _a_utc(valor) -> pd.Timestamp:
    ts = pd.Timestamp(valor)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def validate_temporal_consistency(df: pd.DataFrame, date_column: str, min_date=None, max_date=None) -> ResultadoValidacion:
    """Todas las fechas se pueden interpretar y caen dentro de [min_date, max_date].

    Si ``max_date`` no se indica, se usa "hoy" (no debe haber fechas futuras).
    """
    fechas = _a_fecha(df[date_column])
    no_interpretables = int((fechas.isna() & df[date_column].notna()).sum())
    minimo = _a_utc(min_date) if min_date is not None else None
    maximo = _a_utc(max_date) if max_date is not None else pd.Timestamp.now(tz="UTC")

    fuera = pd.Series(False, index=fechas.index)
    if minimo is not None:
        fuera |= fechas < minimo
    fuera |= fechas > maximo

    nombre = f"fechas válidas en {date_column}"
    rango_txt = f"[{minimo.date() if minimo is not None else '-∞'}, {maximo.date()}]"
    if no_interpretables or fuera.any():
        partes = []
        if no_interpretables:
            partes.append(f"{no_interpretables} fechas no interpretables")
        if fuera.any():
            partes.append(f"{int(fuera.sum())} fechas fuera de {rango_txt}, p. ej.: {_ejemplos(fechas[fuera].dt.date)}")
        return ResultadoValidacion(nombre, False, "; ".join(partes))
    return ResultadoValidacion(nombre, True, f"de {fechas.min().date()} a {fechas.max().date()}, dentro de {rango_txt}")


# --------------------------------------------------------------------------------------
# Consistencia entre tablas / columnas
# --------------------------------------------------------------------------------------
def validate_cross_table_consistency(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    key: str,
    key_b: str | None = None,
    min_coverage: float = 1.0,
) -> ResultadoValidacion:
    """Los valores de ``df_a[key]`` aparecen en ``df_b[key_b]`` (por defecto, la misma columna).

    ``min_coverage`` permite tolerar un porcentaje de no coincidencias (1.0 = todas deben aparecer).
    """
    key_b = key_b or key
    valores_a = set(df_a[key].dropna())
    valores_b = set(df_b[key_b].dropna())
    faltantes = sorted(valores_a - valores_b, key=str)
    cobertura = 1 - len(faltantes) / len(valores_a) if valores_a else 1.0
    nombre = f"{key} de A presentes en {key_b} de B (mín. {min_coverage:.0%})"
    detalle = f"{len(valores_a) - len(faltantes)}/{len(valores_a)} coinciden ({cobertura:.1%})"
    if faltantes:
        detalle += f"; sin coincidencia: {_ejemplos(faltantes, 10)}"
    return ResultadoValidacion(nombre, cobertura >= min_coverage, detalle)


def validate_outcome_prices_sum(df: pd.DataFrame, column: str = "outcome_prices", tolerance: float = 0.02) -> ResultadoValidacion:
    """En cada mercado de Polymarket, los precios de "Yes" y "No" suman ~1.

    ``outcome_prices`` viene como texto con una lista JSON, p. ej. '["0.185", "0.815"]'.
    """
    def suma(valor):
        try:
            return sum(float(p) for p in json.loads(valor))
        except (TypeError, ValueError):
            return float("nan")

    sumas = df[column].apply(suma)
    no_interpretables = int(sumas.isna().sum())
    malas = (sumas - 1).abs() > tolerance
    nombre = f"{column} suma 1 ± {tolerance}"
    if no_interpretables or malas.any():
        return ResultadoValidacion(
            nombre, False,
            f"{int(malas.sum())} mercados fuera de tolerancia, {no_interpretables} no interpretables; "
            f"p. ej.: {_ejemplos(sumas[malas | sumas.isna()].round(4))}",
        )
    return ResultadoValidacion(nombre, True, f"{len(df)} mercados, suma entre {sumas.min():.4f} y {sumas.max():.4f}")


def validate_group_sum(
    df: pd.DataFrame,
    group_column: str,
    value_column: str,
    expected_column: str,
    relative_tolerance: float = 0.10,
) -> ResultadoValidacion:
    """Por grupo, la suma de ``value_column`` se parece al valor de ``expected_column``.

    Pensado para el resumen de eventos de Polymarket: en "quién será campeón" las
    probabilidades deben sumar ~1, en "top 8" ~8, etc. Un grupo sin valor esperado
    también se reporta como falla, porque no se puede verificar.
    """
    problemas = []
    for grupo, fila in df.set_index(group_column).iterrows():
        suma, esperado = fila[value_column], fila[expected_column]
        if pd.isna(esperado):
            problemas.append(f"{grupo}: suma={suma:.4f} sin valor esperado definido")
        elif abs(suma - esperado) > relative_tolerance * esperado:
            problemas.append(f"{grupo}: suma={suma:.4f} vs esperado={esperado:g}")
    nombre = f"suma de {value_column} por {group_column} (tolerancia {relative_tolerance:.0%})"
    if problemas:
        return ResultadoValidacion(nombre, False, "; ".join(problemas))
    return ResultadoValidacion(nombre, True, f"{len(df)} grupos dentro de la tolerancia")
