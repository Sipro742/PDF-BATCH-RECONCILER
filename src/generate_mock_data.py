import csv
import random
from datetime import date, timedelta
from pathlib import Path
from fpdf import FPDF

# Rutas de salida
DIRECTORIO_BASE = Path(__file__).resolve().parent.parent
CARPETA_FACTURAS = DIRECTORIO_BASE / "data" / "invoices"
RUTA_CSV_MAESTRO = DIRECTORIO_BASE / "data" / "registro_maestro.csv"

PROVEEDORES = [
    ("TechSupply Corp S.A.", "1234567-8"),
    ("CloudHost Solutions Ltd.", "8765432-1"),
    ("DataLogistics Global S.A.", "4561239-0"),
    ("NovaSoft Services Inc.", "9988776-5"),
    ("Apex Office Supplies", "3344556-2"),
]

SERVICIOS = [
    "Licencia de Software Anual",
    "Soporte Tecnico Mensual",
    "Alojamiento en la Nube (Servidor)",
    "Consultoria de Automatizacion",
    "Mantenimiento Preventivo de Red",
    "Suscripcion API Empresarial",
]


def crear_pdf_factura(ruta_archivo: Path, datos_factura: dict) -> None:
    """Genera una factura comercial en PDF con fpdf2."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Encabezado
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "FACTURA COMERCIAL", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # Datos del proveedor y factura
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Proveedor: {datos_factura['proveedor']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"NIT: {datos_factura['nit']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"No. Factura: {datos_factura['id_factura']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Fecha: {datos_factura['fecha']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Encabezado de tabla
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(95, 8, "Descripcion", border=1, fill=True)
    pdf.cell(25, 8, "Cant.", border=1, align="C", fill=True)
    pdf.cell(35, 8, "Precio Unit.", border=1, align="R", fill=True)
    pdf.cell(35, 8, "Importe", border=1, align="R", fill=True, new_x="LMARGIN", new_y="NEXT")

    # Filas de conceptos
    pdf.set_font("Helvetica", "", 10)
    for concepto in datos_factura["conceptos"]:
        pdf.cell(95, 8, concepto["descripcion"], border=1)
        pdf.cell(25, 8, str(concepto["cantidad"]), border=1, align="C")
        pdf.cell(35, 8, f"${concepto['precio_unitario']:,.2f}", border=1, align="R")
        pdf.cell(35, 8, f"${concepto['importe']:,.2f}", border=1, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)

    # Totales
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(155, 7, "Subtotal:", align="R")
    pdf.cell(35, 7, f"${datos_factura['subtotal']:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.cell(155, 7, "Impuestos (12%):", align="R")
    pdf.cell(35, 7, f"${datos_factura['impuesto']:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(155, 9, "TOTAL A PAGAR:", align="R")
    pdf.cell(35, 9, f"${datos_factura['total']:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(ruta_archivo))


def generar_datos_prueba(total_registros: int = 20) -> None:
    """Crea el lote de PDFs y el CSV maestro con errores intencionales."""
    CARPETA_FACTURAS.mkdir(parents=True, exist_ok=True)
    random.seed(42)  # Semilla fija para generar siempre los mismos datos

    filas_maestro = []
    fecha_inicio = date(2026, 11, 1)

    for numero in range(1, total_registros + 1):
        id_factura = f"INV-2026-{numero:03d}"
        fecha_factura = (fecha_inicio + timedelta(days=numero)).isoformat()
        proveedor, nit = random.choice(PROVEEDORES)

        # Generar entre 1 y 3 conceptos por factura
        conceptos = []
        for _ in range(random.randint(1, 3)):
            cantidad = random.randint(1, 5)
            precio_unitario = round(random.uniform(50.0, 400.0), 2)
            importe = round(cantidad * precio_unitario, 2)
            conceptos.append({
                "descripcion": random.choice(SERVICIOS),
                "cantidad": cantidad,
                "precio_unitario": precio_unitario,
                "importe": importe,
            })

        subtotal = round(sum(c["importe"] for c in conceptos), 2)
        impuesto = round(subtotal * 0.12, 2)
        total = round(subtotal + impuesto, 2)

        datos_factura = {
            "id_factura": id_factura,
            "fecha": fecha_factura,
            "proveedor": proveedor,
            "nit": nit,
            "conceptos": conceptos,
            "subtotal": subtotal,
            "impuesto": impuesto,
            "total": total,
        }

        # --- INYECCION DE ESCENARIOS DE PRUEBA ---
        # 1. Facturas 5 y 12: Diferencia de monto entre PDF y CSV maestro
        if numero in (5, 12):
            crear_pdf_factura(CARPETA_FACTURAS / f"{id_factura}.pdf", datos_factura)
            total_alterado = round(total + 45.50, 2)
            filas_maestro.append([id_factura, fecha_factura, proveedor, total_alterado])

        # 2. Factura 8: Existe en el CSV maestro, pero FALTA el archivo PDF
        elif numero == 8:
            filas_maestro.append([id_factura, fecha_factura, proveedor, total])

        # 3. Factura 15: Existe el archivo PDF, pero FALTA en el CSV maestro
        elif numero == 15:
            crear_pdf_factura(CARPETA_FACTURAS / f"{id_factura}.pdf", datos_factura)

        # 4. Factura 18: PDF DUPLICADO (dos archivos distintos con el mismo ID)
        elif numero == 18:
            crear_pdf_factura(CARPETA_FACTURAS / f"{id_factura}.pdf", datos_factura)
            crear_pdf_factura(CARPETA_FACTURAS / f"{id_factura}_copia.pdf", datos_factura)
            filas_maestro.append([id_factura, fecha_factura, proveedor, total])

        # 5. Resto de facturas: Conciliacion OK
        else:
            crear_pdf_factura(CARPETA_FACTURAS / f"{id_factura}.pdf", datos_factura)
            filas_maestro.append([id_factura, fecha_factura, proveedor, total])

    # Guardar archivo maestro CSV
    with open(RUTA_CSV_MAESTRO, mode="w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(["id_factura", "fecha", "proveedor", "total_esperado"])
        escritor.writerows(filas_maestro)

    print(f"PDFs generados en: {CARPETA_FACTURAS}")
    print(f"CSV maestro generado en: {RUTA_CSV_MAESTRO}")


if __name__ == "__main__":
    generar_datos_prueba()