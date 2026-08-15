from __future__ import annotations
import io
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def dataframe_to_xlsx(df: pd.DataFrame, sheet_name: str = "Resultado") -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.book[sheet_name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.sheet_view.showGridLines = False
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for column in range(1, ws.max_column + 1):
            values = [str(ws.cell(row, column).value or "") for row in range(1, min(ws.max_row, 100) + 1)]
            ws.column_dimensions[get_column_letter(column)].width = min(max(max(map(len, values)) + 2, 12), 42)
        if "Normal" in df.columns:
            idx = list(df.columns).index("Normal") + 1
            for row in range(2, ws.max_row + 1):
                ws.cell(row, idx).number_format = 'R$ #,##0.00'
    return buffer.getvalue()
