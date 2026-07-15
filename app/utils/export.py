"""Utilidades de exportación a Excel (openpyxl) y PDF (reportlab)."""
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name="Arial", size=10)
THIN_BORDER = Border(*[Side(style="thin")] * 4)


def _cell_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return value


def rows_to_excel(
    *,
    title: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    sheet_name: str = "Datos",
    money_columns: Sequence[int] = (),
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(len(headers), 1))
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = Font(name="Arial", bold=True, size=13)
    title_cell.alignment = Alignment(horizontal="center")

    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    for r, row in enumerate(rows, start=4):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=_cell_value(value))
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            if c in money_columns:
                cell.number_format = "$#,##0"

    for col in range(1, len(headers) + 1):
        max_len = max(
            [len(str(headers[col - 1]))] + [len(str(row[col - 1])) for row in rows if col <= len(row)] or [10]
        )
        ws.column_dimensions[get_column_letter(col)].width = min(max_len + 4, 40)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


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
