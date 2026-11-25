from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import sys

from fpdf import FPDF
import pandas as pd

from extractor import (
    aplanar_con_factura,
    eliminar_encabezados,
    extraer_datos_pdf,
    normalizar_columnas,
)
from generate_mock_data import (
    CARPETA_FACTURAS as CARPETA_FACTURAS_PRUEBA,
    RUTA_CSV_MAESTRO as RUTA_CSV_PRUEBA,
    generar_datos_prueba,
)
from reconciler import (
    RUTA_CSV as RUTA_CSV_RECONCILER,
    preparar_para_comparar,
    sumar_por_factura,
)

# Rutas base del proyecto (alineadas con extractor.py y reconciler.py)
DIRECTORIO_BASE = Path(__file__).resolve().parent.parent
CARPETA_DATA = DIRECTORIO_BASE / "data"
CARPETA_REPORTES_PDF = CARPETA_DATA / "reportes_pdf"
RUTA_BASE_DATOS = CARPETA_DATA / "historial_reportes.db"
CARPETA_FACTURAS_REALES = CARPETA_DATA / "invoices"
RUTA_CSV_REAL = RUTA_CSV_RECONCILER

TOLERANCIA_MONTO = 0.01

# Codigos ANSI para una interfaz comoda en terminal
RESET = "\033[0m"
NEGRITA = "\033[1m"
TENUE = "\033[2m"
CIAN = "\033[36m"
VERDE = "\033[32m"
AMARILLO = "\033[33m"
ROJO = "\033[31m"
AZUL = "\033[34m"


def resolver_csv_prueba() -> Path:
    """Usa el CSV generado por mock si existe; si no, usa el RUTA_CSV de reconciler."""
    if RUTA_CSV_PRUEBA.exists():
        return RUTA_CSV_PRUEBA
    if RUTA_CSV_RECONCILER.exists():
        return RUTA_CSV_RECONCILER
    return RUTA_CSV_RECONCILER


@dataclass
class Configuracion:
    """Almacena la configuracion activa de ejecucion."""

    modo: str = "prueba"
    carpeta_pdfs: Path = CARPETA_FACTURAS_PRUEBA
    ruta_csv_maestro: Path = field(default_factory=resolver_csv_prueba)
    tasa_impuesto: float = 0.12
    guardar_csv_extraido: bool = True
    carpeta_reportes: Path = CARPETA_REPORTES_PDF
    ruta_bd: Path = RUTA_BASE_DATOS


# --- UTILIDADES DE INTERFAZ EN TERMINAL ---


def habilitar_colores_terminal() -> None:
    """Habilita soporte ANSI en terminales Windows cuando es necesario."""
    if os.name == "nt":
        os.system("")


def limpiar_pantalla() -> None:
    """Limpia la terminal solo cuando se ejecuta en modo interactivo."""
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")


def pausar(mensaje: str = "Presiona Enter para continuar...") -> None:
    """Espera confirmacion del usuario antes de volver al menu."""
    try:
        input(f"\n{TENUE}{mensaje}{RESET}")
    except EOFError:
        pass


def imprimir_linea(caracter: str = "─", ancho: int = 72, color: str = TENUE) -> None:
    """Imprime un separador visual limpio."""
    print(f"{color}{caracter * ancho}{RESET}")


def imprimir_encabezado(config: Configuracion) -> None:
    """Muestra el banner superior y el estado actual de configuracion."""
    ancho = 72
    etiqueta_modo = (
        f"{AMARILLO}MODO PRUEBA{RESET}"
        if config.modo == "prueba"
        else f"{VERDE}MODO ARCHIVOS REALES{RESET}"
    )

    print(f"{CIAN}{NEGRITA}┌{'─' * (ancho - 2)}┐{RESET}")
    titulo = "SISTEMA DE CONCILIACION Y AUDITORIA DE FACTURAS PDF"
    print(f"{CIAN}{NEGRITA}│{titulo.center(ancho - 2)}│{RESET}")
    print(f"{CIAN}{NEGRITA}└{'─' * (ancho - 2)}┘{RESET}")
    print(f"  {NEGRITA}Modo activo :{RESET} {etiqueta_modo}")
    print(f"  {NEGRITA}Carpeta PDF :{RESET} {config.carpeta_pdfs}")
    print(f"  {NEGRITA}CSV Maestro :{RESET} {config.ruta_csv_maestro}")
    print(f"  {NEGRITA}Tasa IVA    :{RESET} {config.tasa_impuesto * 100:.0f}%")
    imprimir_linea(ancho=ancho)


