"""Utilidades de exportación a PDF (reportlab)."""
import io
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape as _escape_xml

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Paleta de marca
BRAND = colors.HexColor("#1F4E78")
BRAND_LIGHT = colors.HexColor("#F2F6FA")
GOLD = colors.HexColor("#E39B1B")
GREY = colors.HexColor("#666666")
LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "lactis-logo.png"


def _texto(valor: Any) -> str:
    """Escapa el texto libre antes de meterlo en un Paragraph: ReportLab
    interpreta mini-XML y un '<' del usuario borraría texto o reventaría el
    documento.

    Sin esto, un cliente llamado "Depósito <El Bueno> & Hnos" se imprimía como
    "Depósito & Hnos" (pérdida silenciosa) y uno llamado "Ana <onDraw name='x'/>"
    dejaba el PDF caído con un 500 permanente.
    """
    return _escape_xml(str(valor or ""), {'"': "&quot;"})


def _cell_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return value


DOS_DECIMALES = Decimal("0.01")
ENTERO = Decimal("1")
# Ninguna columna del sistema pasa de doce dígitos enteros; por encima de cuarenta
# no hay nada sensato que redondear y estirar la precisión sería regalarle memoria a
# una cifra absurda. Ver _medio_arriba.
MAX_DIGITOS_ENTEROS = 40


def _medio_arriba(valor: Any, exponente: Decimal) -> Decimal:
    """Redondea con el MEDIO PARA ARRIBA de todo el proyecto, no con el de Python.

    ES LA REGLA DE LA CASA: 0,005 SUBE (-> 0,01). Los schemas de entrada redondean
    así (`app/common/schemas.py::a_dos_decimales`) y los servicios calculan la plata
    así, pero los formateadores de este archivo se estaban quedando con el redondeo
    POR OMISIÓN de Python, que es el del banquero (ROUND_HALF_EVEN: el medio va al
    dígito par). Y eso hacía que el PAPEL dijera una cifra distinta de la pantalla:

      · `pesos(2.505)` imprimía $2,50 cuando la regla escrita en el propio proyecto
        dice que tiene que dar $2,51;
      · `pesos(1800.005)` imprimía $1.800,00 cuando la columna guarda $1.800,01, o
        sea que el comprobante contradecía a la base de datos por un centavo.

    El PDF es el papel que se le entrega a un tercero —al productor, al conductor,
    al cliente— y que después se compara contra la pantalla. Si dicen cifras
    distintas, la discusión la pierde el dueño.

    NO LEVANTA NUNCA, y es a propósito: `quantize` se rinde con InvalidOperation
    cuando el resultado no cabe en la precisión del contexto, y acá eso sería un 500
    al descargar el documento. La precisión va holgada y acotada, y una cifra tan
    grande que no cabe en ninguna columna se imprime tal como venga.
    """
    numero = Decimal(valor or 0)
    if not numero.is_finite() or numero.adjusted() >= MAX_DIGITOS_ENTEROS:
        return numero
    with localcontext() as ctx:
        ctx.prec = MAX_DIGITOS_ENTEROS + 3
        return numero.quantize(exponente, rounding=ROUND_HALF_UP)


def _miles(valor: Any, decimales: int) -> str:
    """Número con separador de miles PUNTO y decimal COMA (estilo colombiano).

    El formato de Python (`:,.0f`) separa los miles con coma, que en Colombia es
    justo el separador decimal: $18,525,000 se lee mal. Se voltean los dos
    separadores usando un marcador temporal para no pisar el trabajo hecho.

    El número se redondea antes de formatear (ver `_medio_arriba`): el `:,.Nf` de
    Python redondea con el del banquero, y era por acá por donde ese redondeo se
    metía en los cuatro formateadores.
    """
    numero = _medio_arriba(valor, Decimal(1).scaleb(-decimales))
    texto = f"{numero:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _decimales_utiles(numero: Decimal, maximo: int) -> int:
    """Cuántos decimales hacen falta para no perder información ni imprimir ceros
    de relleno: 100,00 -> 0 decimales; 10,30 -> 1; 10,34 -> 2.

    Es la clave de que las columnas del documento SUMEN: si se imprime menos
    precisión de la que guarda la base, las filas dejan de dar el total.
    """
    escalado = numero
    for usados in range(maximo + 1):
        if escalado == escalado.to_integral_value():
            return usados
        escalado *= 10
    return maximo


def pesos(valor: Any) -> str:
    """Pesos colombianos: $18.525.000, y con centavos SOLO cuando existen.

    El caso normal en pesos es sin centavos, pero descartarlos hacía que el
    cliente no pudiera reproducir el total de su propia fila: 100 kg a
    $19.500,50 son $1.950.050, no $1.950.000.
    """
    # Se redondea PRIMERO y se decide después si hay centavos. Al revés —que era
    # como estaba— la decisión se tomaba sobre la cifra cruda: $0,999 contaba como
    # "tiene centavos" y salía "$1,00" en vez de "$1". Y el redondeo es el medio para
    # arriba de la casa, no el del banquero: ver `_medio_arriba`.
    numero = _medio_arriba(valor, DOS_DECIMALES)
    signo = "-" if numero < 0 else ""
    absoluto = abs(numero)
    # Los centavos son 0 o 2 dígitos, nunca 1: en plata "$19.500,5" se lee como si
    # se hubiera perdido un centavo.
    decimales = 0 if absoluto == absoluto.to_integral_value() else 2
    return f"{signo}${_miles(absoluto, decimales)}"


def kilogramos(valor: Any) -> str:
    """Kilos con la misma precisión que guarda la base (hasta 2 decimales) y sin
    ceros a la derecha: 100 kg, 10,34 kg, 1.234,5 kg.

    Antes se imprimía un solo decimal y la columna no sumaba: cinco ventas de
    10,34 kg salían como cinco "10,3 kg" (51,5) contra un total de 51,7 kg.
    """
    numero = _medio_arriba(valor, DOS_DECIMALES)
    return f"{_miles(numero, _decimales_utiles(numero, 2))} kg"


def barras(valor: Any) -> str:
    """Barras de mozzarella: "8 barras", "1 barra", y SIN decimales nunca.

    Nunca dice "kg", y eso es el punto de que exista esta función en vez de
    reutilizar `kilogramos`: un documento que le diga "8 kg" a alguien que recibió
    8 barras está mintiendo sobre lo que se despachó, y el cliente o el productor
    no reconocería su propia entrega al cuadrar a mano.

    Sin decimales porque una barra es una barra: la columna es Numeric(12,0) y el
    esquema de entrada rechaza "8,5 barras". Imprimir "8,0 barras" haría pensar que
    puede haber medias.

    Se pluraliza porque el documento lo lee una persona: "1 barras" se ve como un
    error del sistema y le quita confianza a todo lo demás que diga la hoja.
    """
    numero = _medio_arriba(valor, ENTERO)
    unidad = "barra" if abs(numero) == 1 else "barras"
    return f"{_miles(numero, 0)} {unidad}"


