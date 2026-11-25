"""Generación de reportes PDF paginados e historial en SQLite."""

from contextlib import closing
from pathlib import Path
import sqlite3

from fpdf import FPDF

from models import (
    CARPETA_REPORTES,
    RUTA_HISTORIAL,
    TIPOS_HALLAZGO,
    ResultadoConciliacion,
    fmt_monto,
)


def _texto_seguro(texto: object) -> str:
    """Sanitiza texto a Latin-1 para compatibilidad con las fuentes base de fpdf2."""
    return str(texto).encode("latin-1", "replace").decode("latin-1")


def _recortar(pdf: FPDF, texto: object, ancho: float) -> str:
    """Recorta el texto con '...' según el ancho real disponible en la celda."""
    cadena = _texto_seguro(texto)
    if pdf.get_string_width(cadena) <= ancho - 2:
        return cadena
    while cadena and pdf.get_string_width(cadena + "...") > ancho - 2:
        cadena = cadena[:-1]
    return cadena + "..."


class ReportePDF(FPDF):
    """Plantilla A4 horizontal con encabezado y pie de página numerado."""

    def header(self) -> None:
        self.set_text_color(28, 45, 74)
        self.set_font("Helvetica", "B", 14)
        self.cell(
            0,
            8,
            _texto_seguro("Reporte de conciliación de facturas"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.set_draw_color(180, 180, 180)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, _texto_seguro(f"Página {self.page_no()}/{{nb}}"), align="C")


def _seccion(pdf: FPDF, titulo: str) -> None:
    """Agrega un título de sección con espacio superior."""
    pdf.ln(3)
    pdf.set_text_color(28, 45, 74)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, _texto_seguro(titulo), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


def _pares(pdf: FPDF, pares: list[tuple[str, str]]) -> None:
    """Imprime pares etiqueta-valor alineados."""
    for etiqueta, valor in pares:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(48, 6, _texto_seguro(etiqueta))
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, _recortar(pdf, valor, 225), new_x="LMARGIN", new_y="NEXT")


def _tabla(
    pdf: FPDF,
    columnas: list[tuple[str, int, str]],
    filas: list[list],
) -> None:
    """Dibuja una tabla con encabezado repetido automáticamente en saltos de página."""
    def encabezado() -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 235, 242)
        pdf.set_text_color(0, 0, 0)
        for titulo, ancho, alineacion in columnas:
            pdf.cell(ancho, 7, _texto_seguro(titulo), border=1, align=alineacion, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8.5)

    encabezado()
    for fila in filas:
        if pdf.will_page_break(7):
            pdf.add_page()
            encabezado()
        for (_, ancho, alineacion), valor in zip(columnas, fila):
            pdf.cell(ancho, 6.5, _recortar(pdf, valor, ancho), border=1, align=alineacion)
        pdf.ln()


