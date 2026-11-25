"""Carga del CSV maestro, agregación de montos y conciliación contra PDFs."""

from collections.abc import Callable
from datetime import datetime
import logging
from pathlib import Path

import pandas as pd

from extractor import extraer_lote, listar_pdfs
from models import (
    Configuracion,
    ErrorDeProceso,
    FilaDetalle,
    Hallazgo,
    Incidencia,
    ResultadoConciliacion,
)

LOG = logging.getLogger("conciliador")


def validar_entradas(config: Configuracion) -> list[Path]:
    """Valida que existan la carpeta de PDFs, al menos un PDF y el archivo maestro."""
    carpeta, maestro = config.carpeta_facturas, config.csv_maestro
    if not carpeta.is_dir():
        raise ErrorDeProceso(f"La carpeta de facturas no existe:\n{carpeta}")
    if not maestro.is_file():
        raise ErrorDeProceso(f"No se encontró el archivo maestro:\n{maestro}")
    pdfs = listar_pdfs(carpeta)
    if not pdfs:
        raise ErrorDeProceso(f"La carpeta no contiene archivos PDF:\n{carpeta}")
    return pdfs


def preparar_para_comparar(df_base: pd.DataFrame, col_factura: str, col_monto: str) -> pd.Series:
    """Normaliza clave y monto y devuelve una Serie indexada por número de factura."""
    serie_factura = df_base[col_factura].astype(str).str.strip()
    serie_monto = pd.to_numeric(df_base[col_monto], errors="coerce").round(2)
    return pd.Series(serie_monto.values, index=serie_factura, name="monto")


def cargar_maestro(ruta: Path) -> tuple[pd.Series, list[Incidencia]]:
    """Lee el CSV maestro (primera columna = factura, última = monto) y valida su contenido."""
    incidencias: list[Incidencia] = []
    try:
        mr = pd.read_csv(ruta, encoding="utf-8-sig")
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        raise ErrorDeProceso(f"No se pudo leer el maestro '{ruta.name}':\n{exc}") from exc

    if mr.shape[1] < 2 or mr.empty:
        raise ErrorDeProceso(
            "El maestro debe tener datos y al menos 2 columnas (número de factura ... monto)."
        )

    mr_extremos = mr.iloc[:, [0, -1]].copy()
    serie = preparar_para_comparar(mr_extremos, mr_extremos.columns[0], mr_extremos.columns[1])

    vacias = serie.index.isna() | serie.index.isin(["", "nan", "None"])
    if vacias.any():
        incidencias.append(Incidencia(
            "AVISO",
            ruta.name,
            f"{int(vacias.sum())} fila(s) sin número de factura; se omiten.",
        ))
        serie = serie[~vacias]

    for factura in serie.index[serie.index.duplicated()].unique():
        incidencias.append(Incidencia(
            "AVISO",
            str(factura),
            "Repetida en el maestro; se usa la primera fila.",
        ))

    return serie[~serie.index.duplicated(keep="first")], incidencias


def sumar_por_factura(df: pd.DataFrame, tasa: float = 0.12) -> pd.Series:
    """Agrupa por no_factura, suma importes, aplica tasa de impuesto y devuelve Serie."""
    if df.empty:
        return pd.Series(dtype="float64", name="monto")

    df_resumen = df.groupby("no_factura", as_index=False)["importe"].sum()
    df_resumen["importe"] = (df_resumen["importe"] * (1 + tasa)).round(2)
    return preparar_para_comparar(df_resumen, "no_factura", "importe")