def imprimir_mensaje(texto: str, tipo: str = "info") -> None:
    """Muestra un mensaje formateado con icono y color segun severidad."""
    estilos = {
        "ok": (VERDE, "[OK]"),
        "info": (CIAN, "[INFO]"),
        "aviso": (AMARILLO, "[AVISO]"),
        "error": (ROJO, "[ERROR]"),
    }
    color, prefijo = estilos.get(tipo, (CIAN, "[INFO]"))
    print(f"{color}{NEGRITA}{prefijo}{RESET} {texto}")


def formatear_monto(valor: float | None) -> str:
    """Formatea un monto numerico o devuelve '-' si no aplica."""
    if valor is None or pd.isna(valor):
        return "-"
    return f"${valor:,.2f}"


# --- BASE DE DATOS DE REPORTES PDF (SQLITE) ---


def inicializar_bd(ruta_bd: Path) -> None:
    """Crea la base de datos SQLite y la tabla de historial si no existen."""
    ruta_bd.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ruta_bd) as conexion:
        conexion.execute(
            """
            CREATE TABLE IF NOT EXISTS reportes_pdf (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_ejecucion TEXT NOT NULL,
                modo TEXT NOT NULL,
                carpeta_origen TEXT NOT NULL,
                csv_maestro TEXT NOT NULL,
                total_facturas INTEGER NOT NULL,
                total_ok INTEGER NOT NULL,
                total_irregularidades INTEGER NOT NULL,
                total_errores INTEGER NOT NULL,
                ruta_pdf TEXT NOT NULL
            )
            """
        )


