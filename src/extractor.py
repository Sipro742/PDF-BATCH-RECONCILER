import pdfplumber
import csv
from pathlib import Path
import csv
def main():
    aqui = Path(__file__).resolve().parent
    ruta_Carp = aqui/data/invoices
    errores = []
    listado_de_pdf = []
    tablas_extraidas = []
    if ruta_Carp is none:
        errores.append("La carpeta se encuentra vacia") 
        return 2
    listado_de_pdf = sorted(ruta_Carp.glob("*.pdf"))
    if listado_de_pdf is none:
        errores.append("La carpeta no contiene PDF")
    for pdf in listado_de_pdf:
        try:
            with pdfplumber.open(pdf) as doc:
                tablas_pdf = [
                    tabla
                    for pagina in doc.pages
                    for tabla in pagina.extract_tables()
                ]

            if not tablas_pdf:
                errores.append(f"No se extrajeron tablas en {pdf.name}")
                continue
            tablas_extraidas.extend(tablas_pdf)
        except Exception as e:
            errores.append(str(e))  
 

with open("salida.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    for tabla in tablas_extraidas:
        writer.writerows(tabla)

         

            


    