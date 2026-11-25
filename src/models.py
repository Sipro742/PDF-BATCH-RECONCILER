"""Modelos de datos, constantes de rutas y configuración del conciliador."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
from pathlib import Path

import pandas as pd

# Rutas base del proyecto (src/ -> raíz del proyecto)
RAIZ = Path(__file__).resolve().parent.parent
CARPETA_DATA = RAIZ / "data"

# Rutas por defecto en modo real (alineadas con el proyecto existente)
CARPETA_INVOICES_REAL = CARPETA_DATA / "invoices"
RUTA_CSV_REAL = CARPETA_DATA / "master_records.csv"

# Rutas aisladas en modo prueba (evitan sobrescribir datos reales)
CARPETA_MOCK = CARPETA_DATA / "mock"
CARPETA_INVOICES_PRUEBA = CARPETA_MOCK / "invoices"
RUTA_CSV_PRUEBA = CARPETA_MOCK / "registro_maestro.csv"

# Rutas de salida, reportes, historial y configuración
CARPETA_SALIDA = CARPETA_DATA / "output"
CARPETA_REPORTES = CARPETA_DATA / "reports"
RUTA_HISTORIAL = CARPETA_REPORTES / "historial.db"
RUTA_CONFIG = RAIZ / "config.json"
RUTA_LOG = RAIZ / "logs" / "conciliador.log"

# Fuente única de verdad: tipo de hallazgo -> (etiqueta legible, color en terminal)
TIPOS_HALLAZGO: dict[str, tuple[str, str]] = {
    "DIFERENCIA_MONTO": ("Diferencia de monto", "yellow"),
    "FALTA_PDF": ("Falta PDF", "red"),
    "FALTA_MAESTRO": ("Falta en maestro", "magenta"),
    "PDF_DUPLICADO": ("PDF duplicado", "cyan"),
    "MONTO_INVALIDO": ("Monto inválido", "red"),
}

LOG = logging.getLogger("conciliador")


class ErrorDeProceso(Exception):
    """Error fatal con mensaje pensado para mostrarse tal cual al usuario."""


def fmt_monto(valor: float | None) -> str:
    """Formatea un monto numérico o devuelve '-' si no aplica."""
    if valor is None or pd.isna(valor):
        return "-"
    return f"{valor:,.2f}"


@dataclass
class Configuracion:
    """Configuración persistente del conciliador."""

    modo: str = "prueba"  # "prueba" | "real"
    carpeta_real: str = str(CARPETA_INVOICES_REAL)
    maestro_real: str = str(RUTA_CSV_REAL)
    tasa_impuesto: float = 0.12
    tolerancia: float = 0.01
    abrir_reporte: bool = True
    guardar_csv_extraido: bool = True

    @property
    def es_prueba(self) -> bool:
        """Indica si el programa opera en modo prueba."""
        return self.modo == "prueba"

    @property
    def carpeta_facturas(self) -> Path:
        """Devuelve la carpeta activa de facturas PDF según el modo."""
        return CARPETA_INVOICES_PRUEBA if self.es_prueba else Path(self.carpeta_real)

    @property
    def csv_maestro(self) -> Path:
        """Devuelve la ruta activa del CSV maestro según el modo."""
        return RUTA_CSV_PRUEBA if self.es_prueba else Path(self.maestro_real)

    @classmethod
    def cargar(cls, ruta: Path = RUTA_CONFIG) -> "Configuracion":
        """Lee config.json; si no existe o está dañado, devuelve los valores por defecto."""
        if not ruta.exists():
            return cls()
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            validos = {k: v for k, v in datos.items() if k in cls.__dataclass_fields__}
            return cls(**validos)
        except (json.JSONDecodeError, TypeError, OSError) as exc:
            LOG.warning("config.json inválido, se usan valores por defecto: %s", exc)
            return cls()

    def guardar(self, ruta: Path = RUTA_CONFIG) -> None:
        """Persiste la configuración actual en formato JSON."""
        ruta.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


@dataclass
class Incidencia:
    """Irregularidad técnica o de ejecución (archivo ilegible, dato malformado, etc.)."""

    nivel: str  # "ERROR" | "AVISO"
    origen: str
    detalle: str


@dataclass
class Hallazgo:
    """Irregularidad de negocio encontrada al conciliar PDFs contra el CSV maestro."""

    no_factura: str
    tipo: str
    monto_extraido: float | None
    monto_maestro: float | None
    detalle: str

    @property
    def diferencia(self) -> float | None:
        """Calcula la diferencia (extraído - maestro) cuando ambos montos existen."""
        if self.monto_extraido is None or self.monto_maestro is None:
            return None
        if pd.isna(self.monto_extraido) or pd.isna(self.monto_maestro):
            return None
        return round(float(self.monto_extraido) - float(self.monto_maestro), 2)


@dataclass
class FilaDetalle:
    """Resultado consolidado por factura evaluada (incluyendo las OK)."""

    no_factura: str
    monto_extraido: float | None
    monto_maestro: float | None
    diferencia: float | None
    estado: str
    detalle: str


@dataclass
class ResultadoConciliacion:
    """Resultado completo de una ejecución de conciliación."""

    inicio: datetime
    modo: str
    carpeta: str
    maestro: str
    tasa: float
    tolerancia: float
    pdfs_leidos: int = 0
    facturas_pdf: int = 0
    registros_maestro: int = 0
    coincidentes: int = 0
    universo: set[str] = field(default_factory=set)
    detalle: list[FilaDetalle] = field(default_factory=list)
    hallazgos: list[Hallazgo] = field(default_factory=list)
    incidencias: list[Incidencia] = field(default_factory=list)
    ruta_reporte: Path | None = None

    @property
    def errores(self) -> int:
        """Cantidad de incidencias con nivel ERROR."""
        return sum(1 for i in self.incidencias if i.nivel == "ERROR")

    @property
    def facturas_con_hallazgos(self) -> int:
        """Cantidad de facturas únicas con al menos un hallazgo."""
        return len({h.no_factura for h in self.hallazgos})

    def resumen_pares(self) -> list[tuple[str, str]]:
        """Pares clave-valor compartidos entre terminal y reporte PDF."""
        return [
            ("PDF leídos", str(self.pdfs_leidos)),
            ("Facturas en PDF", str(self.facturas_pdf)),
            ("Registros en maestro", str(self.registros_maestro)),
            ("Facturas que coinciden", str(self.coincidentes)),
            ("Facturas con hallazgos", str(self.facturas_con_hallazgos)),
            ("Incidencias de ejecución", f"{len(self.incidencias)} ({self.errores} errores)"),
        ]
