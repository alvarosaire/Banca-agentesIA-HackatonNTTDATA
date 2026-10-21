"""Genera los archivos de prueba del catálogo de casos de uso del Agente 1 en normas/casos_uso/.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/generar_casos_uso.py [--solo-areas]

Todos los documentos son FICTICIOS (entidades "SBS — DOCUMENTO SINTÉTICO PARA DEMO" y
"Banco Andino (ficticio) — DOCUMENTO SINTÉTICO PARA DEMO") y usan códigos que no existen en normas/.
Los PDF se imprimen con Playwright + el Chrome instalado, con la misma ficha que los PDF de normas/
(título, líneas etiqueta / valor, aviso, "Artículo N. Título" + texto) y se verifica que
parsing.parsear + separar_frontmatter leen bien la ficha de cada uno. Catálogo: normas/CASOS_DE_USO.md.
"""
from __future__ import annotations

import base64
import html
import io
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.normas.parsing import parsear, separar_frontmatter  # noqa: E402

NORMAS = ROOT / "normas"
OUT = NORMAS / "casos_uso"
SBS = "SBS — DOCUMENTO SINTÉTICO PARA DEMO"
BANCO = "Banco Andino (ficticio) — DOCUMENTO SINTÉTICO PARA DEMO"
AVISO = "Aviso: documento ficticio creado solo para pruebas del hackathon. No es una norma real."

CSS = """
body { font-family: Arial, Helvetica, sans-serif; font-size: 11pt; color: #111; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 14pt 0; }
.lbl { font-size: 9pt; font-weight: bold; color: #555; margin: 6pt 0 0 0; }
.val { margin: 0 0 2pt 0; }
.aviso { margin: 14pt 0; padding: 6pt; border: 1px solid #c77700; color: #8a5200; font-size: 9.5pt; }
h2 { font-size: 12pt; margin: 12pt 0 4pt 0; }
p { margin: 0 0 6pt 0; line-height: 1.35; }
"""


# ---------------------------------------------------------------------------- definición de los documentos
def ficha(codigo, entidad, emision, vigencia, estado, version, reemplaza="Ninguna"):
    """Lista ordenada (etiqueta, valor) como en los PDF de normas/. valor None = la línea se omite."""
    filas = [("Código", codigo), ("Entidad emisora", entidad), ("Fecha de emisión", emision),
             ("Fecha de vigencia", vigencia), ("Estado", estado), ("Versión", version), ("Reemplaza a", reemplaza)]
    return [(k, v) for k, v in filas if v is not None]


