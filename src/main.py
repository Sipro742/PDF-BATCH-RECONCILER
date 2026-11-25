"""
Punto de entrada CLI e interfaz interactiva con Rich para el conciliador de facturas.

Uso:
    python src/main.py                           # menú interactivo
    python src/main.py --ejecutar                # conciliación directa (sin menú)
    python src/main.py --ejecutar --modo real    # fuerza modo real
    python src/main.py --ejecutar --no-abrir     # no abre el PDF al terminar

Códigos de salida:
    0 = conciliación sin hallazgos
    1 = conciliación con hallazgos
    2 = error de ejecución
"""

import argparse
import functools
import logging
import os
from pathlib import Path
import subprocess
import sys

try:
    from rich import box
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
    from rich.table import Table
except ImportError:
    sys.exit("Falta la librería 'rich'. Instálala con: pip install rich")

from extractor import listar_pdfs
from generate_mock_data import ESCENARIOS_PRUEBA, generar_datos_prueba
from models import (
    CARPETA_INVOICES_PRUEBA,
    RUTA_LOG,
    TIPOS_HALLAZGO,
    Configuracion,
    ErrorDeProceso,
    Incidencia,
    ResultadoConciliacion,
    fmt_monto,
)
from reconciler import ejecutar_conciliacion, validar_entradas
from reporter import consultar_historial, generar_reporte_pdf, registrar_historial

console = Console()
LOG = logging.getLogger("conciliador")


def configurar_logging() -> None:
    """Configura el log técnico en archivo para guardar tracebacks completos."""
    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=RUTA_LOG,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )


def limpiar_ruta(texto: str) -> Path:
    """Quita comillas comunes al arrastrar carpetas o archivos a la terminal."""
    return Path(texto.strip().strip("\"'")).expanduser()


