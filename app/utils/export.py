"""Utilidades de exportación a PDF (reportlab)."""
import io
from datetime import date, datetime
from decimal import Decimal
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


def _miles(valor: Any, decimales: int) -> str:
    """Número con separador de miles PUNTO y decimal COMA (estilo colombiano).

    El formato de Python (`:,.0f`) separa los miles con coma, que en Colombia es
    justo el separador decimal: $18,525,000 se lee mal. Se voltean los dos
    separadores usando un marcador temporal para no pisar el trabajo hecho.
    """
    texto = f"{Decimal(valor or 0):,.{decimales}f}"
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
    numero = Decimal(valor or 0)
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
    numero = Decimal(valor or 0).quantize(Decimal("0.01"))
    return f"{_miles(numero, _decimales_utiles(numero, 2))} kg"


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
    resumen_rows: Sequence[tuple[str, str, bool]],
    anticipos_rows: Sequence[Sequence[Any]] = (),
    observaciones: str | None = None,
) -> bytes:
    """Comprobante de liquidación con membrete, resumen, anticipos y firmas."""
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
    det_data = [list(detalle_headers)] + [
        [str(_cell_value(v)) if v is not None else "" for v in row] for row in detalle_rows
    ]
    det = Table(det_data, repeatRows=1, hAlign="LEFT")
    det.setStyle(
        TableStyle(
            [
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
        )
    )
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
) -> bytes:
    """Estado de cuenta de un cliente, con la misma familia visual del comprobante
    de liquidación.

    `ventas`: dicts con {fecha, producto, kilos, precio_kilo, valor_total,
    abonado, saldo}. `pagos`: dicts con {fecha, valor}.

    OJO: este documento SE LE ENTREGA AL CLIENTE. Aquí no entra ni se imprime
    nada interno de la quesera (gastos de venta, "venta libre", costos de compra,
    productores, márgenes NI las observaciones del abono, que son la nota que la
    quesera se escribe a sí misma): sería mostrarle la ganancia del negocio.
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
        rotulo_saldo = "SALDO A FAVOR"
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
    det_data: list[list[Any]] = [
        ["Fecha", "Producto", "Kilos", "Precio/kg", "Total", "Abonado", "Saldo"]
    ]
    for venta in ventas:
        det_data.append(
            [
                venta["fecha"].strftime("%d/%m/%Y"),
                venta["producto"],
                Paragraph(kilogramos(venta["kilos"]), st_kilos),
                pesos(venta["precio_kilo"]),
                pesos(venta["valor_total"]),
                pesos(venta["abonado"]),
                pesos(venta["saldo"]),
            ]
        )
    det_data.append(
        [
            "TOTALES", "", Paragraph(kilogramos(total_kilos), st_kilos_tot), "",
            pesos(total_facturado), pesos(total_abonado), pesos(saldo),
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
    ]
    if ventas:
        det_style.append(
            ("ROWBACKGROUNDS", (0, 1), (-1, fila_totales - 1), [colors.white, BRAND_LIGHT])
        )
    det.setStyle(TableStyle(det_style))
    elements += [det, Spacer(1, 12)]

    # --- Pagos recibidos
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
        elements += [pag, Spacer(1, 12)]
    else:
        elements += [Paragraph("Sin pagos registrados", st_vacio), Spacer(1, 12)]

    # --- Resumen (con el saldo destacado)
    resumen_rows: list[tuple[str, str, bool]] = [
        ("Total facturado", pesos(total_facturado), False),
        ("Total abonado", pesos(total_abonado), False),
        # Con saldo negativo el rótulo cambia y el valor va en POSITIVO: es plata
        # a favor del cliente, no una deuda con signo menos.
        (rotulo_saldo, valor_saldo, True),
    ]
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
    elements += [res, Spacer(1, 14)]

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