DOCS = {
    # B5 — derogación por cláusula, sin "Reemplaza a" en la ficha
    "CU05_res-sint-2026-101_deroga_por_clausula.pdf": dict(
        titulo="Reglamento de Gestión del Riesgo de Crédito (texto actualizado)",
        ficha=ficha("RES-SINT-2026-101", SBS, "2026-02-10", "2026-04-01", "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Marco de gestión", "Las entidades contarán con políticas, procedimientos y un Comité de Riesgos que "
                                 "apruebe el apetito de riesgo crediticio y lo revise periódicamente."),
            ("Responsabilidades", "El Directorio aprueba la política de riesgo crediticio; la Gerencia la "
                                  "implementa y Auditoría Interna evalúa su cumplimiento."),
            ("Excepciones", "Toda excepción a la política debe ser aprobada por el Comité de Riesgos y quedar "
                            "documentada con su sustento técnico."),
        ],
        disposiciones=["Primera. Deróguese la RES-SINT-2023-009 a partir de la entrada en vigencia del presente "
                       "reglamento.",
                       "Segunda. El presente reglamento entra en vigencia el 1 de abril de 2026."]),
    # B6 — reemplazo inválido: declara reemplazar a una norma POSTERIOR (RES-SINT-2023-018, vigencia 2023-10-01)
    "CU06_res-sint-2022-102_reemplazo_invalido.pdf": dict(
        titulo="Disposiciones sobre Tasación de Inmuebles en Garantía",
        ficha=ficha("RES-SINT-2022-102", SBS, "2022-03-01", "2022-06-01", "vigente", "1.0", "RES-SINT-2023-018"),
        articulos=[
            ("Perito tasador", "La tasación de inmuebles que respaldan créditos debe ser realizada por un perito "
                               "inscrito en el registro de peritos de la entidad supervisora."),
            ("Contenido del informe", "El informe de tasación describirá el inmueble, su estado de conservación, "
                                      "la metodología utilizada y el valor comercial y de realización."),
            ("Independencia", "El perito no podrá tener vínculo con el deudor ni con el área comercial que "
                              "origina el crédito."),
        ]),
    # B7 — estado no declarado ("—") y vigencia futura -> por_entrar_en_vigencia inferido
    "CU07_res-sint-2026-103_vigencia_futura_sin_estado.pdf": dict(
        titulo="Reglamento de Canales Digitales para la Contratación de Créditos",
        ficha=ficha("RES-SINT-2026-103", SBS, "2026-09-01", "2027-01-01", "—", "1.0", "Ninguna"),
        articulos=[
            ("Objeto", "Establecer las condiciones mínimas de seguridad y transparencia para contratar créditos "
                       "por canales digitales."),
            ("Identificación del cliente", "La entidad verificará la identidad del cliente mediante mecanismos de "
                                           "autenticación reforzada antes de la firma del contrato."),
            ("Información previa", "Antes de contratar, el cliente debe acceder a la hoja resumen y al cronograma "
                                   "de pagos en un formato que pueda descargar."),
        ]),
    # B8 — estado no declarado ("—") y vigencia pasada -> vigente inferido
    "CU08_pol-dig-2024-104_vigencia_pasada_sin_estado.pdf": dict(
        titulo="Política de Firma Electrónica de Contratos de Crédito",
        ficha=ficha("POL-DIG-2024-104", BANCO, "2024-04-01", "2024-05-01", "—", "1.0", "Ninguna"),
        articulos=[
            ("Alcance", "Aplica a los contratos de crédito minorista que el Banco Andino suscribe por canales "
                        "digitales."),
            ("Firma", "Los contratos se firman con firma electrónica avanzada vinculada a la identidad verificada "
                      "del cliente."),
            ("Custodia", "La constancia de firma se archiva junto al expediente digital del crédito."),
        ]),
    # C14 — valores equivalentes en distinta unidad (1 día = 24 horas de 03; 120 meses = 10 años de 31)
    "CU14_pro-ros-2026-105_unidades_equivalentes.pdf": dict(
        titulo="Procedimiento de Comunicación de Operaciones Sospechosas (actualizado)",
        ficha=ficha("PRO-ROS-2026-105", BANCO, "2026-05-04", "2026-06-01", "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Detección", "El analista comunica la alerta al Oficial de Cumplimiento mediante el formulario "
                          "interno de alertas."),
            ("Plazo de reporte", "El Oficial de Cumplimiento reportará la operación sospechosa a la unidad de "
                                 "inteligencia financiera en un plazo máximo de un (1) día calendario desde que "
                                 "se califica como sospechosa."),
            ("Conservación", "El expediente de evaluación de cada crédito vinculado a una alerta se conservará "
                             "por un mínimo de 120 meses desde la cancelación del crédito."),
        ]),
    # C15 — días calendario frente a días hábiles (31: quinto día hábil)
    "CU15_pro-crc-2026-106_habiles_vs_calendario.pdf": dict(
        titulo="Procedimiento de Envío del Reporte de Deudores",
        ficha=ficha("PRO-CRC-2026-106", BANCO, "2026-04-06", "2026-05-01", "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Responsable", "El área de Riesgos genera el reporte mensual de clasificación de deudores."),
            ("Plazo de envío", "El reporte de clasificación de deudores se enviará a la central de riesgos a más "
                               "tardar el día 20 calendario del mes siguiente."),
            ("Control", "Antes del envío, el Jefe de Riesgos valida la consistencia del reporte."),
        ]),
    # C16 — misma regla y mismo valor que RES-SINT-2024-001 (40% y 10 años)
    "CU16_pol-exp-2026-107_mismo_valor.pdf": dict(
        titulo="Política de Evaluación y Conservación de Expedientes de Consumo",
        ficha=ficha("POL-EXP-2026-107", BANCO, "2026-03-02", "2026-04-01", "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Relación cuota-ingreso", "La cuota mensual total de las obligaciones del cliente no podrá exceder "
                                       "el 40% de su ingreso mensual neto demostrable."),
            ("Conservación", "El expediente de evaluación de cada crédito debe conservarse por un mínimo de 10 "
                             "años desde la cancelación del crédito."),
            ("Responsables", "El analista de créditos arma el expediente y el área de Archivo lo custodia."),
        ]),
    # D19 — mismo código y versión que 05 (RES-SINT-2023-018 v1.0) con contenido distinto (corrección)
    "CU19_res-sint-2023-018_corregida_misma_version.pdf": dict(
        titulo="Reglamento de Créditos Hipotecarios para Vivienda",
        ficha=ficha("RES-SINT-2023-018", SBS, "2023-06-30", "2023-10-01", "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Relación préstamo-valor", "El monto financiado no excederá el 90% del valor de tasación del inmueble."),
            ("Plazo máximo", "El plazo máximo del crédito hipotecario es de 30 años."),
            ("Tasación", "La tasación debe ser realizada por un perito inscrito y tendrá una vigencia máxima de "
                         "12 meses."),
            ("Seguros", "El inmueble debe contar con seguro contra todo riesgo y el deudor con seguro de "
                        "desgravamen, ambos endosados a favor de la entidad (texto corregido)."),
        ]),
    # D21 — ficha incompleta: falta la línea "Fecha de vigencia"
    "CU21_ci-2026-108_ficha_incompleta.pdf": dict(
        titulo="Circular Interna: Formato Único de Solicitud de Crédito",
        ficha=ficha("CI-2026-108", BANCO, "2026-07-01", None, "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Formato", "Toda solicitud de crédito minorista se registra en el Formato Único de Solicitud."),
            ("Firma", "El formato debe estar firmado por el cliente y por el asesor que lo atiende."),
            ("Registro", "El asesor digitaliza el formato y lo adjunta al expediente del crédito."),
        ]),
    # D22 — fechas incoherentes: vigencia anterior a la emisión
    "CU22_ci-2026-109_fechas_incoherentes.pdf": dict(
        titulo="Circular Interna: Sesiones del Comité de Créditos",
        ficha=ficha("CI-2026-109", BANCO, "2026-06-10", "2026-01-01", "vigente", "1.0", "Ninguna"),
        articulos=[
            ("Convocatoria", "El Comité de Créditos se reúne por convocatoria de su presidente."),
            ("Quórum", "Las sesiones requieren la presencia de la mayoría de sus miembros."),
            ("Actas", "Los acuerdos constan en actas firmadas por los asistentes."),
        ]),
    # E25 — ficha correcta pero SIN artículos reconocibles
    "CU25_pol-eti-2026-113_sin_articulos.pdf": dict(
        titulo="Código de Ética Comercial del Asesor de Créditos",
        ficha=ficha("POL-ETI-2026-113", BANCO, "2026-02-02", "2026-03-01", "vigente", "1.0", "Ninguna"),
        prosa=[
            "El asesor de créditos actúa con honestidad, transparencia y respeto hacia el cliente en todo momento.",
            "No ofrece productos que el cliente no necesita ni oculta información sobre el costo del crédito.",
            "Ante un posible conflicto de interés, el asesor lo comunica a su jefatura antes de continuar con la "
            "evaluación de la solicitud.",
        ]),
}