def registrar_reporte_bd(
    ruta_bd: Path,
    resultado: dict,
    config: Configuracion,
    ruta_pdf: Path,
) -> int:
    """Guarda el registro de un reporte PDF generado y devuelve su ID."""
    inicializar_bd(ruta_bd)
    resumen = resultado["resumen"]

    with sqlite3.connect(ruta_bd) as conexion:
        cursor = conexion.execute(
            """
            INSERT INTO reportes_pdf (
                fecha_ejecucion,
                modo,
                carpeta_origen,
                csv_maestro,
                total_facturas,
                total_ok,
                total_irregularidades,
                total_errores,
                ruta_pdf
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resultado["fecha_ejecucion"],
                config.modo,
                str(config.carpeta_pdfs),
                str(config.ruta_csv_maestro),
                resumen["total_facturas"],
                resumen["ok"],
                resumen["irregularidades"],
                resumen["errores_ejecucion"],
                str(ruta_pdf),
            ),
        )
        return int(cursor.lastrowid)


def listar_reportes_bd(ruta_bd: Path, limite: int = 15) -> list[tuple]:
    """Consulta los reportes PDF mas recientes almacenados en la base de datos."""
    inicializar_bd(ruta_bd)
    with sqlite3.connect(ruta_bd) as conexion:
        cursor = conexion.execute(
            """
            SELECT
                id,
                fecha_ejecucion,
                modo,
                total_facturas,
                total_ok,
                total_irregularidades,
                total_errores,
                ruta_pdf
            FROM reportes_pdf
            ORDER BY id DESC
            LIMIT ?
            """,
            (limite,),
        )
        return cursor.fetchall()


# --- EXTRACCION Y RECONCILIACION ---


def procesar_lote_pdfs(
    carpeta_pdfs: Path,
    guardar_csv: bool = True,
) -> dict:
    """Extrae los PDFs de la carpeta configurada, detectando duplicados y errores."""
    errores_ejecucion: list[dict] = []
    filas_planas: list[list] = []
    archivos_por_factura: dict[str, list[str]] = {}
    columnas_esperadas = [
        "descripcion",
        "cantidad",
        "precio_unitario",
        "importe",
        "no_factura",
    ]

    if not carpeta_pdfs.exists() or not carpeta_pdfs.is_dir():
        errores_ejecucion.append({
            "origen": str(carpeta_pdfs),
            "detalle": "La carpeta de facturas PDF no existe o no es un directorio valido.",
        })
        return {
            "df_extraido": pd.DataFrame(columns=columnas_esperadas),
            "total_archivos": 0,
            "duplicados": {},
            "errores_ejecucion": errores_ejecucion,
        }

    listado_pdfs = sorted(carpeta_pdfs.glob("*.pdf"))
    if not listado_pdfs:
        errores_ejecucion.append({
            "origen": str(carpeta_pdfs),
            "detalle": "No se encontraron archivos PDF en la carpeta configurada.",
        })
        return {
            "df_extraido": pd.DataFrame(columns=columnas_esperadas),
            "total_archivos": 0,
            "duplicados": {},
            "errores_ejecucion": errores_ejecucion,
        }

    for pdf in listado_pdfs:
        try:
            no_factura, tablas_pdf = extraer_datos_pdf(pdf)

            if not tablas_pdf:
                errores_ejecucion.append({
                    "origen": pdf.name,
                    "detalle": "No se extrajeron tablas del archivo PDF.",
                })
                continue

            if not no_factura:
                errores_ejecucion.append({
                    "origen": pdf.name,
                    "detalle": "No se encontro el numero de factura en el documento.",
                })
                continue

            clave_factura = no_factura.strip()
            archivos_por_factura.setdefault(clave_factura, []).append(pdf.name)

            # Si es una copia duplicada, se registra la irregularidad sin duplicar montos
            if len(archivos_por_factura[clave_factura]) > 1:
                continue

            filas_planas.extend(aplanar_con_factura(tablas_pdf, clave_factura))

        except Exception as exc:
            errores_ejecucion.append({
                "origen": pdf.name,
                "detalle": f"Error al leer PDF: {exc}",
            })

    duplicados = {
        factura: archivos
        for factura, archivos in archivos_por_factura.items()
        if len(archivos) > 1
    }

    if filas_planas:
        df = pd.DataFrame(filas_planas)
        df = eliminar_encabezados(df, columna=0)
        df = normalizar_columnas(df)
        if guardar_csv:
            df.to_csv(DIRECTORIO_BASE / "salidadf.csv", index=False, encoding="utf-8-sig")
    else:
        df = pd.DataFrame(columns=columnas_esperadas)

    return {
        "df_extraido": df,
        "total_archivos": len(listado_pdfs),
        "duplicados": duplicados,
        "errores_ejecucion": errores_ejecucion,
    }


def cargar_csv_maestro(ruta_csv: Path) -> tuple[pd.DataFrame | None, str | None]:
    """Lee y valida la estructura minima del CSV maestro."""
    if not ruta_csv.exists():
        return None, f"No se encontro el CSV maestro en: {ruta_csv}"

    try:
        df_maestro = pd.read_csv(ruta_csv, encoding="utf-8-sig")
    except Exception as exc:
        return None, f"Error al leer el CSV maestro ({ruta_csv.name}): {exc}"

    if df_maestro.empty:
        return None, f"El CSV maestro ({ruta_csv.name}) no contiene registros."

    if df_maestro.shape[1] < 2:
        return None, (
            f"El CSV maestro ({ruta_csv.name}) debe tener al menos 2 columnas "
            "(numero de factura y monto total)."
        )

    return df_maestro, None


def ejecutar_conciliacion(config: Configuracion) -> dict:
    """Conecta la extraccion y la reconciliacion devolviendo un informe estructurado."""
    fecha_ejecucion = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    extraccion = procesar_lote_pdfs(
        config.carpeta_pdfs,
        guardar_csv=config.guardar_csv_extraido,
    )
    errores_ejecucion = list(extraccion["errores_ejecucion"])
    duplicados = extraccion["duplicados"]
    df_extraido = extraccion["df_extraido"]

    df_maestro, error_csv = cargar_csv_maestro(config.ruta_csv_maestro)
    if error_csv:
        errores_ejecucion.append({
            "origen": str(config.ruta_csv_maestro.name),
            "detalle": error_csv,
        })

    # Serie de montos extraidos desde los PDFs
    if not df_extraido.empty and {"importe", "no_factura"}.issubset(df_extraido.columns):
        df_ultimas_dos = df_extraido[["importe", "no_factura"]].copy()
        df_funcional = sumar_por_factura(df_ultimas_dos, tasa=config.tasa_impuesto)
        s_extraido = preparar_para_comparar(df_funcional, "no_factura", "importe")
    else:
        s_extraido = pd.Series(dtype="float64", name="monto")

    # Serie de montos esperados desde el CSV maestro
    duplicados_maestro: set[str] = set()
    if df_maestro is not None:
        mr_extremos = df_maestro.iloc[:, [0, -1]].copy()
        col_fact_mr = mr_extremos.columns[0]
        col_monto_mr = mr_extremos.columns[1]
        s_maestro = preparar_para_comparar(mr_extremos, col_fact_mr, col_monto_mr)

        if s_maestro.index.duplicated().any():
            duplicados_maestro = set(s_maestro.index[s_maestro.index.duplicated()].tolist())
            errores_ejecucion.append({
                "origen": config.ruta_csv_maestro.name,
                "detalle": (
                    "Facturas repetidas en el CSV maestro: "
                    + ", ".join(sorted(duplicados_maestro))
                ),
            })
            s_maestro = s_maestro[~s_maestro.index.duplicated(keep="first")]
    else:
        s_maestro = pd.Series(dtype="float64", name="monto")

    # Alinear indices y clasificar cada factura
    s_ext_al, s_mr_al = s_extraido.align(s_maestro)
    detalle: list[dict] = []

    for no_factura in sorted(s_ext_al.index):
        val_ext = s_ext_al.loc[no_factura]
        val_mr = s_mr_al.loc[no_factura]
        monto_ext = None if pd.isna(val_ext) else round(float(val_ext), 2)
        monto_mr = None if pd.isna(val_mr) else round(float(val_mr), 2)

        if monto_ext is not None and monto_mr is not None:
            diferencia = round(monto_ext - monto_mr, 2)
        else:
            diferencia = None

        if no_factura in duplicados:
            archivos = ", ".join(duplicados[no_factura])
            estado = "DUPLICADO"
            observacion = f"PDF duplicado ({archivos})"
            if diferencia is not None and abs(diferencia) > TOLERANCIA_MONTO:
                observacion += f" | Dif. monto: {diferencia:+.2f}"
        elif monto_ext is None and monto_mr is not None:
            estado = "FALTANTE_EN_PDF"
            observacion = "Registrada en CSV maestro pero falta el archivo PDF."
        elif monto_ext is not None and monto_mr is None:
            estado = "FALTANTE_EN_MAESTRO"
            observacion = "PDF encontrado pero ausente en el CSV maestro."
        elif diferencia is not None and abs(diferencia) > TOLERANCIA_MONTO:
            estado = "DIFERENCIA_MONTO"
            observacion = f"Descuadre de {diferencia:+.2f} contra el CSV maestro."
        elif no_factura in duplicados_maestro:
            estado = "DUPLICADO_MAESTRO"
            observacion = "ID repetido dentro del CSV maestro."
        else:
            estado = "OK"
            observacion = "Conciliacion exacta."

        detalle.append({
            "no_factura": no_factura,
            "monto_extraido": monto_ext,
            "monto_maestro": monto_mr,
            "diferencia": diferencia,
            "estado": estado,
            "observacion": observacion,
        })

    irregularidades = [fila for fila in detalle if fila["estado"] != "OK"]
    conteo_estados = {
        "ok": sum(1 for f in detalle if f["estado"] == "OK"),
        "diferencia_monto": sum(1 for f in detalle if f["estado"] == "DIFERENCIA_MONTO"),
        "faltante_pdf": sum(1 for f in detalle if f["estado"] == "FALTANTE_EN_PDF"),
        "faltante_maestro": sum(1 for f in detalle if f["estado"] == "FALTANTE_EN_MAESTRO"),
        "duplicado": sum(1 for f in detalle if f["estado"] in {"DUPLICADO", "DUPLICADO_MAESTRO"}),
    }

    resumen = {
        "total_archivos_pdf": extraccion["total_archivos"],
        "total_facturas": len(detalle),
        "ok": conteo_estados["ok"],
        "irregularidades": len(irregularidades),
        "diferencia_monto": conteo_estados["diferencia_monto"],
        "faltante_pdf": conteo_estados["faltante_pdf"],
        "faltante_maestro": conteo_estados["faltante_maestro"],
        "duplicado": conteo_estados["duplicado"],
        "errores_ejecucion": len(errores_ejecucion),
    }

    return {
        "fecha_ejecucion": fecha_ejecucion,
        "resumen": resumen,
        "detalle": detalle,
        "irregularidades": irregularidades,
        "errores_ejecucion": errores_ejecucion,
    }


# --- GENERACION DE REPORTES EN PDF ---


def texto_pdf_seguro(texto: str) -> str:
    """Convierte caracteres fuera de Latin-1 para evitar errores en fuentes base de FPDF."""
    return str(texto).encode("latin-1", "replace").decode("latin-1")


def generar_reporte_pdf(resultado: dict, config: Configuracion) -> Path:
    """Genera un reporte PDF profesional de la conciliacion y lo guarda en disco."""
    config.carpeta_reportes.mkdir(parents=True, exist_ok=True)
    marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"reporte_conciliacion_{marca_tiempo}_{config.modo}.pdf"
    ruta_pdf = config.carpeta_reportes / nombre_archivo

    resumen = resultado["resumen"]
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Encabezado del reporte
    pdf.set_fill_color(28, 45, 74)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(
        0,
        12,
        texto_pdf_seguro("REPORTE DE CONCILIACION DE FACTURAS"),
        align="C",
        fill=True,
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    # Metadatos de ejecucion
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Helvetica", "", 10)
    metadatos = [
        f"Fecha de ejecucion: {resultado['fecha_ejecucion']}",
        f"Modo de operacion: {config.modo.upper()}",
        f"Carpeta de PDFs: {config.carpeta_pdfs}",
        f"Archivo CSV maestro: {config.ruta_csv_maestro}",
        f"Tasa de impuesto aplicada: {config.tasa_impuesto * 100:.0f}%",
    ]
    for linea in metadatos:
        pdf.cell(0, 6, texto_pdf_seguro(linea), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Resumen ejecutivo
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(235, 240, 248)
    pdf.cell(
        0,
        8,
        "1. RESUMEN EJECUTIVO",
        fill=True,
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)

    metricas = [
        ("Archivos PDF encontrados", str(resumen["total_archivos_pdf"])),
        ("Facturas evaluadas", str(resumen["total_facturas"])),
        ("Conciliacion exacta (OK)", str(resumen["ok"])),
        ("Total irregularidades", str(resumen["irregularidades"])),
        ("  - Diferencia de monto", str(resumen["diferencia_monto"])),
        ("  - Faltante en PDF", str(resumen["faltante_pdf"])),
        ("  - Faltante en CSV maestro", str(resumen["faltante_maestro"])),
        ("  - Duplicados", str(resumen["duplicado"])),
        ("Errores de ejecucion", str(resumen["errores_ejecucion"])),
    ]
    pdf.set_font("Helvetica", "", 10)
    for etiqueta, valor in metricas:
        pdf.cell(120, 6, texto_pdf_seguro(etiqueta), border=1)
        pdf.cell(
            70,
            6,
            texto_pdf_seguro(valor),
            border=1,
            align="R",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    pdf.ln(6)

    # Seccion de irregularidades
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(252, 236, 224)
    pdf.cell(
        0,
        8,
        "2. IRREGULARIDADES DETECTADAS",
        fill=True,
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)

    if not resultado["irregularidades"]:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(
            0,
            7,
            "No se detectaron irregularidades. Todas las facturas coinciden.",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    else:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(32, 7, "Factura", border=1, fill=True)
        pdf.cell(26, 7, "PDF", border=1, align="R", fill=True)
        pdf.cell(26, 7, "Maestro", border=1, align="R", fill=True)
        pdf.cell(24, 7, "Dif.", border=1, align="R", fill=True)
        pdf.cell(36, 7, "Estado", border=1, align="C", fill=True)
        pdf.cell(
            46,
            7,
            "Observacion",
            border=1,
            fill=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )

        pdf.set_font("Helvetica", "", 8)
        for fila in resultado["irregularidades"]:
            dif_txt = (
                f"{fila['diferencia']:+,.2f}"
                if fila["diferencia"] is not None
                else "-"
            )
            obs_corta = fila["observacion"][:32]
            pdf.cell(32, 6, texto_pdf_seguro(fila["no_factura"]), border=1)
            pdf.cell(
                26,
                6,
                texto_pdf_seguro(formatear_monto(fila["monto_extraido"])),
                border=1,
                align="R",
            )
            pdf.cell(
                26,
                6,
                texto_pdf_seguro(formatear_monto(fila["monto_maestro"])),
                border=1,
                align="R",
            )
            pdf.cell(24, 6, texto_pdf_seguro(dif_txt), border=1, align="R")
            pdf.cell(36, 6, texto_pdf_seguro(fila["estado"]), border=1, align="C")
            pdf.cell(
                46,
                6,
                texto_pdf_seguro(obs_corta),
                border=1,
                new_x="LMARGIN",
                new_y="NEXT",
            )
    pdf.ln(6)

    # Seccion de errores de ejecucion
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(250, 226, 226)
    pdf.cell(
        0,
        8,
        "3. ERRORES DE EJECUCION",
        fill=True,
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)

    if not resultado["errores_ejecucion"]:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(
            0,
            7,
            "No se produjeron errores tecnicos durante la ejecucion.",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    else:
        pdf.set_font("Helvetica", "", 9)
        for err in resultado["errores_ejecucion"]:
            linea = f"- [{err['origen']}] {err['detalle']}"
            pdf.multi_cell(0, 6, texto_pdf_seguro(linea), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Detalle completo de facturas evaluadas
    if resultado["detalle"]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(235, 240, 248)
        pdf.cell(
            0,
            8,
            "4. DETALLE COMPLETO DE FACTURAS",
            fill=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(40, 7, "No. Factura", border=1, fill=True)
        pdf.cell(35, 7, "Total PDF", border=1, align="R", fill=True)
        pdf.cell(35, 7, "Total Maestro", border=1, align="R", fill=True)
        pdf.cell(30, 7, "Diferencia", border=1, align="R", fill=True)
        pdf.cell(
            50,
            7,
            "Estado",
            border=1,
            align="C",
            fill=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )

        pdf.set_font("Helvetica", "", 9)
        for fila in resultado["detalle"]:
            dif_txt = (
                f"{fila['diferencia']:+,.2f}"
                if fila["diferencia"] is not None
                else "-"
            )
            pdf.cell(40, 6, texto_pdf_seguro(fila["no_factura"]), border=1)
            pdf.cell(
                35,
                6,
                texto_pdf_seguro(formatear_monto(fila["monto_extraido"])),
                border=1,
                align="R",
            )
            pdf.cell(
                35,
                6,
                texto_pdf_seguro(formatear_monto(fila["monto_maestro"])),
                border=1,
                align="R",
            )
            pdf.cell(30, 6, texto_pdf_seguro(dif_txt), border=1, align="R")
            pdf.cell(
                50,
                6,
                texto_pdf_seguro(fila["estado"]),
                border=1,
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )

    pdf.output(str(ruta_pdf))
    return ruta_pdf


# --- VISUALIZACION DE RESULTADOS EN TERMINAL ---


def mostrar_resultados_en_terminal(resultado: dict) -> None:
    """Imprime un resumen claro de metricas, irregularidades y errores."""
    resumen = resultado["resumen"]

    print(f"\n{AZUL}{NEGRITA}RESUMEN DE CONCILIACION{RESET}")
    imprimir_linea()
    print(f"  Archivos PDF leidos     : {resumen['total_archivos_pdf']}")
    print(f"  Facturas evaluadas      : {resumen['total_facturas']}")
    print(f"  {VERDE}Conciliacion exacta (OK): {resumen['ok']}{RESET}")
    color_irr = AMARILLO if resumen["irregularidades"] > 0 else VERDE
    print(f"  {color_irr}Irregularidades         : {resumen['irregularidades']}{RESET}")
    color_err = ROJO if resumen["errores_ejecucion"] > 0 else VERDE
    print(f"  {color_err}Errores de ejecucion    : {resumen['errores_ejecucion']}{RESET}")

    if resultado["irregularidades"]:
        print(f"\n{AMARILLO}{NEGRITA}IRREGULARIDADES DETECTADAS{RESET}")
        imprimir_linea()
        encabezado = (
            f"  {'FACTURA':<15} {'PDF':>11} {'MAESTRO':>11} "
            f"{'DIF.':>10}  {'ESTADO':<20}"
        )
        print(f"{NEGRITA}{encabezado}{RESET}")
        imprimir_linea()
        for fila in resultado["irregularidades"]:
            dif_txt = (
                f"{fila['diferencia']:+,.2f}"
                if fila["diferencia"] is not None
                else "-"
            )
            print(
                f"  {fila['no_factura']:<15} "
                f"{formatear_monto(fila['monto_extraido']):>11} "
                f"{formatear_monto(fila['monto_maestro']):>11} "
                f"{dif_txt:>10}  "
                f"{AMARILLO}{fila['estado']:<20}{RESET}"
            )
            print(f"    {TENUE}↳ {fila['observacion']}{RESET}")
    else:
        print()
        imprimir_mensaje("Todas las facturas coinciden al 100%.", "ok")

    if resultado["errores_ejecucion"]:
        print(f"\n{ROJO}{NEGRITA}ERRORES DE EJECUCION{RESET}")
        imprimir_linea()
        for err in resultado["errores_ejecucion"]:
            print(f"  {ROJO}• [{err['origen']}]{RESET} {err['detalle']}")


# --- ACCIONES DEL MENU INTERACTIVO ---


def accion_ejecutar_conciliacion(config: Configuracion) -> None:
    """Ejecuta la conciliacion, muestra resultados y guarda el reporte en PDF + BD."""
    limpiar_pantalla()
    imprimir_encabezado(config)
    imprimir_mensaje("Procesando facturas y comparando contra el CSV maestro...", "info")

    try:
        resultado = ejecutar_conciliacion(config)
    except Exception as exc:
        imprimir_mensaje(f"Fallo inesperado durante la conciliacion: {exc}", "error")
        pausar()
        return

    mostrar_resultados_en_terminal(resultado)

    print()
    respuesta = input("¿Deseas generar y guardar el reporte en PDF? [S/n]: ").strip().lower()
    if respuesta in {"", "s", "si", "sí", "y"}:
        try:
            ruta_pdf = generar_reporte_pdf(resultado, config)
            id_registro = registrar_reporte_bd(config.ruta_bd, resultado, config, ruta_pdf)
            imprimir_mensaje(f"Reporte PDF guardado en: {ruta_pdf}", "ok")
            imprimir_mensaje(
                f"Registrado en la base de datos con ID #{id_registro} ({config.ruta_bd.name}).",
                "info",
            )
        except Exception as exc:
            imprimir_mensaje(f"No se pudo guardar el reporte PDF: {exc}", "error")
    else:
        imprimir_mensaje("Reporte PDF omitido por el usuario.", "aviso")

    pausar()


def accion_generar_datos_prueba(config: Configuracion) -> None:
    """Genera un lote fresco de PDFs y CSV maestro para probar el sistema."""
    limpiar_pantalla()
    imprimir_encabezado(config)
    print(f"{NEGRITA}GENERADOR DE DATOS DE PRUEBA{RESET}")
    imprimir_linea()

    entrada = input("Cantidad de facturas a generar [Enter = 20]: ").strip()
    if not entrada:
        cantidad = 20
    else:
        try:
            cantidad = int(entrada)
            if cantidad < 1:
                raise ValueError
        except ValueError:
            imprimir_mensaje("Ingresa un numero entero positivo.", "error")
            pausar()
            return

    try:
        generar_datos_prueba(total_registros=cantidad)
        if config.modo == "prueba":
            config.carpeta_pdfs = CARPETA_FACTURAS_PRUEBA
            config.ruta_csv_maestro = RUTA_CSV_PRUEBA
        imprimir_mensaje(
            f"Se generaron los datos de prueba ({cantidad} registros base).",
            "ok",
        )
        if config.modo != "prueba":
            imprimir_mensaje(
                "Recuerda cambiar al MODO PRUEBA en Configuracion para usar este lote.",
                "aviso",
            )
    except Exception as exc:
        imprimir_mensaje(f"Error al generar datos de prueba: {exc}", "error")

    pausar()


def accion_configurar(config: Configuracion) -> None:
    """Permite alternar entre modo prueba/real y ajustar rutas o tasa de IVA."""
    while True:
        limpiar_pantalla()
        imprimir_encabezado(config)
        print(f"{NEGRITA}CONFIGURACION DEL PROGRAMA{RESET}")
        imprimir_linea()
        print("  1. Usar MODO PRUEBA (archivos generados automaticamente)")
        print("  2. Usar MODO ARCHIVOS REALES (definir carpeta PDF y CSV maestro)")
        print(f"  3. Cambiar tasa de impuesto (actual: {config.tasa_impuesto * 100:.0f}%)")
        estado_csv = "SI" if config.guardar_csv_extraido else "NO"
        print(f"  4. Exportar CSV intermedio salidadf.csv (actual: {estado_csv})")
        print("  0. Volver al menu principal")
        imprimir_linea()

        opcion = input("Selecciona una opcion: ").strip()

        if opcion == "1":
            config.modo = "prueba"
            config.carpeta_pdfs = CARPETA_FACTURAS_PRUEBA
            config.ruta_csv_maestro = resolver_csv_prueba()
            imprimir_mensaje("Configuracion cambiada a MODO PRUEBA.", "ok")
            pausar()

        elif opcion == "2":
            print(f"\n{TENUE}Deja vacio y presiona Enter para mantener la ruta sugerida.{RESET}")
            ruta_pdf_sugerida = (
                config.carpeta_pdfs
                if config.modo == "real"
                else CARPETA_FACTURAS_REALES
            )
            ruta_csv_sugerida = (
                config.ruta_csv_maestro
                if config.modo == "real"
                else RUTA_CSV_REAL
            )

            nueva_carpeta = input(f"Carpeta de PDFs reales [{ruta_pdf_sugerida}]: ").strip()
            nuevo_csv = input(f"Ruta del CSV maestro real [{ruta_csv_sugerida}]: ").strip()

            config.modo = "real"
            config.carpeta_pdfs = Path(nueva_carpeta) if nueva_carpeta else ruta_pdf_sugerida
            config.ruta_csv_maestro = Path(nuevo_csv) if nuevo_csv else ruta_csv_sugerida

            imprimir_mensaje("Configuracion cambiada a MODO ARCHIVOS REALES.", "ok")
            if not config.carpeta_pdfs.exists():
                imprimir_mensaje(
                    f"Aviso: la carpeta {config.carpeta_pdfs} aun no existe.",
                    "aviso",
                )
            if not config.ruta_csv_maestro.exists():
                imprimir_mensaje(
                    f"Aviso: el archivo {config.ruta_csv_maestro} aun no existe.",
                    "aviso",
                )
            pausar()

        elif opcion == "3":
            nueva_tasa = input("Nueva tasa en porcentaje (ej. 12 para 12%, 0 para sin IVA): ").strip()
            try:
                valor = float(nueva_tasa)
                if valor < 0 or valor > 100:
                    raise ValueError
                config.tasa_impuesto = round(valor / 100, 4)
                imprimir_mensaje(f"Tasa actualizada a {valor:.2f}%.", "ok")
            except ValueError:
                imprimir_mensaje("Ingresa un porcentaje valido entre 0 y 100.", "error")
            pausar()

        elif opcion == "4":
            config.guardar_csv_extraido = not config.guardar_csv_extraido
            estado = "activada" if config.guardar_csv_extraido else "desactivada"
            imprimir_mensaje(f"Exportacion de salidadf.csv {estado}.", "ok")
            pausar()

        elif opcion == "0":
            return

        else:
            imprimir_mensaje("Opcion no valida.", "aviso")
            pausar()


def accion_ver_historial(config: Configuracion) -> None:
    """Muestra los ultimos reportes PDF registrados en la base de datos SQLite."""
    limpiar_pantalla()
    imprimir_encabezado(config)
    print(f"{NEGRITA}HISTORIAL DE REPORTES PDF GUARDADOS{RESET}")
    imprimir_linea()

    try:
        registros = listar_reportes_bd(config.ruta_bd)
    except Exception as exc:
        imprimir_mensaje(f"Error al consultar la base de datos: {exc}", "error")
        pausar()
        return

    if not registros:
        imprimir_mensaje("Aun no hay reportes PDF guardados en el historial.", "info")
        pausar()
        return

    encabezado = (
        f"  {'ID':<4} {'FECHA':<19} {'MODO':<7} "
        f"{'FACT.':>5} {'OK':>4} {'IRR.':>4} {'ERR.':>4}"
    )
    print(f"{NEGRITA}{encabezado}{RESET}")
    imprimir_linea()

    for reg in registros:
        id_reg, fecha, modo, total, ok, irr, err, ruta_pdf = reg
        existe_pdf = Path(ruta_pdf).exists()
        marca_archivo = f"{VERDE}disponible{RESET}" if existe_pdf else f"{ROJO}no encontrado{RESET}"
        print(
            f"  {id_reg:<4} {fecha:<19} {modo.upper():<7} "
            f"{total:>5} {ok:>4} {irr:>4} {err:>4}"
        )
        print(f"    {TENUE}↳ {ruta_pdf}{RESET} ({marca_archivo})")

    pausar()


# --- BUCLE PRINCIPAL ---


def main() -> None:
    """Punto de entrada principal del sistema de conciliacion."""
    habilitar_colores_terminal()
    config = Configuracion()
    inicializar_bd(config.ruta_bd)

    while True:
        limpiar_pantalla()
        imprimir_encabezado(config)
        print(f"{NEGRITA}MENU PRINCIPAL{RESET}")
        imprimir_linea()
        print("  1. Ejecutar conciliacion y generar reporte PDF")
        print("  2. Generar lote de datos de prueba")
        print("  3. Configuracion (Modo Prueba / Archivos Reales / IVA)")
        print("  4. Ver historial de reportes PDF")
        print("  0. Salir")
        imprimir_linea()

        try:
            opcion = input("Selecciona una opcion: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            imprimir_mensaje("Sesion finalizada por el usuario.", "info")
            break

        if opcion == "1":
            accion_ejecutar_conciliacion(config)
        elif opcion == "2":
            accion_generar_datos_prueba(config)
        elif opcion == "3":
            accion_configurar(config)
        elif opcion == "4":
            accion_ver_historial(config)
        elif opcion == "0":
            imprimir_mensaje("Hasta luego.", "info")
            break
        else:
            imprimir_mensaje("Opcion no reconocida. Elige un numero del 0 al 4.", "aviso")
            pausar()


if __name__ == "__main__":
    main()