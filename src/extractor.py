"""Extracción, normalización y control de calidad de tablas en facturas PDF."""

from collections.abc import Callable
import logging
from pathlib import Path
import re

import pandas as pd
import pdfplumber

from models import CARPETA_SALIDA, ErrorDeProceso, Incidencia

COLUMNAS_ESPERADAS = ("descripcion", "cantidad", "precio_unitario", "importe", "no_factura")
LOG = logging.getLogger("conciliador")


def listar_pdfs(carpeta: Path) -> list[Path]:
    """Lista los archivos PDF de una carpeta sin distinguir mayúsculas en la extensión."""
    return sorted(
        p for p in carpeta.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def extraer_no_factura(texto: str) -> str | None:
    """Busca y devuelve el número de factura en el texto de una página."""
    match = re.search(r"No\.\s*Factura:\s*([^\s\n]+)", texto)
    return match.group(1).strip() if match else None


def extraer_datos_pdf(pdf_path: Path) -> tuple[str | None, list]:
    """Extrae el número de factura y todas las tablas de un archivo PDF."""
    no_factura = None
    tablas_pdf: list = []

    with pdfplumber.open(pdf_path) as doc:
        for pagina in doc.pages:
            texto_pagina = pagina.extract_text() or ""
            if not no_factura:
                no_factura = extraer_no_factura(texto_pagina)
            for tabla in pagina.extract_tables():
                tablas_pdf.append(tabla)

    return no_factura, tablas_pdf


def aplanar_con_factura(tablas_pdf: list, no_factura: str) -> list[list]:
    """Convierte las tablas extraídas en filas planas anexando el número de factura."""
    return [[*fila, no_factura] for tabla in tablas_pdf for fila in tabla]


def eliminar_encabezados(df: pd.DataFrame, columna: int = 0) -> pd.DataFrame:
    """Elimina las filas de encabezado donde la descripción contiene 'Descripcion'."""
    if df.empty or columna not in df.columns:
        return df.copy()
    mascara = (
        df[columna]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.contains("descripcion", case=False, regex=False)
    )
    return df.loc[~mascara].copy()


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia tipos de datos y renombra las columnas al esquema estándar."""
    if df.empty:
        return pd.DataFrame(columns=COLUMNAS_ESPERADAS)

    df = df.copy()
    if 0 in df.columns:
        df[0] = (
            df[0]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
            .str.title()
        )
    if 1 in df.columns:
        df[1] = pd.to_numeric(df[1].astype(str).str.strip(), errors="coerce").astype("Int64")
    for col in (2, 3):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r"[$,\s]", "", regex=True),
                errors="coerce",
            )
    if 4 in df.columns:
        df[4] = df[4].fillna("").astype(str).str.strip()

    return df.rename(columns=dict(enumerate(COLUMNAS_ESPERADAS)))


def validar_calidad(df: pd.DataFrame, tolerancia: float) -> list[Incidencia]:
    """Detecta valores no numéricos y descuadres internos (cantidad × precio ≠ importe)."""
    incidencias: list[Incidencia] = []
    if df.empty:
        return incidencias

    numericas = df[["cantidad", "precio_unitario", "importe"]]
    for factura, grupo in df[numericas.isna().any(axis=1)].groupby("no_factura"):
        incidencias.append(Incidencia(
            "AVISO",
            str(factura),
            f"{len(grupo)} fila(s) con valores no numéricos (se ignoran en la suma).",
        ))

    calculado = df["cantidad"].astype("Float64") * df["precio_unitario"]
    descuadre = ((calculado - df["importe"]).abs().round(2) > tolerancia).fillna(False).astype(bool)
    for factura, grupo in df[descuadre].groupby("no_factura"):
        incidencias.append(Incidencia(
            "AVISO",
            str(factura),
            f"{len(grupo)} fila(s) donde importe ≠ cantidad × precio unitario.",
        ))

    return incidencias


def extraer_lote(
    pdfs: list[Path],
    tolerancia: float = 0.01,
    guardar_csv: bool = False,
    al_avanzar: Callable[[Path], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, list[str]], list[Incidencia]]:
    """Extrae un lote de PDFs tolerando fallos individuales y detectando duplicados."""
    filas_planas: list[list] = []
    archivos_por_factura: dict[str, list[str]] = {}
    incidencias: list[Incidencia] = []

    for pdf in pdfs:
        try:
            no_factura, tablas_pdf = extraer_datos_pdf(pdf)
            if not no_factura:
                incidencias.append(
                    Incidencia("ERROR", pdf.name, "No se encontró 'No. Factura'; archivo omitido.")
                )
            elif no_factura in archivos_por_factura:
                archivos_por_factura[no_factura].append(pdf.name)
            elif not tablas_pdf:
                incidencias.append(
                    Incidencia("ERROR", pdf.name, "No se detectaron tablas; archivo omitido.")
                )
            else:
                archivos_por_factura[no_factura] = [pdf.name]
                filas_planas.extend(aplanar_con_factura(tablas_pdf, no_factura))
        except Exception as exc:
            LOG.exception("Fallo al leer %s", pdf.name)
            incidencias.append(Incidencia("ERROR", pdf.name, f"No se pudo leer el PDF: {exc}"))
        finally:
            if al_avanzar is not None:
                al_avanzar(pdf)

    if not filas_planas:
        return pd.DataFrame(columns=COLUMNAS_ESPERADAS), archivos_por_factura, incidencias

    df = eliminar_encabezados(pd.DataFrame(filas_planas), columna=0)
    df = normalizar_columnas(df)

    if df.shape[1] != len(COLUMNAS_ESPERADAS) or df.empty:
        raise ErrorDeProceso(
            f"Estructura de tabla inesperada: {df.shape[1]} columnas "
            f"(se esperaban {len(COLUMNAS_ESPERADAS)})."
        )

    desconocidas = set(df["no_factura"]) - set(archivos_por_factura)
    if desconocidas:
        raise ErrorDeProceso(
            "Hay filas con columnas desalineadas (No. Factura no coincide con ningún PDF). "
            f"Valores sospechosos: {sorted(map(str, desconocidas))[:5]}"
        )

    incidencias.extend(validar_calidad(df, tolerancia))

    if guardar_csv:
        CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
        df.to_csv(CARPETA_SALIDA / "salidadf.csv", index=False, encoding="utf-8-sig")

    return df, archivos_por_factura, incidencias
