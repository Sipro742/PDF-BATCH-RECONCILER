from extractor import main as extraer_facturas
from pathlib import Path
import pandas as pd
raiz = Path(__file__).resolve().parent.parent
ruta_csv = raiz/"data"/"master_records.csv"
df = extraer_facturas()
mr = pd.read_csv(ruta_csv,encoding="utf-8-sig")
def sumar_por_factura(df_ultimas_dos: pd.DataFrame, tasa: float = 0.12) -> pd.DataFrame:
    """Agrupa por no_factura y suma los valores numéricos en un nuevo DataFrame."""
    col_valor = [col for col in df_ultimas_dos.columns if col != "no_factura"][0]

    df_resumen = (
        df_ultimas_dos
        .groupby("no_factura", as_index=False)[col_valor]
        .sum()
    )
    df_resumen[col_valor] =(df_resumen[col_valor] * (1 + tasa)).round(2)
    return df_resumen
def main ():
    if df is not None and mr is not None:
        print(df.info())
        print(mr.info())
    df_ultimas_dos = df.iloc[:, -2:].copy()
    mr_funcional = mr.iloc[:, [0, -1]].copy()

    df_funcional = sumar_por_factura(df_ultimas_dos)
    print(df_funcional)
    print(mr_funcional)

if __name__ == "__main__":
    main()