# D20 — sin ficha ni frontmatter: solo texto (metadatos por LLM)
SIN_FICHA = ("CU20_sin_ficha_solo_texto.pdf", dict(
    titulo="Circular Interna CI-2026-114 del Banco Andino (ficticio): Atención Preferente en Agencias",
    articulos=[
        ("Objeto", "Ordenar la atención preferente de adultos mayores, gestantes y personas con discapacidad en "
                   "las agencias del Banco Andino (ficticio)."),
        ("Ventanilla preferente", "Cada agencia habilita una ventanilla preferente señalizada y visible."),
        ("Supervisión", "El jefe de agencia supervisa que la atención preferente se cumpla en todo el horario."),
    ]))

# E23 — DOCX con ficha
DOCX = ("CU23_pol-tec-2026-110_ficha.docx", dict(
    titulo="Política de Seguridad de la Información en Créditos",
    ficha=ficha("POL-TEC-2026-110", BANCO, "2026-05-11", "2026-06-01", "vigente", "1.0", "Ninguna"),
    articulos=[
        ("Alcance", "Aplica a los sistemas que soportan la evaluación y el desembolso de créditos."),
        ("Accesos", "El acceso a los sistemas de créditos se otorga por perfil y con autorización de la jefatura."),
        ("Incidentes", "Todo incidente de seguridad se reporta de inmediato al área de Seguridad de la Información."),
    ]))