def conciliar(
    s_pdf: pd.Series,
    s_maestro: pd.Series,
    archivos_por_factura: dict[str, list[str]],
    tolerancia: float,
) -> tuple[list[FilaDetalle], list[Hallazgo]]:
    """Compara factura por factura, generando el detalle completo y los hallazgos."""
    d_pdf = s_pdf.to_dict()
    d_maestro = s_maestro.to_dict()
    universo = sorted(set(d_pdf) | set(d_maestro))

    detalle: list[FilaDetalle] = []
    hallazgos: list[Hallazgo] = []

    for factura in universo:
        extraido = d_pdf.get(factura)
        maestro = d_maestro.get(factura)
        archivos = archivos_por_factura.get(factura, [])
        hallazgos_factura: list[Hallazgo] = []

        if len(archivos) > 1:
            hallazgos_factura.append(Hallazgo(
                factura,
                "PDF_DUPLICADO",
                extraido,
                maestro,
                f"{len(archivos)} archivos: {', '.join(archivos)}. Solo se procesó el primero.",
            ))

        if extraido is None:
            hallazgos_factura.append(Hallazgo(
                factura,
                "FALTA_PDF",
                None,
                maestro,
                "Existe en el maestro pero no hay archivo PDF.",
            ))
        elif maestro is None:
            hallazgos_factura.append(Hallazgo(
                factura,
                "FALTA_MAESTRO",
                extraido,
                None,
                "Existe el PDF pero no está registrado en el maestro.",
            ))
        elif pd.isna(extraido) or pd.isna(maestro):
            hallazgos_factura.append(Hallazgo(
                factura,
                "MONTO_INVALIDO",
                extraido,
                maestro,
                "Uno de los montos no es numérico.",
            ))
        else:
            diferencia = round(float(extraido) - float(maestro), 2)
            if abs(diferencia) > tolerancia:
                hallazgos_factura.append(Hallazgo(
                    factura,
                    "DIFERENCIA_MONTO",
                    extraido,
                    maestro,
                    f"El PDF difiere del maestro en {diferencia:+,.2f}.",
                ))

        hallazgos.extend(hallazgos_factura)

        if hallazgos_factura:
            estado = " + ".join(h.tipo for h in hallazgos_factura)
            texto_detalle = " | ".join(h.detalle for h in hallazgos_factura)
        else:
            estado = "OK"
            texto_detalle = "Conciliación exacta."

        dif_fila = (
            None
            if extraido is None or maestro is None or pd.isna(extraido) or pd.isna(maestro)
            else round(float(extraido) - float(maestro), 2)
        )
        detalle.append(FilaDetalle(
            no_factura=factura,
            monto_extraido=None if extraido is None or pd.isna(extraido) else round(float(extraido), 2),
            monto_maestro=None if maestro is None or pd.isna(maestro) else round(float(maestro), 2),
            diferencia=dif_fila,
            estado=estado,
            detalle=texto_detalle,
        ))

    return detalle, hallazgos


def ejecutar_conciliacion(
    config: Configuracion,
    al_avanzar: Callable[[Path], None] | None = None,
) -> ResultadoConciliacion:
    """Orquesta validación, carga de maestro, extracción y conciliación completa."""
    resultado = ResultadoConciliacion(
        inicio=datetime.now(),
        modo=config.modo,
        carpeta=str(config.carpeta_facturas),
        maestro=str(config.csv_maestro),
        tasa=config.tasa_impuesto,
        tolerancia=config.tolerancia,
    )

    # 1. Validar entradas
    pdfs = validar_entradas(config)
    resultado.pdfs_leidos = len(pdfs)

    # 2. Cargar maestro primero (fail-fast antes de procesar todos los PDFs)
    s_maestro, incidencias_maestro = cargar_maestro(config.csv_maestro)
    resultado.incidencias.extend(incidencias_maestro)

    # 3. Extraer PDFs y sumar por factura
    df, archivos_por_factura, incidencias_ext = extraer_lote(
        pdfs,
        tolerancia=config.tolerancia,
        guardar_csv=config.guardar_csv_extraido,
        al_avanzar=al_avanzar,
    )
    resultado.incidencias.extend(incidencias_ext)
    s_pdf = sumar_por_factura(df, config.tasa_impuesto)

    # 4. Conciliar
    resultado.detalle, resultado.hallazgos = conciliar(
        s_pdf,
        s_maestro,
        archivos_por_factura,
        config.tolerancia,
    )
    resultado.universo = {fila.no_factura for fila in resultado.detalle}
    resultado.facturas_pdf = len(s_pdf)
    resultado.registros_maestro = len(s_maestro)
    resultado.coincidentes = len(resultado.universo) - resultado.facturas_con_hallazgos

    LOG.info(
        "Conciliación | modo=%s | pdfs=%d | hallazgos=%d | incidencias=%d",
        config.modo,
        resultado.pdfs_leidos,
        len(resultado.hallazgos),
        len(resultado.incidencias),
    )
    return resultado
