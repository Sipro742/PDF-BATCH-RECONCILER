# PDF-BATCH-RECONCILER
Python automation engine that extracts data from multiple PDF invoices in batch and reconciles them against master records using Pandas to detect discrepancies.
PDF-BATCH-RECONCILER/
├── src/
│   ├── generate_mock_data.py   # Crea PDFs y CSV de prueba
│   ├── extractor.py            # Lee PDFs en lote
│   ├── reconciler.py           # Cruza datos con Pandas
│   └── main.py                 # Orquestador principal
├── data/                       # Ignorado en .gitignore (salvo ejemplos)
├── requirements.txt
└── README.md