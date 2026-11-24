from extractor import main as extraer_facturas
import pandas as pd
df = extraer_facturas()

if df is not None:
    print(df.head())