# E24 — TXT con ficha y MD con frontmatter YAML
TXT = ("CU24a_pro-ate-2026-111_ficha.txt", dict(
    titulo="Procedimiento de Atención de Reclamos de Crédito",
    ficha=ficha("PRO-ATE-2026-111", BANCO, "2026-04-13", "2026-05-01", "vigente", "1.0", "Ninguna"),
    articulos=[
        ("Recepción", "Los reclamos se reciben en agencias, banca telefónica y canales digitales."),
        ("Registro", "Cada reclamo se registra con un número de seguimiento que se entrega al cliente."),
        ("Respuesta", "La respuesta se comunica por escrito al cliente por el medio que eligió."),
    ]))
MD = ("CU24b_pro-rec-2026-112_frontmatter.md", dict(
    titulo="Procedimiento de Recuperación de Garantías",
    yaml={"nombre_norma": "Procedimiento de Recuperación de Garantías", "codigo": "PRO-REC-2026-112",
          "entidad_emisora": BANCO, "fecha_emision": "2026-03-16", "fecha_vigencia": "2026-04-01",
          "estado": "vigente", "version": "1.0", "reemplaza_a": ""},
    articulos=[
        ("Inicio", "La recuperación de garantías se inicia cuando el área de Cobranzas agota la gestión amistosa."),
        ("Evaluación legal", "El área Legal evalúa la vía de ejecución más adecuada para cada garantía."),
        ("Informe", "El resultado de la recuperación se informa al Comité de Riesgos."),
    ]))

# G32 — PDF con fila "Áreas de aplicación" en la ficha (áreas declaradas)
AREAS_PDF = ("CU32_pol-pub-2026-132_areas_en_ficha.pdf", dict(
    titulo="Política de Publicidad y Comunicación Comercial de Créditos",
    ficha=ficha("POL-PUB-2026-132", BANCO, "2026-06-01", "2026-07-01", "vigente", "1.0", "Ninguna")
    + [("Áreas de aplicación", "Marketing, Call Center, Legal")],
    articulos=[
        ("Alcance", "Aplica a toda pieza publicitaria y guion comercial que ofrezca créditos del Banco Andino "
                    "(ficticio), en cualquier canal."),
        ("Información veraz", "Toda publicidad de créditos informa la tasa de costo efectivo anual de forma "
                              "visible y no omite condiciones relevantes para el cliente."),
        ("Revisión previa", "El área Legal revisa y aprueba cada campaña y cada guion del centro de llamadas "
                            "antes de su difusión."),
    ]))
# G33 — MD con `areas_aplica` en el frontmatter (variantes que se normalizan: legal, RR.HH.)
AREAS_MD = ("CU33_pro-cob-2026-133_areas_frontmatter.md", dict(
    titulo="Procedimiento de Cobranza Responsable",
    yaml={"nombre_norma": "Procedimiento de Cobranza Responsable", "codigo": "PRO-COB-2026-133",
          "entidad_emisora": BANCO, "fecha_emision": "2026-06-08", "fecha_vigencia": "2026-07-01",
          "estado": "vigente", "version": "1.0", "reemplaza_a": "", "areas_aplica": "Cobranzas, legal, RR.HH."},
    articulos=[
        ("Trato al cliente", "La gestión de cobranza se realiza con respeto, sin amenazas ni mensajes a terceros."),
        ("Horario", "Las llamadas de cobranza se realizan solo en el horario que autoriza la política comercial."),
        ("Capacitación", "Recursos Humanos capacita al personal de cobranzas en trato responsable cada año."),
    ]))

NO_SUBIR = ("CU28_notas_internas_NO_SUBIR.md",
            "# Notas internas del equipo (NO SUBIR)\n\n"
            "> Aviso: documento ficticio creado solo para pruebas del hackathon. No es una norma real.\n\n"
            "Material de trabajo del equipo: el sistema debe rechazar este archivo por su nombre (NO_SUBIR).\n\n"
            "## Artículo 1. Prueba\nEste texto nunca debe indexarse.\n")


# ---------------------------------------------------------------------------- render
def html_doc(d: dict) -> str:
    e = html.escape
    partes = [f"<h1>{e(d['titulo'])}</h1>"]
    for k, v in d.get("ficha", []):
        partes.append(f'<p class="lbl">{e(k)}</p><p class="val">{e(v)}</p>')
    partes.append(f'<p class="aviso">{e(AVISO)}</p>')
    for i, (t, txt) in enumerate(d.get("articulos", []), start=1):
        partes.append(f"<h2>Artículo {i}. {e(t)}</h2><p>{e(txt)}</p>")
    if d.get("disposiciones"):
        partes.append("<h2>Disposiciones finales</h2>" + "".join(f"<p>{e(x)}</p>" for x in d["disposiciones"]))
    for p in d.get("prosa", []):
        partes.append(f"<p>{e(p)}</p>")
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(partes)}</body></html>"


