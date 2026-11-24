from pathlib import Path
import pandas as pd
from extractor import main as extraer_facturas

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CSV = RAIZ / "data" / "master_records.csv"


def sumar_por_factura(df_ultimas_dos: pd.DataFrame, tasa: float = 0.12) -> pd.DataFrame:
    """Agrupa por no_factura, suma los valores numéricos, aplica tasa y redondea."""
    col_valor = [col for col in df_ultimas_dos.columns if col != "no_factura"][0]

    df_resumen = (
        df_ultimas_dos
        .groupby("no_factura", as_index=False)[col_valor]
        .sum()
    )
    df_resumen[col_valor] = (df_resumen[col_valor] * (1 + tasa)).round(2)
    return df_resumen


def preparar_para_comparar(df_base: pd.DataFrame, col_factura: str, col_monto: str) -> pd.Series:
    """Normaliza clave y monto y devuelve una Serie indexada por no_factura."""
    serie_factura = df_base[col_factura].astype(str).str.strip()
    serie_monto = pd.to_numeric(df_base[col_monto], errors="coerce").round(2)
    return pd.Series(serie_monto.values, index=serie_factura, name="monto")


def main() -> None:
    df = extraer_facturas()
    mr = pd.read_csv(RUTA_CSV, encoding="utf-8-sig")

    if df is None or mr is None:
        print("No se pudo obtener df o master_records.")
        return

    print(df.info())
    print(mr.info())

    df_ultimas_dos = df.iloc[:, -2:].copy()
    mr_extremos = mr.iloc[:, [0, -1]].copy()

    df_funcional = sumar_por_factura(df_ultimas_dos)
    col_valor_df = [col for col in df_ultimas_dos.columns if col != "no_factura"][0]

    # Ajusta estos nombres si en master_records.csv la primera o última columna se llama distinto
    col_fact_mr = mr_extremos.columns[0]
    col_monto_mr = mr_extremos.columns[1]

    s_df = preparar_para_comparar(df_funcional, "no_factura", col_valor_df)
    s_mr = preparar_para_comparar(mr_extremos, col_fact_mr, col_monto_mr)

    # Alinear índices antes de comparar (align con fill_value evita errores si falta alguna factura)
    s_df_al, s_mr_al = s_df.align(s_mr)
    diferencias = s_df_al.compare(s_mr_al, result_names=("extraido", "master"))

    if diferencias.empty:
        print("Los montos por factura coinciden al 100%.")
    else:
        print("Diferencias encontradas:")
        print(diferencias)


if __name__ == "__main__":
    main()