def build_pdf(
    *,
    title: str,
    subtitle: str = "",
    sections: Sequence[dict[str, Any]],
) -> bytes:
    """Genera un PDF con secciones de tablas.

    Cada sección: {"heading": str, "headers": [...], "rows": [[...]], "col_widths": [...] opcional}
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleQ", parent=styles["Title"], fontSize=15, spaceAfter=4)
    elements: list[Any] = [Paragraph(title, title_style)]
    if subtitle:
        elements.append(Paragraph(subtitle, styles["Normal"]))
    elements.append(Spacer(1, 10))

    for section in sections:
        if section.get("heading"):
            elements.append(Paragraph(section["heading"], styles["Heading3"]))
        data = [list(section["headers"])] + [
            [str(_cell_value(v)) if v is not None else "" for v in row] for row in section["rows"]
        ]
        table = Table(data, colWidths=section.get("col_widths"), repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(table)
        elements.append(Spacer(1, 12))

    doc.build(elements)
    return buffer.getvalue()


def build_liquidacion_pdf(
    *,
    empresa_nombre: str,
    empresa_nit: str | None,
    empresa_ubicacion: str | None,
    folio: str,
    estado: str,
    emitido: str,
    tercero_label: str,
    tercero_nombre: str,
    tercero_detalle: str | None,
    periodo: str,
    detalle_headers: Sequence[str],
    detalle_rows: Sequence[Sequence[Any]],
    detalle_col_widths: Sequence[float] | None = None,
    detalle_wrap_cols: Sequence[int] = (),
    resumen_rows: Sequence[tuple[str, str, bool]],
    anticipos_rows: Sequence[Sequence[Any]] = (),
    observaciones: str | None = None,
) -> bytes:
    """Comprobante de liquidación con membrete, resumen, anticipos y firmas.

    `detalle_col_widths`: ancho de cada columna del detalle EN CENTÍMETROS. Si no
    viene, la tabla se mide sola como siempre. Hace falta desde que el comprobante
    del transportador lleva una columna de texto (la ruta): con el ancho automático
    un nombre largo empujaba las cifras fuera de la hoja.

    `detalle_wrap_cols`: los índices de las columnas del detalle que son TEXTO
    LIBRE. Esas se imprimen envueltas (parten en varias líneas en vez de
    desbordarse) y alineadas a la izquierda. Por omisión ninguna, así que el
    comprobante del proveedor sale idéntico a como salía.
    """
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    st_company = ParagraphStyle("Company", parent=styles["Title"], fontSize=16, textColor=BRAND, spaceAfter=0, leading=18, alignment=0)
    st_sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=8, textColor=GREY, leading=11)
    st_doctitle = ParagraphStyle("DocT", parent=styles["Normal"], fontSize=12.5, textColor=BRAND, fontName="Helvetica-Bold", alignment=TA_RIGHT, leading=15)
    st_docmeta = ParagraphStyle("DocM", parent=styles["Normal"], fontSize=8.5, textColor=GREY, alignment=TA_RIGHT, leading=12)
    st_head = ParagraphStyle("Sec", parent=styles["Heading3"], fontSize=10.5, textColor=BRAND, spaceBefore=2, spaceAfter=4)
    st_lbl = ParagraphStyle("Lbl", parent=styles["Normal"], fontSize=7.5, textColor=GREY)
    st_val = ParagraphStyle("Val", parent=styles["Normal"], fontSize=9.5, fontName="Helvetica-Bold")
    st_obs = ParagraphStyle("Obs", parent=styles["Normal"], fontSize=9, leading=13)
    st_sign = ParagraphStyle("Sign", parent=styles["Normal"], fontSize=8.5, alignment=TA_CENTER, textColor=GREY, leading=12)

    # --- Encabezado: logo + empresa + datos del comprobante
    # Todo texto libre va escapado con _texto: un '<' en el nombre de la empresa
    # o del tercero borraría texto del recibo o tumbaría la generación.
    company_block: list[Any] = [Paragraph(_texto(empresa_nombre), st_company)]
    sub = " · ".join(
        p
        for p in [
            f"NIT {_texto(empresa_nit)}" if empresa_nit else None,
            _texto(empresa_ubicacion) if empresa_ubicacion else None,
        ]
        if p
    )
    if sub:
        company_block.append(Paragraph(sub, st_sub))
    doc_block = [
        Paragraph("COMPROBANTE DE LIQUIDACIÓN", st_doctitle),
        Paragraph(f"N.º {_texto(folio)}", st_docmeta),
        Paragraph(f"Estado: <b>{_texto(estado.upper())}</b>", st_docmeta),
        Paragraph(f"Emitido: {_texto(emitido)}", st_docmeta),
    ]
    logo_cell: Any = (
        RLImage(str(LOGO_PATH), width=1.4 * cm, height=1.4 * cm) if LOGO_PATH.exists() else ""
    )
    header = Table([[logo_cell, company_block, doc_block]], colWidths=[1.7 * cm, 8.6 * cm, 7.0 * cm])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements: list[Any] = [
        header,
        HRFlowable(width="100%", thickness=1.2, color=BRAND, spaceBefore=6, spaceAfter=10),
    ]

    # --- Datos del tercero
    info_rows = [
        [Paragraph(_texto(tercero_label), st_lbl), Paragraph(_texto(tercero_nombre), st_val),
         Paragraph("Período", st_lbl), Paragraph(_texto(periodo), st_val)],
    ]
    if tercero_detalle:
        info_rows.append(
            [Paragraph("Ruta / vereda", st_lbl), Paragraph(_texto(tercero_detalle), st_val),
             Paragraph("Comprobante", st_lbl), Paragraph(f"N.º {_texto(folio)}", st_val)]
        )
    info = Table(info_rows, colWidths=[2.6 * cm, 6.2 * cm, 2.6 * cm, 5.9 * cm])
    info.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6E0EA")),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements += [info, Spacer(1, 12)]

    # --- Detalle diario
    elements.append(Paragraph("Detalle diario", st_head))
    # El texto libre del detalle (hoy: el nombre de la ruta) va en Paragraph para
    # que se envuelva dentro de su celda. Y va escapado con _texto por lo mismo que
    # todo lo demás: una ruta llamada "La Y <arriba>" borraría texto del recibo.
    st_celda = ParagraphStyle("Celda", parent=styles["Normal"], fontSize=8, leading=9.5)
    envuelven = set(detalle_wrap_cols)
    det_data = [list(detalle_headers)] + [
        [
            Paragraph(_texto(v), st_celda)
            if i in envuelven
            else (str(_cell_value(v)) if v is not None else "")
            for i, v in enumerate(row)
        ]
        for row in detalle_rows
    ]
    det = Table(
        det_data,
        colWidths=[ancho * cm for ancho in detalle_col_widths] if detalle_col_widths else None,
        repeatRows=1,
        hAlign="LEFT",
    )
    det_style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    # Las columnas de texto van a la izquierda, encabezado incluido: la regla de
    # arriba alinea a la derecha todo lo que no sea la primera columna, y un
    # "Ruta / Nápoles" pegado a las cifras se lee como si fuera una cifra más.
    for columna in sorted(envuelven):
        det_style.append(("ALIGN", (columna, 0), (columna, -1), "LEFT"))
    det.setStyle(TableStyle(det_style))
    elements += [det, Spacer(1, 12)]

    # --- Resumen (con VALOR TOTAL y SALDO destacados)
    elements.append(Paragraph("Resumen de liquidación", st_head))
    res = Table([[c, v] for (c, v, _) in resumen_rows], colWidths=[6 * cm, 5 * cm], hAlign="RIGHT")
    res_style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E6E6E6")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i, (_, _, resaltado) in enumerate(resumen_rows):
        if resaltado:
            res_style += [
                ("BACKGROUND", (0, i), (-1, i), BRAND_LIGHT),
                ("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, i), (-1, i), BRAND),
                ("FONTSIZE", (0, i), (-1, i), 10),
            ]
    res.setStyle(TableStyle(res_style))
    elements += [res, Spacer(1, 12)]

    # --- Anticipos aplicados
    if anticipos_rows:
        elements.append(Paragraph("Anticipos aplicados", st_head))
        ant_data = [["Fecha", "Valor", "Observaciones"]] + [
            [str(_cell_value(v)) if v is not None else "" for v in row] for row in anticipos_rows
        ]
        ant = Table(ant_data, colWidths=[3 * cm, 3 * cm, 10.9 * cm], repeatRows=1, hAlign="LEFT")
        ant.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements += [ant, Spacer(1, 12)]

    # --- Observaciones
    if observaciones:
        elements.append(Paragraph("Observaciones", st_head))
        box = Table([[Paragraph(_texto(observaciones), st_obs)]], colWidths=[16.9 * cm])
        box.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6E0EA")),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements += [box]

    # --- Firmas
    elements.append(Spacer(1, 28))
    firma = Table(
        [
            ["", ""],
            [
                # El <br/> es marcado nuestro y se deja tal cual; los nombres van
                # escapados porque son texto libre.
                Paragraph(f"Entregué conforme<br/>{_texto(empresa_nombre)}", st_sign),
                Paragraph(f"Recibí conforme<br/>{_texto(tercero_nombre)}", st_sign),
            ],
        ],
        colWidths=[8.4 * cm, 8.4 * cm],
        rowHeights=[0.9 * cm, None],
    )
    firma.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black),
                ("LINEABOVE", (1, 1), (1, 1), 0.6, colors.black),
                ("TOPPADDING", (0, 1), (-1, 1), 4),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 20), ("RIGHTPADDING", (0, 0), (-1, -1), 20),
            ]
        )
    )
    elements.append(firma)

    def _footer(canvas: Any, doc_: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D6E0EA"))
        canvas.setLineWidth(0.5)
        canvas.line(1.5 * cm, 1.3 * cm, letter[0] - 1.5 * cm, 1.3 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(1.5 * cm, 1.0 * cm, f"Generado por Lactis · {emitido}")
        canvas.drawRightString(letter[0] - 1.5 * cm, 1.0 * cm, f"Página {doc_.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter, topMargin=1.4 * cm, bottomMargin=1.8 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm, title=f"Liquidación {folio}",
    )
    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def build_estado_cuenta_pdf(
    *,
    empresa_nombre: str,
    empresa_nit: str | None,
    empresa_ubicacion: str | None,
    cliente: str,
    emitido: str,
    periodo: str,
    compras: int,
    ventas: Sequence[dict[str, Any]],
    pagos: Sequence[dict[str, Any]],
    total_kilos: Decimal,
    total_facturado: Decimal,
    total_abonado: Decimal,
    saldo: Decimal,
    saldos_anteriores: Sequence[dict[str, Any]] = (),
    libro_anterior_total: Decimal = Decimal("0"),
    libro_anterior_abonado: Decimal = Decimal("0"),
    libro_anterior_saldo: Decimal = Decimal("0"),
    total_barras: Decimal = Decimal("0"),
) -> bytes:
    """Estado de cuenta de un cliente, con la misma familia visual del comprobante
    de liquidación.

    `ventas`: dicts con {fecha, producto, kilos, precio_kilo, valor_total,
    abonado, saldo} y, si la venta es de mozzarella, {unidad: 'barra', barras,
    precio_barra}. `pagos`: dicts con {fecha, valor}, y son SOLO los abonos de
    esas ventas: lo que el cliente abonó a una cuenta del sistema anterior va en
    la columna "Abonado" de `saldos_anteriores` (ver el comentario de la sección
    "Pagos recibidos", que explica por qué no se mezclan y cómo se le dice al
    cliente para no negarle un pago que sí hizo).

    LAS DOS UNIDADES NO SE SUMAN. Cada fila imprime su cantidad con su unidad
    ("40 kg" o "8 barras") y la fila de TOTALES lleva los dos subtotales en
    renglones separados: `total_kilos` y `total_barras`. No hay ni puede haber una
    casilla con la suma de los dos, porque 40 kg y 8 barras no son 48 de nada. Si
    `total_barras` viene en cero —el caso de todos los clientes de hoy— el
    documento sale exactamente igual que siempre, con la columna rotulada "Kilos".

    `saldos_anteriores`: dicts con {fecha, concepto, valor_total, abonado,
    saldo}, las cuentas a medio pagar que el cliente traía del sistema anterior.
    Si viene vacío, esa sección NO se imprime y el documento queda igual que
    siempre. `saldo` es TODO lo que debe (sistema + libro anterior), mientras
    que `total_facturado` y `total_abonado` son solo del sistema.

    OJO: este documento SE LE ENTREGA AL CLIENTE. Aquí no entra ni se imprime
    nada interno de la quesera (gastos de venta, "venta libre", costos de compra,
    productores, márgenes NI las observaciones del abono o del saldo anterior,
    que son la nota que la quesera se escribe a sí misma): sería mostrarle la
    ganancia del negocio.
    """
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    st_company = ParagraphStyle("Company", parent=styles["Title"], fontSize=16, textColor=BRAND, spaceAfter=0, leading=18, alignment=0)
    st_sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=8, textColor=GREY, leading=11)
    st_doctitle = ParagraphStyle("DocT", parent=styles["Normal"], fontSize=12.5, textColor=BRAND, fontName="Helvetica-Bold", alignment=TA_RIGHT, leading=15)
    st_docmeta = ParagraphStyle("DocM", parent=styles["Normal"], fontSize=8.5, textColor=GREY, alignment=TA_RIGHT, leading=12)
    st_head = ParagraphStyle("Sec", parent=styles["Heading3"], fontSize=10.5, textColor=BRAND, spaceBefore=2, spaceAfter=4)
    st_lbl = ParagraphStyle("Lbl", parent=styles["Normal"], fontSize=7.5, textColor=GREY)
    st_val = ParagraphStyle("Val", parent=styles["Normal"], fontSize=9.5, fontName="Helvetica-Bold", leading=12)
    st_vacio = ParagraphStyle("Vacio", parent=styles["Normal"], fontSize=9, textColor=GREY, leading=13)
    st_nota = ParagraphStyle("Nota", parent=styles["Normal"], fontSize=8, textColor=GREY, leading=11)
    # Los kilos van como Paragraph alineado a la derecha: una celda de texto
    # plano no se puede envolver y con más de 100 toneladas acumuladas el número
    # se salía de la columna en silencio.
    st_kilos = ParagraphStyle("Kilos", parent=styles["Normal"], fontSize=8, leading=10, alignment=TA_RIGHT)
    st_kilos_tot = ParagraphStyle(
        "KilosTot", parent=st_kilos, fontName="Helvetica-Bold", textColor=BRAND
    )
    # El concepto del saldo viejo es texto libre y suele ser largo ("Venta 120 kg
    # del 3 de mayo"): va como Paragraph para que se envuelva dentro de la celda.
    st_concepto = ParagraphStyle("Concepto", parent=styles["Normal"], fontSize=8, leading=10)

    # --- Encabezado: logo + empresa + bloque del documento
    # Todo texto libre va escapado con _texto: ReportLab interpreta mini-XML y un
    # '<' del usuario borraría texto o tumbaría la generación del documento.
    company_block: list[Any] = [Paragraph(_texto(empresa_nombre), st_company)]
    sub = " · ".join(
        p
        for p in [
            f"NIT {_texto(empresa_nit)}" if empresa_nit else None,
            _texto(empresa_ubicacion) if empresa_ubicacion else None,
        ]
        if p
    )
    if sub:
        company_block.append(Paragraph(sub, st_sub))
    doc_block = [
        Paragraph("ESTADO DE CUENTA", st_doctitle),
        Paragraph(f"Emitido: {_texto(emitido)}", st_docmeta),
    ]
    logo_cell: Any = (
        RLImage(str(LOGO_PATH), width=1.4 * cm, height=1.4 * cm) if LOGO_PATH.exists() else ""
    )
    header = Table([[logo_cell, company_block, doc_block]], colWidths=[1.7 * cm, 8.6 * cm, 7.0 * cm])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements: list[Any] = [
        header,
        HRFlowable(width="100%", thickness=1.2, color=BRAND, spaceBefore=6, spaceAfter=10),
    ]

    # Lo que queda debiendo SOLO por las compras hechas en este sistema. Es lo
    # que tiene que cerrar la columna "Saldo" del detalle de compras: `saldo`
    # ahora trae además la deuda del libro anterior, y usarlo en esa fila de
    # totales haría que la tabla no sumara. Sin saldos anteriores son iguales.
    saldo_sistema = total_facturado - total_abonado

    # --- Cómo va el saldo. Son TRES casos, no dos: si el cliente abonó de más
    # (pasa al editar una venta ya pagada), el saldo queda negativo y decirle
    # "Con saldo pendiente -$550.000" le dice lo contrario de la realidad.
    if saldo > 0:
        estado_cuenta = "Con saldo"
        rotulo_saldo = "SALDO PENDIENTE"
        valor_saldo = pesos(saldo)
    elif saldo == 0:
        estado_cuenta = "Al día"
        rotulo_saldo = "SALDO PENDIENTE"
        valor_saldo = pesos(Decimal("0"))
    else:
        estado_cuenta = "Saldo a favor"
        # Con DOS renglones (facturado y abonado) la convención de imprimir el
        # saldo a favor en positivo se entendía; con el renglón nuevo del saldo de
        # la cuenta anterior en medio, no: sumando a mano los tres renglones da el
        # negativo y la cifra destacada sale al revés. Se hace explícito de dos
        # maneras: el rótulo dice de quién es el saldo, y más abajo va un renglón
        # con la operación y su signo. Se prefiere eso a imprimir la cifra
        # destacada en negativo: es plata A FAVOR del cliente, y un signo menos
        # pegado a un total es justo lo que se lee mal.
        rotulo_saldo = "SALDO A FAVOR DEL CLIENTE"
        valor_saldo = pesos(abs(saldo))

    # --- Datos del cliente y del período (el nombre largo se envuelve solo)
    info = Table(
        [
            [Paragraph("Cliente", st_lbl), Paragraph(_texto(cliente), st_val),
             Paragraph("Período", st_lbl), Paragraph(_texto(periodo), st_val)],
            [Paragraph("Compras", st_lbl), Paragraph(str(compras), st_val),
             Paragraph("Estado", st_lbl),
             Paragraph(estado_cuenta, st_val)],
        ],
        colWidths=[2.6 * cm, 6.2 * cm, 2.6 * cm, 5.9 * cm],
    )
    info.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6E0EA")),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements += [info, Spacer(1, 12)]

    # --- Detalle de compras (solo lo que el cliente compró y pagó)
    elements.append(Paragraph("Detalle de compras", st_head))
    if ventas:
        # Los rótulos de las dos columnas de cantidad cambian SOLO si el cliente
        # tiene mozzarella. Con "Kilos" y "Precio/kg" encima de una fila de barras,
        # la cabecera contradiría la celda; y poner "Cantidad" siempre le cambiaría
        # el documento a todos los clientes de hoy sin necesidad.
        hay_barras = bool(total_barras) or any(
            v.get("unidad") == "barra" for v in ventas
        )
        det_data: list[list[Any]] = [
            [
                "Fecha",
                "Producto",
                "Cantidad" if hay_barras else "Kilos",
                "Precio" if hay_barras else "Precio/kg",
                "Total",
                "Abonado",
                "Saldo",
            ]
        ]
        for venta in ventas:
            # Cada fila con SU unidad. La que manda es `unidad`, que el servicio
            # deduce del tipo: no se adivina mirando cuál de las dos cantidades
            # viene en cero, porque una venta de 0 kg no existe pero un dato raro sí
            # podría, y entonces la fila diría cualquier cosa.
            de_barras = venta.get("unidad") == "barra"
            cantidad = (
                barras(venta.get("barras")) if de_barras else kilogramos(venta["kilos"])
            )
            precio = (
                pesos(venta.get("precio_barra")) if de_barras else pesos(venta["precio_kilo"])
            )
            det_data.append(
                [
                    venta["fecha"].strftime("%d/%m/%Y"),
                    venta["producto"],
                    Paragraph(cantidad, st_kilos),
                    precio,
                    pesos(venta["valor_total"]),
                    pesos(venta["abonado"]),
                    pesos(venta["saldo"]),
                ]
            )
        # Los TOTALES de cantidad van en RENGLONES SEPARADOS dentro de la misma
        # celda, uno por unidad, y solo aparece el de la unidad que el cliente de
        # verdad compró. Nunca se suman: no hay una casilla que junte kilos con
        # barras porque esa cifra no significaría nada.
        partes_total = []
        if total_kilos or not hay_barras:
            partes_total.append(kilogramos(total_kilos))
        if total_barras:
            partes_total.append(barras(total_barras))
        det_data.append(
            [
                "TOTALES", "", Paragraph("<br/>".join(partes_total), st_kilos_tot), "",
                pesos(total_facturado), pesos(total_abonado), pesos(saldo_sistema),
            ]
        )
        fila_totales = len(det_data) - 1
        det = Table(
            det_data,
            # El ancho útil de la página son 18,59 cm (carta menos los márgenes) y la
            # tabla usa 18,5: los kilos se llevan el espacio libre porque son la
            # celda que se desbordaba con toneladas acumuladas.
            colWidths=[2.3 * cm, 3.0 * cm, 2.5 * cm, 2.5 * cm, 2.8 * cm, 2.7 * cm, 2.7 * cm],
            repeatRows=1,
            hAlign="LEFT",
        )
        det_style: list[Any] = [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            # Fila de totales resaltada (kilos y plata)
            ("SPAN", (0, fila_totales), (1, fila_totales)),
            ("BACKGROUND", (0, fila_totales), (-1, fila_totales), BRAND_LIGHT),
            ("FONTNAME", (0, fila_totales), (-1, fila_totales), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, fila_totales), (-1, fila_totales), BRAND),
            ("ALIGN", (0, fila_totales), (1, fila_totales), "LEFT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, fila_totales - 1), [colors.white, BRAND_LIGHT]),
        ]
        det.setStyle(TableStyle(det_style))
        elements += [det, Spacer(1, 12)]
    else:
        # Pasa con el cliente que solo arrastra deuda del sistema anterior: una
        # tabla con encabezados y una fila de ceros no dice nada. (Antes esto no
        # podía ocurrir: sin ventas no había estado de cuenta.)
        elements += [Paragraph("Sin compras registradas", st_vacio), Spacer(1, 12)]

    # --- Saldos de la cuenta anterior: lo que el cliente ya venía debiendo del
    # sistema que se usaba antes. Si no hay, la sección no aparece y el documento
    # queda exactamente igual que siempre.
    if saldos_anteriores:
        elements.append(Paragraph("Saldos de la cuenta anterior", st_head))
        ant_data: list[list[Any]] = [["Fecha", "Concepto", "Total", "Abonado", "Saldo"]]
        for anterior in saldos_anteriores:
            ant_data.append(
                [
                    anterior["fecha"].strftime("%d/%m/%Y"),
                    # El concepto es texto libre: va escapado, como todo lo que
                    # escribe el usuario y entra en un Paragraph.
                    Paragraph(_texto(anterior["concepto"]), st_concepto),
                    pesos(anterior["valor_total"]),
                    pesos(anterior["abonado"]),
                    pesos(anterior["saldo"]),
                ]
            )
        ant_data.append(
            [
                "TOTALES", "", pesos(libro_anterior_total),
                pesos(libro_anterior_abonado), pesos(libro_anterior_saldo),
            ]
        )
        fila_tot_ant = len(ant_data) - 1
        ant = Table(
            ant_data,
            # Mismos 18,5 cm de ancho que el detalle de compras; el concepto se
            # lleva el espacio porque es la columna que se envuelve.
            colWidths=[2.3 * cm, 7.0 * cm, 3.1 * cm, 3.1 * cm, 3.0 * cm],
            repeatRows=1,
            hAlign="LEFT",
        )
        ant.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("ROWBACKGROUNDS", (0, 1), (-1, fila_tot_ant - 1), [colors.white, BRAND_LIGHT]),
                    # Fila de totales resaltada, igual que en el detalle de compras
                    ("SPAN", (0, fila_tot_ant), (1, fila_tot_ant)),
                    ("BACKGROUND", (0, fila_tot_ant), (-1, fila_tot_ant), BRAND_LIGHT),
                    ("FONTNAME", (0, fila_tot_ant), (-1, fila_tot_ant), "Helvetica-Bold"),
                    ("TEXTCOLOR", (0, fila_tot_ant), (-1, fila_tot_ant), BRAND),
                    ("ALIGN", (0, fila_tot_ant), (1, fila_tot_ant), "LEFT"),
                ]
            )
        )
        elements += [
            ant,
            Spacer(1, 4),
            Paragraph(
                "Estas cuentas vienen del sistema que se usaba antes y no "
                "corresponden a compras registradas aquí.",
                st_nota,
            ),
            Spacer(1, 12),
        ]

    # --- Pagos recibidos
    #
    # DECISIÓN: esta tabla lista SOLO los abonos de las compras hechas en este
    # sistema. Los abonos de las cuentas anteriores NO se mezclan aquí a
    # propósito: sus totales ya están cuadrados en la sección de arriba (columna
    # Abonado y su fila de TOTALES), y traerlos también a esta tabla haría que la
    # misma plata apareciera dos veces en el documento, o obligaría a una fila de
    # totales que no cuadraría con `total_abonado`, que es solo del sistema.
    # Lo que sí estaba mal era el TEXTO: al cliente que acabó de abonarle a su
    # cuenta vieja el documento le decía "Sin pagos registrados", o sea le negaba
    # un pago que sí hizo. Cuando hay saldos anteriores el texto cambia y se
    # remite a la columna donde ese abono SÍ está.
    elements.append(Paragraph("Pagos recibidos", st_head))
    if pagos:
        # Solo Fecha y Valor. Las observaciones del abono NO van: son la nota
        # interna de la quesera (a quién se le pagó, cuánto, qué se rebajó) y
        # este documento se le entrega al cliente.
        pag_data: list[list[Any]] = [["Fecha", "Valor"]]
        for pago in pagos:
            pag_data.append(
                [pago["fecha"].strftime("%d/%m/%Y"), pesos(pago["valor"])]
            )
        pag = Table(
            pag_data, colWidths=[5.0 * cm, 5.0 * cm], repeatRows=1, hAlign="CENTER"
        )
        pag.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
                    # Desde la fila 0: el encabezado "Valor" tiene que quedar
                    # sobre los montos, igual que en la tabla de compras.
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements += [pag]
    elif saldos_anteriores:
        elements += [
            Paragraph(
                "Sin pagos recibidos por compras registradas en este sistema.",
                st_vacio,
            )
        ]
    else:
        elements += [Paragraph("Sin pagos registrados", st_vacio)]
    if saldos_anteriores:
        elements += [
            Spacer(1, 4),
            Paragraph(
                "Los abonos que hizo a las cuentas del sistema anterior están en "
                'la columna "Abonado" de la sección "Saldos de la cuenta '
                'anterior".',
                st_nota,
            ),
        ]
    elements.append(Spacer(1, 12))

    # --- Resumen (con el saldo destacado). Los renglones tienen que SUMAR la
    # cifra destacada: facturado - abonado + saldo de la cuenta anterior. Cada
    # renglón lleva su operador escrito porque con tres renglones ya no se
    # adivina cuál se resta: el cliente reproduce la cuenta con la calculadora.
    resumen_rows: list[tuple[str, str, bool]] = [
        ("Total facturado", pesos(total_facturado), False),
        ("(-) Total abonado", pesos(total_abonado), False),
    ]
    if saldos_anteriores:
        resumen_rows.append(
            ("(+) Saldo de la cuenta anterior", pesos(libro_anterior_saldo), False)
        )
    resumen_rows.append(
        # Con saldo negativo el rótulo cambia y el valor va en POSITIVO: es plata
        # a favor del cliente, no una deuda con signo menos.
        (rotulo_saldo, valor_saldo, True)
    )
    res = Table([[c, v] for (c, v, _) in resumen_rows], colWidths=[6 * cm, 5 * cm], hAlign="RIGHT")
    res_style: list[Any] = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E6E6E6")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i, (_, _, resaltado) in enumerate(resumen_rows):
        if resaltado:
            res_style += [
                ("BACKGROUND", (0, i), (-1, i), BRAND_LIGHT),
                ("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, i), (-1, i), BRAND),
                ("FONTSIZE", (0, i), (-1, i), 10),
            ]
    res.setStyle(TableStyle(res_style))
    elements += [res, Spacer(1, 8)]

    if saldo < 0:
        # La cifra destacada va en positivo (es plata a favor suya), así que aquí
        # queda escrita la operación CON su signo: sin esto, sumando los tres
        # renglones a mano daba -$1.500.000 contra un destacado de $1.500.000 y
        # parecía un error del documento.
        operacion = f"{pesos(total_facturado)} - {pesos(total_abonado)}"
        if saldos_anteriores:
            operacion += f" + {pesos(libro_anterior_saldo)}"
        elements += [
            Paragraph(
                f"La cuenta da {operacion} = {pesos(saldo)}, es decir que queda a "
                "favor suyo: por eso arriba aparece en positivo.",
                st_nota,
            ),
            Spacer(1, 8),
        ]
    elements.append(Spacer(1, 6))

    # --- Nota informativa (sin bloque de firmas: no es un comprobante de pago)
    elements.append(
        Paragraph(
            "Este documento es un resumen informativo de su cuenta. Si encuentra "
            "alguna diferencia, comuníquese con nosotros.",
            st_nota,
        )
    )

    # Pie PROPIO del estado de cuenta (el de las liquidaciones no se toca): en
    # las páginas de continuación hay que decir de qué documento y de qué cliente
    # es la hoja, porque "Generado por Lactis · Página 3" no identifica nada.
    def _pie_estado_cuenta(canvas: Any, doc_: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D6E0EA"))
        canvas.setLineWidth(0.5)
        canvas.line(1.5 * cm, 1.3 * cm, letter[0] - 1.5 * cm, 1.3 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(1.5 * cm, 1.0 * cm, f"Generado por Lactis · {emitido}")
        canvas.drawRightString(letter[0] - 1.5 * cm, 1.0 * cm, f"Página {doc_.page}")
        if doc_.page > 1:
            # El nombre se recorta para no chocar con los textos de los extremos
            # (el pie se dibuja en el canvas, no se envuelve solo).
            nombre = cliente if len(cliente) <= 60 else f"{cliente[:57]}…"
            canvas.drawCentredString(
                letter[0] / 2, 1.0 * cm, f"Estado de cuenta · {nombre}"
            )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter, topMargin=1.4 * cm, bottomMargin=1.8 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm, title=f"Estado de cuenta {cliente}",
    )
    doc.build(elements, onFirstPage=_pie_estado_cuenta, onLaterPages=_pie_estado_cuenta)
    return buffer.getvalue()


def build_estado_cuenta_productor_pdf(
    *,
    empresa_nombre: str,
    empresa_nit: str | None,
    empresa_ubicacion: str | None,
    productor: str,
    emitido: str,
    periodo: str,
    compras: int,
    compras_detalle: Sequence[dict[str, Any]],
    pagos: Sequence[dict[str, Any]],
    total_kilos: Decimal,
    total_comprado: Decimal,
    total_pagado: Decimal,
    saldo: Decimal,
    saldos_anteriores: Sequence[dict[str, Any]] = (),
    libro_anterior_total: Decimal = Decimal("0"),
    libro_anterior_abonado: Decimal = Decimal("0"),
    libro_anterior_saldo: Decimal = Decimal("0"),
    total_barras: Decimal = Decimal("0"),
) -> bytes:
    """Estado de cuenta de un PRODUCTOR: el espejo de build_estado_cuenta_pdf.

    Es una función PROPIA y no una generalización de la del cliente a propósito:
    esa ya está desplegada y verificada, y tocarla es riesgo puro. Aquí se reusan
    sus helpers (BRAND, LOGO_PATH, pesos, kilogramos, _texto, el encabezado con
    logo, las tablas con header BRAND y el pie).

    `compras_detalle`: dicts con {fecha, kilos, borona_kilos, precio_kilo,
    valor_total, abonado, saldo} y, si la compra es de mozzarella, {unidad:
    'barra', barras, precio_barra}. La borona NO lleva columna propia (ensancharía
    la tabla): va en una nota corta al pie, porque es información suya y es
    honesto decirle cuántos kilos vinieron con los lotes sin que se le paguen.

    LAS DOS UNIDADES NO SE SUMAN, igual que en el documento del cliente: cada fila
    imprime su cantidad con su unidad y los TOTALES llevan un renglón por unidad.
    Con `total_barras` en cero —el caso de todos los productores de hoy— el
    documento sale exactamente igual que siempre.
    `pagos`: dicts con {fecha, valor}, y son SOLO los abonos de esas compras: lo
    que se le abonó a una cuenta del sistema anterior va en la columna "Abonado"
    de `saldos_anteriores` (ver el comentario de la sección "Pagos realizados").

    `saldos_anteriores`: dicts con {fecha, concepto, valor_total, abonado,
    saldo}, las cuentas a medio pagar que la quesera le quedó debiendo en el
    sistema anterior (SOLO las de tipo 'pagar'). Si viene vacío, esa sección no
    se imprime.

    OJO, ES UN DOCUMENTO INTERNO PARA CUADRAR CUENTAS CON EL PRODUCTOR: sin
    numeración consecutiva, sin resolución de la DIAN y sin IVA. No es una
    factura fiscal, no lo dice y no debe parecerlo.

    Y OJO CON LA CONFIDENCIALIDAD, QUE VA AL CONTRARIO QUE EN EL DEL CLIENTE:
    esto lo lee el PRODUCTOR, así que aquí no entra ni se imprime NADA del lado
    de la venta (a qué precio se revendió su queso, total de ventas, precio
    promedio de venta, márgenes, ganancia, gastos de venta ni nombres de
    clientes) ni los saldos del libro anterior de tipo 'cobrar', que son deudas
    de clientes.

    LOS SIGNOS TAMBIÉN VAN AL CONTRARIO: un `saldo` positivo significa que LA
    QUESERA LE DEBE A ÉL, así que la cifra destacada va rotulada como saldo A
    FAVOR DEL PRODUCTOR. Si sale negativo es que se le pagó de más, y el rótulo
    cambia (ver más abajo).
    """
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    st_company = ParagraphStyle("Company", parent=styles["Title"], fontSize=16, textColor=BRAND, spaceAfter=0, leading=18, alignment=0)
    st_sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=8, textColor=GREY, leading=11)
    st_doctitle = ParagraphStyle("DocT", parent=styles["Normal"], fontSize=12.5, textColor=BRAND, fontName="Helvetica-Bold", alignment=TA_RIGHT, leading=15)
    st_docmeta = ParagraphStyle("DocM", parent=styles["Normal"], fontSize=8.5, textColor=GREY, alignment=TA_RIGHT, leading=12)
    st_head = ParagraphStyle("Sec", parent=styles["Heading3"], fontSize=10.5, textColor=BRAND, spaceBefore=2, spaceAfter=4)
    st_lbl = ParagraphStyle("Lbl", parent=styles["Normal"], fontSize=7.5, textColor=GREY)
    st_val = ParagraphStyle("Val", parent=styles["Normal"], fontSize=9.5, fontName="Helvetica-Bold", leading=12)
    st_vacio = ParagraphStyle("Vacio", parent=styles["Normal"], fontSize=9, textColor=GREY, leading=13)
    st_nota = ParagraphStyle("Nota", parent=styles["Normal"], fontSize=8, textColor=GREY, leading=11)
    # Los kilos van como Paragraph alineado a la derecha, igual que en el del
    # cliente: una celda de texto plano no se envuelve y con toneladas acumuladas
    # el número se salía de la columna en silencio.
    st_kilos = ParagraphStyle("Kilos", parent=styles["Normal"], fontSize=8, leading=10, alignment=TA_RIGHT)
    st_kilos_tot = ParagraphStyle(
        "KilosTot", parent=st_kilos, fontName="Helvetica-Bold", textColor=BRAND
    )
    st_concepto = ParagraphStyle("Concepto", parent=styles["Normal"], fontSize=8, leading=10)

    # --- 1. Encabezado: logo + empresa + bloque del documento
    # Todo texto libre va escapado con _texto: ReportLab interpreta mini-XML y un
    # '<' del usuario borraría texto o tumbaría la generación del documento.
    company_block: list[Any] = [Paragraph(_texto(empresa_nombre), st_company)]
    sub = " · ".join(
        p
        for p in [
            f"NIT {_texto(empresa_nit)}" if empresa_nit else None,
            _texto(empresa_ubicacion) if empresa_ubicacion else None,
        ]
        if p
    )
    if sub:
        company_block.append(Paragraph(sub, st_sub))
    doc_block = [
        Paragraph("ESTADO DE CUENTA DEL PRODUCTOR", st_doctitle),
        Paragraph(f"Emitido: {_texto(emitido)}", st_docmeta),
    ]
    logo_cell: Any = (
        RLImage(str(LOGO_PATH), width=1.4 * cm, height=1.4 * cm) if LOGO_PATH.exists() else ""
    )
    header = Table([[logo_cell, company_block, doc_block]], colWidths=[1.7 * cm, 8.2 * cm, 7.4 * cm])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements: list[Any] = [
        header,
        HRFlowable(width="100%", thickness=1.2, color=BRAND, spaceBefore=6, spaceAfter=10),
    ]

    # Lo que se le queda debiendo SOLO por las compras hechas en este sistema. Es
    # lo que tiene que cerrar la columna "Saldo" del detalle: `saldo` trae además
    # la deuda del libro anterior, y usarlo en la fila de totales haría que la
    # tabla no sumara. Sin saldos anteriores los dos son iguales.
    saldo_sistema = total_comprado - total_pagado

    # --- Cómo va el saldo. Son TRES casos: lo normal es que se le deba, puede
    # estar al día, y puede haberse pagado de más (pasa al rebajarle el precio a
    # una compra ya pagada). Cada caso lleva su propio rótulo: decirle "saldo a
    # favor suyo -$550.000" le diría lo contrario de la realidad.
    if saldo > 0:
        estado_cuenta = "Con saldo a favor suyo"
        rotulo_saldo = "SALDO A FAVOR DEL PRODUCTOR"
        valor_saldo = pesos(saldo)
    elif saldo == 0:
        estado_cuenta = "Al día"
        rotulo_saldo = "SALDO A FAVOR DEL PRODUCTOR"
        valor_saldo = pesos(Decimal("0"))
    else:
        # Se le pagó más de lo que valían sus compras. La cifra destacada va en
        # POSITIVO y el rótulo dice de quién es, igual que se hizo en el del
        # cliente: un signo menos pegado a un total es justo lo que se lee mal.
        # Abajo va además un renglón con la operación y su signo.
        estado_cuenta = "Se le pagó de más"
        rotulo_saldo = "PAGADO DE MÁS (a favor de la quesera)"
        valor_saldo = pesos(abs(saldo))

    # --- 2. Datos del productor y del período (el nombre largo se envuelve solo)
    info = Table(
        [
            [Paragraph("Productor", st_lbl), Paragraph(_texto(productor), st_val),
             Paragraph("Período", st_lbl), Paragraph(_texto(periodo), st_val)],
            [Paragraph("Compras", st_lbl), Paragraph(str(compras), st_val),
             Paragraph("Estado", st_lbl), Paragraph(estado_cuenta, st_val)],
        ],
        colWidths=[2.6 * cm, 6.2 * cm, 2.6 * cm, 5.9 * cm],
    )
    info.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6E0EA")),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements += [info, Spacer(1, 12)]

    # --- 3. Detalle de compras (solo lo suyo: lo que se le compró y se le pagó)
    elements.append(Paragraph("Detalle de compras", st_head))
    if compras_detalle:
        # Mismo criterio que en el documento del cliente: los rótulos cambian solo
        # si de verdad hay barras, para no tocarles el documento a los productores
        # de queso, que son todos los de hoy.
        hay_barras = bool(total_barras) or any(
            c.get("unidad") == "barra" for c in compras_detalle
        )
        det_data: list[list[Any]] = [
            [
                "Fecha",
                "Cantidad" if hay_barras else "Kilos",
                "Precio" if hay_barras else "Precio/kg",
                "Total",
                "Abonado",
                "Saldo",
            ]
        ]
        borona_total = Decimal("0")
        for compra in compras_detalle:
            borona_total += Decimal(compra.get("borona_kilos") or 0)
            # La unidad la manda el campo `unidad`, no se adivina (ver el del cliente).
            de_barras = compra.get("unidad") == "barra"
            cantidad = (
                barras(compra.get("barras")) if de_barras else kilogramos(compra["kilos"])
            )
            precio = (
                pesos(compra.get("precio_barra")) if de_barras else pesos(compra["precio_kilo"])
            )
            det_data.append(
                [
                    compra["fecha"].strftime("%d/%m/%Y"),
                    Paragraph(cantidad, st_kilos),
                    precio,
                    pesos(compra["valor_total"]),
                    pesos(compra["abonado"]),
                    pesos(compra["saldo"]),
                ]
            )
        # Un renglón por unidad, nunca una casilla que las junte.
        partes_total = []
        if total_kilos or not hay_barras:
            partes_total.append(kilogramos(total_kilos))
        if total_barras:
            partes_total.append(barras(total_barras))
        det_data.append(
            [
                "TOTALES", Paragraph("<br/>".join(partes_total), st_kilos_tot), "",
                pesos(total_comprado), pesos(total_pagado), pesos(saldo_sistema),
            ]
        )
        fila_totales = len(det_data) - 1
        det = Table(
            det_data,
            # El ancho útil de la página son 18,59 cm (carta menos los márgenes) y
            # la tabla usa 18,5. Sin la columna "Producto" del documento del
            # cliente sobra espacio: se reparte entre la plata, que es lo que el
            # productor revisa con calculadora.
            colWidths=[2.6 * cm, 2.9 * cm, 3.0 * cm, 3.4 * cm, 3.3 * cm, 3.3 * cm],
            repeatRows=1,
            hAlign="LEFT",
        )
        det.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("ROWBACKGROUNDS", (0, 1), (-1, fila_totales - 1), [colors.white, BRAND_LIGHT]),
                    # Fila de totales resaltada (kilos y plata)
                    ("BACKGROUND", (0, fila_totales), (-1, fila_totales), BRAND_LIGHT),
                    ("FONTNAME", (0, fila_totales), (-1, fila_totales), "Helvetica-Bold"),
                    ("TEXTCOLOR", (0, fila_totales), (-1, fila_totales), BRAND),
                    ("ALIGN", (0, fila_totales), (0, fila_totales), "LEFT"),
                ]
            )
        )
        elements += [det]
        if borona_total > 0:
            # La borona NO tiene columna propia (ensancharía la tabla), pero sí se
            # dice: son kilos suyos que vinieron con los lotes y no se le pagan.
            elements += [
                Spacer(1, 4),
                Paragraph(
                    f"Con los lotes vinieron además {kilogramos(borona_total)} de "
                    "borona, que no se pagan y por eso no suman en el total.",
                    st_nota,
                ),
            ]
        elements.append(Spacer(1, 12))
    else:
        # Pasa con el productor que solo arrastra deuda del sistema anterior: una
        # tabla con encabezados y una fila de ceros no dice nada.
        elements += [Paragraph("Sin compras registradas", st_vacio), Spacer(1, 12)]

    # --- 4. Saldos de la cuenta anterior: lo que se le venía debiendo del sistema
    # que se usaba antes (solo los de tipo 'pagar'). Si no hay, no aparece.
    if saldos_anteriores:
        elements.append(Paragraph("Saldos de la cuenta anterior", st_head))
        ant_data: list[list[Any]] = [["Fecha", "Concepto", "Total", "Abonado", "Saldo"]]
        for anterior in saldos_anteriores:
            ant_data.append(
                [
                    anterior["fecha"].strftime("%d/%m/%Y"),
                    # El concepto es texto libre: va escapado, como todo lo que
                    # escribe el usuario y entra en un Paragraph.
                    Paragraph(_texto(anterior["concepto"]), st_concepto),
                    pesos(anterior["valor_total"]),
                    pesos(anterior["abonado"]),
                    pesos(anterior["saldo"]),
                ]
            )
        ant_data.append(
            [
                "TOTALES", "", pesos(libro_anterior_total),
                pesos(libro_anterior_abonado), pesos(libro_anterior_saldo),
            ]
        )
        fila_tot_ant = len(ant_data) - 1
        ant = Table(
            ant_data,
            # Mismos 18,5 cm de ancho que el detalle de compras; el concepto se
            # lleva el espacio porque es la columna que se envuelve.
            colWidths=[2.3 * cm, 7.0 * cm, 3.1 * cm, 3.1 * cm, 3.0 * cm],
            repeatRows=1,
            hAlign="LEFT",
        )
        ant.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("ROWBACKGROUNDS", (0, 1), (-1, fila_tot_ant - 1), [colors.white, BRAND_LIGHT]),
                    # Fila de totales resaltada, igual que en el detalle de compras
                    ("SPAN", (0, fila_tot_ant), (1, fila_tot_ant)),
                    ("BACKGROUND", (0, fila_tot_ant), (-1, fila_tot_ant), BRAND_LIGHT),
                    ("FONTNAME", (0, fila_tot_ant), (-1, fila_tot_ant), "Helvetica-Bold"),
                    ("TEXTCOLOR", (0, fila_tot_ant), (-1, fila_tot_ant), BRAND),
                    ("ALIGN", (0, fila_tot_ant), (1, fila_tot_ant), "LEFT"),
                ]
            )
        )
        elements += [
            ant,
            Spacer(1, 4),
            Paragraph(
                "Estas cuentas vienen del sistema que se usaba antes y no "
                "corresponden a compras registradas aquí.",
                st_nota,
            ),
            Spacer(1, 12),
        ]

    # --- 5. Pagos realizados
    #
    # Como en el documento del cliente, esta tabla lista SOLO los abonos de las
    # compras hechas en este sistema: los de las cuentas anteriores ya están
    # cuadrados arriba (columna Abonado y su fila de TOTALES), y traerlos también
    # aquí haría que la misma plata apareciera dos veces. Lo que sí se cuida es el
    # TEXTO: a quien se le acabó de abonar a su cuenta vieja no se le puede decir
    # "Sin pagos registrados", porque sería negarle un pago que sí recibió.
    elements.append(Paragraph("Pagos realizados", st_head))
    if pagos:
        # Solo Fecha y Valor: las observaciones del abono son la nota interna de
        # la quesera y este documento se le entrega al productor.
        pag_data: list[list[Any]] = [["Fecha", "Valor"]]
        for pago in pagos:
            pag_data.append([pago["fecha"].strftime("%d/%m/%Y"), pesos(pago["valor"])])
        pag = Table(
            pag_data, colWidths=[5.0 * cm, 5.0 * cm], repeatRows=1, hAlign="CENTER"
        )
        pag.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E0EA")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
                    # Desde la fila 0: el encabezado "Valor" tiene que quedar
                    # sobre los montos, igual que en la tabla de compras.
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements += [pag]
    elif saldos_anteriores:
        elements += [
            Paragraph(
                "Sin pagos por compras registradas en este sistema.", st_vacio
            )
        ]
    else:
        elements += [Paragraph("Sin pagos registrados", st_vacio)]
    if saldos_anteriores:
        elements += [
            Spacer(1, 4),
            Paragraph(
                "Los abonos que se le hicieron a las cuentas del sistema anterior "
                'están en la columna "Abonado" de la sección "Saldos de la cuenta '
                'anterior".',
                st_nota,
            ),
        ]
    elements.append(Spacer(1, 12))

    # --- 6. Resumen (con el saldo destacado). Los renglones tienen que SUMAR la
    # cifra destacada: comprado - pagado + saldo de la cuenta anterior. Cada
    # renglón lleva su operador escrito porque con tres renglones ya no se adivina
    # cuál se resta: el productor reproduce la cuenta con la calculadora.
    resumen_rows: list[tuple[str, str, bool]] = [
        ("Total comprado", pesos(total_comprado), False),
        ("(-) Total pagado", pesos(total_pagado), False),
    ]
    if saldos_anteriores:
        resumen_rows.append(
            ("(+) Saldo de la cuenta anterior", pesos(libro_anterior_saldo), False)
        )
    resumen_rows.append(
        # Con saldo negativo el rótulo cambia y el valor va en POSITIVO: se le
        # pagó de más, no es una deuda de la quesera con signo menos.
        (rotulo_saldo, valor_saldo, True)
    )
    # La columna del rótulo va más ancha que en el del cliente: "SALDO A FAVOR DEL
    # PRODUCTOR" y "PAGADO DE MÁS (a favor de la quesera)" son textos largos y una
    # celda de texto plano no se envuelve (se saldría de la columna en silencio).
    res = Table([[c, v] for (c, v, _) in resumen_rows], colWidths=[7.4 * cm, 5 * cm], hAlign="RIGHT")
    res_style: list[Any] = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E6E6E6")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i, (_, _, resaltado) in enumerate(resumen_rows):
        if resaltado:
            res_style += [
                ("BACKGROUND", (0, i), (-1, i), BRAND_LIGHT),
                ("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, i), (-1, i), BRAND),
                ("FONTSIZE", (0, i), (-1, i), 10),
            ]
    res.setStyle(TableStyle(res_style))
    elements += [res, Spacer(1, 8)]

    if saldo < 0:
        # La cifra destacada va en positivo, así que aquí queda escrita la
        # operación CON su signo: sin esto, sumando los renglones a mano da un
        # negativo contra un destacado en positivo y parece un error del documento.
        operacion = f"{pesos(total_comprado)} - {pesos(total_pagado)}"
        if saldos_anteriores:
            operacion += f" + {pesos(libro_anterior_saldo)}"
        elements += [
            Paragraph(
                f"La cuenta da {operacion} = {pesos(saldo)}, es decir que se le "
                f"pagaron {pesos(abs(saldo))} más de lo que valen sus compras: por "
                "eso arriba aparece en positivo a favor de la quesera.",
                st_nota,
            ),
            Spacer(1, 8),
        ]
    elements.append(Spacer(1, 6))

    # --- 7. Nota final (sin firmas: no es un comprobante de pago ni una factura)
    elements.append(
        Paragraph(
            "Este documento es un resumen informativo de la cuenta y no es una "
            "factura. Si encuentra alguna diferencia, comuníquese con nosotros.",
            st_nota,
        )
    )

    # Pie propio: en las páginas de continuación hay que decir de qué documento y
    # de qué productor es la hoja (el del cliente no se toca).
    def _pie_estado_cuenta_productor(canvas: Any, doc_: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D6E0EA"))
        canvas.setLineWidth(0.5)
        canvas.line(1.5 * cm, 1.3 * cm, letter[0] - 1.5 * cm, 1.3 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(1.5 * cm, 1.0 * cm, f"Generado por Lactis · {emitido}")
        canvas.drawRightString(letter[0] - 1.5 * cm, 1.0 * cm, f"Página {doc_.page}")
        if doc_.page > 1:
            # El nombre se recorta para no chocar con los textos de los extremos
            # (el pie se dibuja en el canvas, no se envuelve solo).
            nombre = productor if len(productor) <= 50 else f"{productor[:47]}…"
            canvas.drawCentredString(
                letter[0] / 2, 1.0 * cm, f"Estado de cuenta del productor · {nombre}"
            )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter, topMargin=1.4 * cm, bottomMargin=1.8 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title=f"Estado de cuenta del productor {productor}",
    )
    doc.build(
        elements,
        onFirstPage=_pie_estado_cuenta_productor,
        onLaterPages=_pie_estado_cuenta_productor,
    )
    return buffer.getvalue()


def litros(valor: Any) -> str:
    """Litros con la misma precisión que guarda la base (hasta 2 decimales) y sin
    ceros a la derecha: 250 L, 227,5 L, 1.234,75 L.

    Mismo criterio de `kilogramos`, pero para la leche recibida: recortar la
    precisión hacía que la columna del comprobante no sumara y el productor no
    pudiera reproducir su propio total. Los miles van con punto y los decimales
    con coma, como se escribe en Colombia.
    """
    numero = _medio_arriba(valor, DOS_DECIMALES)
    return f"{_miles(numero, _decimales_utiles(numero, 2))} L"