def abrir_archivo(ruta: Path) -> None:
    """Abre un archivo con la aplicación predeterminada del sistema."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(ruta)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(ruta)], check=False)
        else:
            subprocess.run(["xdg-open", str(ruta)], check=False)
    except Exception as exc:
        LOG.warning("No se pudo abrir %s: %s", ruta, exc)
        console.print(f"[yellow]No se pudo abrir automáticamente:[/yellow] {escape(str(ruta))}")


def proteger(funcion):
    """Decorador para capturar errores sin tumbar el menú interactivo."""
    @functools.wraps(funcion)
    def envoltura(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        except ErrorDeProceso as exc:
            LOG.error("Error de proceso: %s", exc)
            console.print(Panel(escape(str(exc)), title="No se pudo completar", border_style="red"))
        except KeyboardInterrupt:
            console.print("\n[yellow]Operación cancelada.[/yellow]")
        except Exception as exc:
            LOG.exception("Error inesperado")
            console.print(Panel(
                f"{type(exc).__name__}: {escape(str(exc))}\n\n"
                f"[dim]Detalle técnico en: {escape(str(RUTA_LOG))}[/dim]",
                title="Error inesperado",
                border_style="red",
            ))
        return None

    return envoltura


# --- COMPONENTES DE INTERFAZ ---


def pausar() -> None:
    """Espera Enter del usuario antes de regresar al menú."""
    Prompt.ask(
        "\n[dim]Enter para volver al menú[/dim]",
        default="",
        show_default=False,
        console=console,
    )


def mostrar_cabecera(config: Configuracion) -> None:
    """Muestra el panel superior con modo activo, rutas y parámetros."""
    etiqueta = (
        "[bold black on yellow] PRUEBA [/]"
        if config.es_prueba
        else "[bold black on green] REAL [/]"
    )
    carpeta, maestro = config.carpeta_facturas, config.csv_maestro

    def estado(existe: bool) -> str:
        return "[green]✔[/green]" if existe else "[red]✘ no existe[/red]"

    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim")
    info.add_column()
    info.add_row("Modo", etiqueta)
    info.add_row("Facturas", f"{escape(str(carpeta))}  {estado(carpeta.is_dir())}")
    info.add_row("Maestro", f"{escape(str(maestro))}  {estado(maestro.is_file())}")
    info.add_row(
        "Impuesto",
        f"{config.tasa_impuesto:.0%}   [dim]tolerancia[/dim] {config.tolerancia:.2f}",
    )
    console.print(Panel(
        info,
        title="[bold cyan]CONCILIADOR DE FACTURAS[/bold cyan]",
        subtitle="[dim]PDF vs. registro maestro[/dim]",
        box=box.ROUNDED,
        border_style="cyan",
    ))


def mostrar_menu() -> None:
    """Renderiza el menú principal."""
    menu = Table.grid(padding=(0, 2))
    menu.add_column(style="bold cyan", justify="right")
    menu.add_column()
    menu.add_row("1", "Ejecutar conciliación")
    menu.add_row("2", "Generar datos de prueba")
    menu.add_row("3", "Historial de reportes")
    menu.add_row("4", "Configuración")
    menu.add_row("0", "Salir")
    console.print(menu)
    console.print()


def mostrar_resultado(r: ResultadoConciliacion) -> None:
    """Muestra el resumen, la tabla de hallazgos y las incidencias técnicas."""
    if r.errores:
        color, titulo = "red", "Conciliación terminada con errores de ejecución"
    elif r.hallazgos or r.incidencias:
        color, titulo = "yellow", "Conciliación terminada con irregularidades"
    else:
        color, titulo = "green", "Conciliación sin diferencias"

    resumen = Table.grid(padding=(0, 2))
    resumen.add_column(style="dim")
    resumen.add_column()
    for etiqueta, valor in r.resumen_pares():
        resumen.add_row(etiqueta, valor)
    if r.ruta_reporte:
        resumen.add_row("Reporte PDF", escape(str(r.ruta_reporte)))
    console.print(Panel(
        resumen,
        title=f"[bold {color}]{titulo}[/bold {color}]",
        border_style=color,
        box=box.ROUNDED,
    ))

    if r.hallazgos:
        tabla = Table(title="Hallazgos", box=box.SIMPLE_HEAVY, header_style="bold")
        tabla.add_column("Factura", no_wrap=True)
        tabla.add_column("Tipo")
        tabla.add_column("Extraído", justify="right")
        tabla.add_column("Maestro", justify="right")
        tabla.add_column("Diferencia", justify="right")
        tabla.add_column("Detalle", overflow="fold")
        for h in r.hallazgos:
            etiqueta, tono = TIPOS_HALLAZGO[h.tipo]
            tabla.add_row(
                escape(h.no_factura),
                f"[{tono}]{etiqueta}[/{tono}]",
                fmt_monto(h.monto_extraido),
                fmt_monto(h.monto_maestro),
                fmt_monto(h.diferencia),
                escape(h.detalle),
            )
        console.print(tabla)

    if r.incidencias:
        tabla = Table(title="Incidencias de ejecución", box=box.SIMPLE_HEAVY, header_style="bold")
        tabla.add_column("Nivel")
        tabla.add_column("Origen", no_wrap=True)
        tabla.add_column("Detalle", overflow="fold")
        for i in r.incidencias:
            tono = "red" if i.nivel == "ERROR" else "yellow"
            tabla.add_row(f"[{tono}]{i.nivel}[/{tono}]", escape(i.origen), escape(i.detalle))
        console.print(tabla)


def verificar_escenarios(r: ResultadoConciliacion) -> None:
    """Confirma en modo prueba que se detectaron los escenarios definidos en el generador."""
    detectados = {(h.no_factura, h.tipo) for h in r.hallazgos}
    esperados = {(f, t) for f, t in ESCENARIOS_PRUEBA.items() if f in r.universo}
    if not esperados:
        return

    lineas = []
    for factura, tipo in sorted(esperados):
        marca = "[green]✔[/green]" if (factura, tipo) in detectados else "[red]✘[/red]"
        lineas.append(f"{marca} {factura}  [dim]{TIPOS_HALLAZGO[tipo][0]}[/dim]")

    inesperados = detectados - esperados
    if inesperados:
        lineas.append(f"[yellow]⚠ {len(inesperados)} hallazgo(s) no previstos[/yellow]")

    aciertos = len(esperados & detectados)
    color = "green" if aciertos == len(esperados) and not inesperados else "yellow"
    console.print(Panel(
        "\n".join(lineas),
        box=box.ROUNDED,
        border_style=color,
        title=f"Autoverificación ({aciertos}/{len(esperados)} escenarios detectados)",
    ))


# --- ACCIONES DEL MENÚ ---


def datos_prueba_existen(config: Configuracion) -> bool:
    """Comprueba si ya existe un lote de prueba generado."""
    carpeta = config.carpeta_facturas
    return carpeta.is_dir() and bool(listar_pdfs(carpeta)) and config.csv_maestro.is_file()


@proteger
def accion_generar() -> None:
    """Genera un lote de datos de prueba en la carpeta aislada de mock."""
    console.print(f"[dim]Destino de prueba:[/dim] {escape(str(CARPETA_INVOICES_PRUEBA))}")
    console.print("[dim]Tus archivos reales en data/invoices y data/master_records.csv no se tocan.[/dim]\n")

    if CARPETA_INVOICES_PRUEBA.is_dir() and listar_pdfs(CARPETA_INVOICES_PRUEBA):
        if not Confirm.ask(
            "[yellow]Ya existen PDFs de prueba.[/yellow] ¿Sobrescribir?",
            default=False,
            console=console,
        ):
            return

    total = IntPrompt.ask("¿Cuántas facturas generar?", default=20, console=console)
    if not 1 <= total <= 500:
        raise ErrorDeProceso("La cantidad debe estar entre 1 y 500.")
    if total < 18:
        console.print("[yellow]Con menos de 18 facturas no se inyectan todos los escenarios.[/yellow]")

    with console.status("[cyan]Generando facturas y maestro de prueba..."):
        resumen = generar_datos_prueba(total)

    LOG.info("Datos de prueba generados: %d PDFs", resumen["pdfs_creados"])
    console.print(
        f"[green]Listo:[/green] {resumen['pdfs_creados']} PDFs y "
        f"{resumen['registros_maestro']} registros en el maestro de prueba."
    )


@proteger
def accion_conciliar(
    config: Configuracion,
    interactivo: bool = True,
) -> ResultadoConciliacion | None:
    """Ejecuta el pipeline de conciliación, guarda PDF + historial y muestra resultados."""
    if interactivo and config.es_prueba and not datos_prueba_existen(config):
        if not Confirm.ask(
            "[yellow]No hay datos de prueba.[/yellow] ¿Generarlos ahora?",
            default=True,
            console=console,
        ):
            return None
        generar_datos_prueba(20)

    total_pdfs = len(validar_entradas(config))
    columnas = (
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    with Progress(*columnas, console=console, transient=True) as progreso:
        tarea = progreso.add_task("Extrayendo facturas", total=total_pdfs)

        def al_avanzar(pdf: Path) -> None:
            progreso.update(tarea, description=f"Procesado {pdf.name}")
            progreso.advance(tarea)

        resultado = ejecutar_conciliacion(config, al_avanzar=al_avanzar)

    try:
        resultado.ruta_reporte = generar_reporte_pdf(resultado)
        registrar_historial(resultado)
    except Exception as exc:
        LOG.exception("Fallo al guardar reporte o historial")
        resultado.incidencias.append(
            Incidencia("ERROR", "reporte", f"No se pudo guardar el reporte/historial: {exc}")
        )

    mostrar_resultado(resultado)
    if config.es_prueba:
        verificar_escenarios(resultado)
    if interactivo and config.abrir_reporte and resultado.ruta_reporte:
        abrir_archivo(resultado.ruta_reporte)
    return resultado


@proteger
def accion_historial() -> None:
    """Muestra el historial de reportes PDF guardados y permite abrir uno por ID."""
    filas = consultar_historial()
    if not filas:
        console.print("[dim]Aún no hay reportes generados.[/dim]")
        return

    tabla = Table(title="Últimos reportes", box=box.SIMPLE_HEAVY, header_style="bold")
    for nombre, justificar in [
        ("ID", "right"),
        ("Fecha", "left"),
        ("Modo", "left"),
        ("Facturas", "right"),
        ("OK", "right"),
        ("Hallazgos", "right"),
        ("Incid.", "right"),
    ]:
        tabla.add_column(nombre, justify=justificar)

    for id_, fecha, modo, facturas, ok, hallazgos, incidencias, _ in filas:
        tabla.add_row(
            str(id_),
            fecha,
            modo,
            str(facturas),
            f"[green]{ok}[/green]",
            f"[yellow]{hallazgos}[/yellow]" if hallazgos else "0",
            str(incidencias),
        )
    console.print(tabla)

    seleccion = Prompt.ask(
        "ID del reporte a abrir [dim](Enter para volver)[/dim]",
        default="",
        show_default=False,
        console=console,
    )
    if not seleccion.strip().isdigit():
        return
    ruta = next((Path(f[7]) for f in filas if f[0] == int(seleccion)), None)
    if ruta is None:
        console.print("[yellow]Ese ID no está en la lista.[/yellow]")
    elif not ruta.exists():
        console.print(f"[red]El archivo ya no existe:[/red] {escape(str(ruta))}")
    else:
        abrir_archivo(ruta)


@proteger
def accion_configuracion(config: Configuracion) -> None:
    """Menú interactivo para modificar y guardar la configuración en config.json."""
    while True:
        console.clear()
        mostrar_cabecera(config)
        opciones = Table.grid(padding=(0, 2))
        opciones.add_column(style="bold cyan", justify="right")
        opciones.add_column()
        opciones.add_row("1", "Cambiar modo (prueba / real)")
        opciones.add_row("2", "Carpeta de facturas reales")
        opciones.add_row("3", "Archivo maestro real (.csv)")
        opciones.add_row("4", "Tasa de impuesto")
        opciones.add_row("5", "Tolerancia de diferencia")
        opciones.add_row(
            "6",
            f"Abrir PDF al terminar: {'[green]sí[/green]' if config.abrir_reporte else '[red]no[/red]'}",
        )
        opciones.add_row(
            "7",
            f"Exportar CSV intermedio (salidadf.csv): "
            f"{'[green]sí[/green]' if config.guardar_csv_extraido else '[red]no[/red]'}",
        )
        opciones.add_row("0", "Volver")
        console.print(opciones)
        console.print()

        opcion = Prompt.ask(
            "Opción",
            choices=["1", "2", "3", "4", "5", "6", "7", "0"],
            default="0",
            console=console,
        )
        if opcion == "0":
            return
        if opcion == "1":
            config.modo = Prompt.ask(
                "Modo",
                choices=["prueba", "real"],
                default=config.modo,
                console=console,
            )
        elif opcion in ("2", "3"):
            actual = config.carpeta_real if opcion == "2" else config.maestro_real
            ruta = limpiar_ruta(Prompt.ask("Ruta", default=actual, console=console))
            existe = ruta.is_dir() if opcion == "2" else ruta.is_file()
            if not existe:
                console.print("[yellow]Aviso: esa ruta aún no existe (se guardará igualmente).[/yellow]")
            if opcion == "2":
                config.carpeta_real = str(ruta)
            else:
                config.maestro_real = str(ruta)
        elif opcion == "4":
            porcentaje = FloatPrompt.ask(
                "Impuesto en %",
                default=config.tasa_impuesto * 100,
                console=console,
            )
            if 0 <= porcentaje <= 100:
                config.tasa_impuesto = round(porcentaje / 100, 4)
            else:
                console.print("[red]Debe estar entre 0 y 100.[/red]")
        elif opcion == "5":
            tolerancia = FloatPrompt.ask(
                "Tolerancia (unidades monetarias)",
                default=config.tolerancia,
                console=console,
            )
            if tolerancia >= 0:
                config.tolerancia = tolerancia
            else:
                console.print("[red]No puede ser negativa.[/red]")
        elif opcion == "6":
            config.abrir_reporte = not config.abrir_reporte
        elif opcion == "7":
            config.guardar_csv_extraido = not config.guardar_csv_extraido

        config.guardar()


def menu_principal(config: Configuracion) -> None:
    """Bucle principal del menú interactivo."""
    while True:
        console.clear()
        mostrar_cabecera(config)
        mostrar_menu()
        opcion = Prompt.ask(
            "Selecciona una opción",
            choices=["1", "2", "3", "4", "0"],
            default="1",
            console=console,
        )

        if opcion == "0":
            console.print("[dim]Hasta luego.[/dim]")
            return
        if opcion == "4":
            accion_configuracion(config)
            continue

        console.print()
        if opcion == "1":
            accion_conciliar(config)
        elif opcion == "2":
            accion_generar()
        elif opcion == "3":
            accion_historial()
        pausar()


# --- CLI Y PUNTO DE ENTRADA ---


def leer_argumentos() -> argparse.Namespace:
    """Define y parsea los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Conciliador de facturas PDF vs. registro maestro."
    )
    parser.add_argument(
        "--ejecutar",
        action="store_true",
        help="Corre la conciliación directamente sin abrir el menú.",
    )
    parser.add_argument(
        "--modo",
        choices=("prueba", "real"),
        help="Fuerza el modo de operación para esta ejecución.",
    )
    parser.add_argument(
        "--no-abrir",
        action="store_true",
        help="No abre el reporte PDF al finalizar.",
    )
    return parser.parse_args()


def codigo_salida(resultado: ResultadoConciliacion | None) -> int:
    """Traduce el resultado a código de salida estándar (0=OK, 1=hallazgos, 2=error)."""
    if resultado is None or resultado.errores:
        return 2
    return 1 if resultado.hallazgos else 0


def main() -> int:
    """Punto de entrada principal del programa."""
    args = leer_argumentos()
    configurar_logging()
    config = Configuracion.cargar()
    if args.modo:
        config.modo = args.modo
    if args.no_abrir:
        config.abrir_reporte = False

    LOG.info("Inicio | modo=%s | ejecutar=%s", config.modo, args.ejecutar)

    if args.ejecutar:
        return codigo_salida(accion_conciliar(config, interactivo=False))

    try:
        menu_principal(config)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Hasta luego.[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
