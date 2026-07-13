# -*- coding: utf-8 -*-
"""
Generador de archivos .xlsx SIN dependencias externas (solo stdlib).

Un .xlsx es un ZIP con varios XML dentro (formato OpenXML). Aquí se
construye a mano el mínimo necesario para un libro con varias hojas de
datos (texto y números), que es justo lo que necesitan las estadísticas
del bot. No usa fórmulas (los cálculos ya vienen hechos desde Python),
así que el archivo es 100% valores y lo abre Excel, LibreOffice y Google
Sheets sin problema.

Uso:
    from excel_export import construir_xlsx
    data = construir_xlsx([
        ("Resumen", [["Métrica", "Valor"], ["Total", 123], ...]),
        ("Por par", [[...], ...]),
    ])
    # data es bytes -> se puede escribir a disco o mandar por HTTP
"""
import zipfile
import io
import datetime
from xml.sax.saxutils import escape


def _col_letra(idx):
    """0 -> A, 1 -> B, ... 26 -> AA, etc."""
    s = ""
    idx += 1
    while idx > 0:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _es_numero(v):
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float))


def _celda_xml(col_idx, fila_idx, valor):
    ref = f"{_col_letra(col_idx)}{fila_idx + 1}"
    if valor is None:
        valor = ""
    if _es_numero(valor):
        return f'<c r="{ref}"><v>{valor}</v></c>'
    # Texto: usamos inline string para no gestionar la tabla de strings
    # compartidos (más simple y robusto para este volumen de datos).
    txt = escape(str(valor))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>'


def _hoja_xml(filas):
    partes = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    partes.append('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
    partes.append('<sheetData>')
    for i, fila in enumerate(filas):
        partes.append(f'<row r="{i + 1}">')
        for j, val in enumerate(fila):
            partes.append(_celda_xml(j, i, val))
        partes.append('</row>')
    partes.append('</sheetData>')
    partes.append('</worksheet>')
    return "".join(partes)


def _sanear_nombre(nombre, usados):
    """Nombres de hoja: máx 31 chars, sin : \\ / ? * [ ], únicos."""
    limpio = nombre
    for c in ':\\/?*[]':
        limpio = limpio.replace(c, ' ')
    limpio = limpio.strip()[:31] or "Hoja"
    base = limpio
    n = 2
    while limpio.lower() in usados:
        sufijo = f" {n}"
        limpio = (base[:31 - len(sufijo)] + sufijo)
        n += 1
    usados.add(limpio.lower())
    return limpio


def construir_xlsx(hojas):
    """hojas = lista de (nombre_hoja, filas), donde filas es lista de
    listas (cada sublista = una fila de celdas). Devuelve bytes del
    .xlsx."""
    usados = set()
    hojas_norm = [(_sanear_nombre(nombre, usados), filas) for nombre, filas in hojas]
    n = len(hojas_norm)

    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                     '<Default Extension="xml" ContentType="application/xml"/>',
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    for i in range(n):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                             f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append('</Types>')

    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')

    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
          '<sheets>']
    for i, (nombre, _f) in enumerate(hojas_norm):
        wb.append(f'<sheet name="{escape(nombre)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>')
    wb.append('</sheets></workbook>')

    wb_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(n):
        wb_rels.append(f'<Relationship Id="rId{i + 1}" '
                       f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                       f'Target="worksheets/sheet{i + 1}.xml"/>')
    wb_rels.append('</Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content_types))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
        for i, (_nombre, filas) in enumerate(hojas_norm):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _hoja_xml(filas))
    return buf.getvalue()