def generar_reporte_pdf(
    resultado: ResultadoConciliacion,
    ruta: Path | None = None,
) -> Path:
    """Genera el reporte PDF completo y devuelve la ruta donde se guardó."""
    if ruta is None:
        nombre = f"reporte_{resultado.modo}_{resultado.inicio:%Y%m%d_%H%M%S}.pdf"
        ruta = CARPETA_REPORTES / nombre

    ruta.parent.mkdir(parents=True, exist_ok=True)
    pdf = ReportePDF(orientation="L", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # 1. Parámetros de ejecución
    _seccion(pdf, "1. Parámetros de ejecución")
    _pares(pdf, [
        ("Fecha", resultado.inicio.strftime("%Y-%m-%d %H:%M:%S")),
        ("Modo", resultado.modo.upper()),
        ("Carpeta de PDFs", resultado.carpeta),
        ("Archivo maestro", resultado.maestro),
        ("Impuesto aplicado", f"{resultado.tasa:.0%}"),
        ("Tolerancia", f"{resultado.tolerancia:.2f}"),
    ])

    # 2. Resumen ejecutivo
    _seccion(pdf, "2. Resumen ejecutivo")
    _pares(pdf, resultado.resumen_pares())

    # 3. Hallazgos
    _seccion(pdf, f"3. Hallazgos ({len(resultado.hallazgos)})")
    if resultado.hallazgos:
        _tabla(
            pdf,
            [
                ("Factura", 34, "L"),
                ("Tipo", 42, "L"),
                ("Extraído", 28, "R"),
                ("Maestro", 28, "R"),
                ("Diferencia", 26, "R"),
                ("Detalle", 119, "L"),
            ],
            [
                [
                    h.no_factura,
                    TIPOS_HALLAZGO[h.tipo][0],
                    fmt_monto(h.monto_extraido),
                    fmt_monto(h.monto_maestro),
                    fmt_monto(h.diferencia),
                    h.detalle,
                ]
                for h in resultado.hallazgos
            ],
        )
    else:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(
            0,
            6,
            _texto_seguro("Sin hallazgos: todas las facturas coinciden con el maestro."),
            new_x="LMARGIN",
            new_y="NEXT",
        )

    # 4. Incidencias de ejecución
    _seccion(pdf, f"4. Incidencias de ejecución ({len(resultado.incidencias)})")
    if resultado.incidencias:
        _tabla(
            pdf,
            [("Nivel", 22, "C"), ("Origen", 60, "L"), ("Detalle", 195, "L")],
            [[i.nivel, i.origen, i.detalle] for i in resultado.incidencias],
        )
    else:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, _texto_seguro("Sin incidencias técnicas."), new_x="LMARGIN", new_y="NEXT")

    # 5. Detalle completo de facturas evaluadas (incluyendo OK)
    _seccion(pdf, f"5. Detalle completo de facturas ({len(resultado.detalle)})")
    if resultado.detalle:
        _tabla(
            pdf,
            [
                ("Factura", 34, "L"),
                ("Extraído", 28, "R"),
                ("Maestro", 28, "R"),
                ("Diferencia", 26, "R"),
                ("Estado", 48, "L"),
                ("Observación", 113, "L"),
            ],
            [
                [
                    f.no_factura,
                    fmt_monto(f.monto_extraido),
                    fmt_monto(f.monto_maestro),
                    fmt_monto(f.diferencia),
                    f.estado,
                    f.detalle,
                ]
                for f in resultado.detalle
            ],
        )

    pdf.output(str(ruta))
    return ruta


def registrar_historial(
    resultado: ResultadoConciliacion,
    ruta_bd: Path = RUTA_HISTORIAL,
) -> int:
    """Registra el reporte PDF generado en SQLite y devuelve el ID insertado."""
    ruta_bd.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(ruta_bd)) as con, con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS reportes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                modo TEXT NOT NULL,
                facturas INTEGER NOT NULL,
                coincidentes INTEGER NOT NULL,
                hallazgos INTEGER NOT NULL,
                incidencias INTEGER NOT NULL,
                ruta_pdf TEXT NOT NULL
            )
            """
        )
        cursor = con.execute(
            """
            INSERT INTO reportes (
                fecha, modo, facturas, coincidentes, hallazgos, incidencias, ruta_pdf
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resultado.inicio.strftime("%Y-%m-%d %H:%M:%S"),
                resultado.modo,
                len(resultado.universo),
                resultado.coincidentes,
                len(resultado.hallazgos),
                len(resultado.incidencias),
                str(resultado.ruta_reporte),
            ),
        )
        return int(cursor.lastrowid)


def consultar_historial(
    limite: int = 15,
    ruta_bd: Path = RUTA_HISTORIAL,
) -> list[tuple]:
    """Devuelve los reportes PDF más recientes almacenados en SQLite."""
    if not ruta_bd.exists():
        return []
    with closing(sqlite3.connect(ruta_bd)) as con:
        try:
            return con.execute(
                """
                SELECT id, fecha, modo, facturas, coincidentes, hallazgos, incidencias, ruta_pdf
                FROM reportes
                ORDER BY id DESC
                LIMIT ?
                """,
                (limite,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
