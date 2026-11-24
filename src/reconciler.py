from extractor import main as extraer_facturas
from pathlib import Path
import pandas as pd
raiz = Path(__file__).resolve().parent.parent
ruta_csv = raiz/"data"/"master_records.csv"
df = extraer_facturas()
mr = pd.read_csv(ruta_csv,encoding="utf-8-sig")
if df is not None and mr is not None:
    print(df.head())
    print(mr.head())