def lineas_texto(d: dict) -> list[str]:
    out = [d["titulo"]]
    for k, v in d.get("ficha", []):
        out += [k, v]
    out.append(AVISO)
    for i, (t, txt) in enumerate(d.get("articulos", []), start=1):
        out += [f"Artículo {i}. {t}", txt]
    return out


def imagen_escaneada_png() -> bytes:
    """Página 'escaneada': una imagen (sin capa de texto) con el aviso dibujado."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("L", (1240, 1754), 236)
    dr = ImageDraw.Draw(img)
    dr.text((90, 90), "DOCUMENTO ESCANEADO (FICTICIO) - SIN CAPA DE TEXTO", fill=40)
    dr.text((90, 130), "Aviso: documento ficticio creado solo para pruebas del hackathon.", fill=60)
    y = 220
    for i in range(38):  # renglones simulados
        dr.rectangle((90, y, 90 + 900 + (i * 37) % 160, y + 9), fill=120)
        y += 36
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def pdf(page, html_str: str, destino: Path, cabecera: str | None) -> None:
    page.set_content(html_str, wait_until="load")
    hdr = (f"<div style='font-size:8px;width:100%;text-align:right;margin-right:1.5cm;color:#666'>"
           f"{html.escape(cabecera)}</div>") if cabecera else "<span></span>"
    ftr = ("<div style='font-size:8px;width:100%;text-align:center;color:#666'>Página "
           "<span class='pageNumber'></span> de <span class='totalPages'></span></div>")
    page.pdf(path=str(destino), format="A4", print_background=True, display_header_footer=True,
             header_template=hdr, footer_template=ftr,
             margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"})


def xlsx_minimo(destino: Path) -> None:
    """Hoja de cálculo mínima (sin openpyxl): debe ser rechazada por formato no soportado."""
    archivos = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Limites" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Documento ficticio para pruebas del hackathon (no es una norma real)</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Limite cuota-ingreso</t></is></c><c r="B2"><v>40</v></c></row></sheetData></worksheet>',
    }
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for n, c in archivos.items():
            z.writestr(n, c)


def jpg(destino: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 500), (245, 245, 245))
    dr = ImageDraw.Draw(img)
    dr.text((40, 40), "FOTO DE UNA NORMA (FICTICIA) - formato no soportado", fill=(30, 30, 30))
    dr.text((40, 80), "Aviso: documento ficticio creado solo para pruebas del hackathon.", fill=(80, 80, 80))
    img.save(destino, "JPEG", quality=85)


def docx(destino: Path, d: dict) -> None:
    import docx as pydocx

    doc = pydocx.Document()
    doc.add_heading(d["titulo"], level=1)
    for k, v in d["ficha"]:
        doc.add_paragraph(k).runs[0].bold = True
        doc.add_paragraph(v)
    doc.add_paragraph(AVISO)
    for i, (t, txt) in enumerate(d["articulos"], start=1):
        doc.add_heading(f"Artículo {i}. {t}", level=2)
        doc.add_paragraph(txt)
    doc.save(str(destino))


def md(destino: Path, d: dict) -> None:
    fm = "\n".join(f"{k}: {v}" for k, v in d["yaml"].items())
    cuerpo = [f"# {d['titulo']}", "", f"> **{AVISO.split(':')[0]}:**{AVISO.split(':', 1)[1]}", ""]
    for i, (t, txt) in enumerate(d["articulos"], start=1):
        cuerpo += [f"## Artículo {i}. {t}", txt, ""]
    destino.write_text(f"---\n{fm}\n---\n\n" + "\n".join(cuerpo), encoding="utf-8")


# ---------------------------------------------------------------------------- verificación de la ficha
def verificar(path: Path, esperado: dict | None) -> str:
    try:
        paginas, basura, motivo = parsear(path)
    except ValueError as e:
        return f"rechazado por parsear: {e}"
    fm, _ = separar_frontmatter("\n".join(t for _, t in paginas))
    if esperado is None:
        return f"ficha={'sí' if fm else 'no'} texto_insuficiente={basura} {motivo}".strip()
    malos = {k: (fm.get(k), v) for k, v in esperado.items() if fm.get(k) != v}
    if malos:
        raise SystemExit(f"FICHA MAL LEÍDA en {path.name}: {malos}")
    return f"ficha OK ({fm.get('codigo')} v{fm.get('version')}) texto_insuficiente={basura} {motivo}".strip()


def esperado_de(fic: list[tuple[str, str]]) -> dict:
    clave = {"Código": "codigo", "Entidad emisora": "entidad_emisora", "Fecha de emisión": "fecha_emision",
             "Fecha de vigencia": "fecha_vigencia", "Estado": "estado", "Versión": "version",
             "Reemplaza a": "reemplaza_a", "Áreas de aplicación": "areas_aplica"}
    return {clave[k]: (None if v in ("Ninguna", "—") else v) for k, v in fic}


def generar_areas(page, informe: list) -> None:
    """G32-G33: áreas de aplicación declaradas (ficha del PDF y frontmatter del MD)."""
    nombre, d = AREAS_PDF
    cod, ver = dict(d["ficha"])["Código"], dict(d["ficha"])["Versión"]
    pdf(page, html_doc(d), OUT / nombre, f"{cod} — v{ver}")
    informe.append((nombre, verificar(OUT / nombre, esperado_de(d["ficha"]))))
    nombre, d = AREAS_MD
    md(OUT / nombre, d)
    informe.append((nombre, verificar(OUT / nombre, {"codigo": d["yaml"]["codigo"],
                                                     "areas_aplica": d["yaml"]["areas_aplica"]})))


def main() -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    informe = []
    if "--solo-areas" in sys.argv:  # solo CU32/CU33 (no regenera los demás archivos)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            generar_areas(browser.new_page(), informe)
            browser.close()
        print(f"Generados en {OUT}:")
        for n, r in informe:
            print(f"  {n:<55} {r}")
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        for nombre, d in DOCS.items():
            dest = OUT / nombre
            cod = dict(d["ficha"])["Código"]
            ver = dict(d["ficha"])["Versión"]
            pdf(page, html_doc(d), dest, f"{cod} — v{ver}")
            informe.append((nombre, verificar(dest, esperado_de(d["ficha"]))))
        nombre, d = SIN_FICHA
        pdf(page, html_doc(d), OUT / nombre, None)
        informe.append((nombre, verificar(OUT / nombre, None)))
        b64 = base64.b64encode(imagen_escaneada_png()).decode()
        pdf(page, f"<!doctype html><html><body style='margin:0'><img src='data:image/png;base64,{b64}' "
                  f"style='width:100%'></body></html>", OUT / "CU26_escaneado_sin_texto.pdf", None)
        informe.append(("CU26_escaneado_sin_texto.pdf", verificar(OUT / "CU26_escaneado_sin_texto.pdf", None)))
        generar_areas(page, informe)
        browser.close()

    nombre, d = DOCX
    docx(OUT / nombre, d)
    informe.append((nombre, verificar(OUT / nombre, esperado_de(d["ficha"]))))
    nombre, d = TXT
    (OUT / nombre).write_text("\n".join(lineas_texto(d)) + "\n", encoding="utf-8")
    informe.append((nombre, verificar(OUT / nombre, esperado_de(d["ficha"]))))
    nombre, d = MD
    md(OUT / nombre, d)
    informe.append((nombre, verificar(OUT / nombre, {"codigo": d["yaml"]["codigo"], "version": "1.0"})))

    # D18 — mismo contenido que 05 con otro nombre (copia byte a byte)
    shutil.copyfile(NORMAS / "05_res-sint-2023-018.pdf", OUT / "CU18_copia_de_05_otro_nombre.pdf")
    informe.append(("CU18_copia_de_05_otro_nombre.pdf", "copia byte a byte de 05_res-sint-2023-018.pdf"))
    xlsx_minimo(OUT / "CU27_tabla_no_soportada.xlsx")
    jpg(OUT / "CU27_foto_no_soportada.jpg")
    informe += [("CU27_tabla_no_soportada.xlsx", verificar(OUT / "CU27_tabla_no_soportada.xlsx", None)),
                ("CU27_foto_no_soportada.jpg", verificar(OUT / "CU27_foto_no_soportada.jpg", None))]
    (OUT / NO_SUBIR[0]).write_text(NO_SUBIR[1], encoding="utf-8")
    informe.append((NO_SUBIR[0], "nombre con NO_SUBIR (debe rechazarse antes de leerlo)"))

    print(f"Generados en {OUT}:")
    for n, r in informe:
        print(f"  {n:<55} {r}")


if __name__ == "__main__":
    main()
