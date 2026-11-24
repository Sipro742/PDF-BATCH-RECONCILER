import csv
from pathlib import Path
import re
import pandas as pd
import pdfplumber


def extraer_no_factura(texto: str) -> str | None:
    """Busca y devuelve el número de factura en el texto de una página."""
    match = re.search(r"No\.\s*Factura:\s*([^\s\n]+)", texto)
    return match.group(1) if match else None


def extraer_datos_pdf(pdf_path: Path) -> tuple[str | None, list]:
    """Extrae el número de factura y todas las tablas de un archivo PDF."""
    no_factura = None
    tablas_pdf = []

    with pdfplumber.open(pdf_path) as doc:
        for pagina in doc.pages:
            texto_pagina = pagina.extract_text() or ""
            if not no_factura:
                no_factura = extraer_no_factura(texto_pagina)

            for tabla in pagina.extract_tables():
                tablas_pdf.append(tabla)

    return no_factura, tablas_pdf

def eliminar_encabezados(df, columna="0"):
    # Elimina las filas donde la columna contiene "Descripcion" (sin importar mayúsculas/minúsculas o espacios)
    mascara = (
        df[columna]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.contains("descripcion", case=False, regex=False)
    )
    return df.loc[~mascara].copy()
def aplanar_con_factura(tablas_pdf: list, no_factura: str | None) -> list[list]:
    """Convierte las tablas de un PDF en filas planas anexando el número de factura."""
    filas = []
    for tabla in tablas_pdf:
        for fila in tabla:
            filas.append([*fila, no_factura])
    return filas

def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    # 1. Texto: descripción (columna 0)
    if 0 in df.columns:
        df[0] = (
            df[0]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
            .str.title()
        )

    # 2. Cantidad como entero (columna 1)
    if 1 in df.columns:
        df[1] = pd.to_numeric(
            df[1].astype(str).str.strip(),
            errors="coerce"
        ).astype("Int64")

    # 3. Precio unitario e importe como float (columnas 2 y 3)
    for col in [2, 3]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col]
                .astype(str)
                .str.replace(r"[$,\s]", "", regex=True),
                errors="coerce"
            )

    # 4. Número de factura como texto limpio (columna 4)
    if 4 in df.columns:
        df[4] = (
            df[4]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # 5. Renombrar al final
    return df.rename(columns={
        0: "descripcion",
        1: "cantidad",
        2: "precio_unitario",
        3: "importe",
        4: "no_factura",
    })

    
def exportar_resultados(filas_planas: list[list], ruta_base: Path) -> None:
    """Filtra encabezados repetidos y guarda el resultado en un único CSV."""
    df = pd.DataFrame(filas_planas)
    df = eliminar_encabezados(df, columna=0)
    df = normalizar_columnas(df)
    df.to_csv(ruta_base / "salidadf.csv", index=False, encoding="utf-8-sig")
    return df

def main() -> int:
    raiz = Path(__file__).resolve().parent.parent
    ruta_carpeta = raiz / "data" / "invoices"
    errores = []
    filas_planas = []

    if not ruta_carpeta.exists():
        errores.append("La carpeta no existe")
        return 2

    listado_de_pdf = sorted(ruta_carpeta.glob("*.pdf"))
    if not listado_de_pdf:
        errores.append("La carpeta no contiene PDF")

    for pdf in listado_de_pdf:
        try:
            no_factura, tablas_pdf = extraer_datos_pdf(pdf)

            if not tablas_pdf:
                errores.append(f"No se extrajeron tablas en {pdf.name}")
                continue

            filas_planas.extend(aplanar_con_factura(tablas_pdf, no_factura))
        except Exception as e:
            errores.append(f"{pdf.name}: {e}")
    resultados = exportar_resultados(filas_planas, raiz)
    return resultados


if __name__ == "__main__":
    main()

         

            


    