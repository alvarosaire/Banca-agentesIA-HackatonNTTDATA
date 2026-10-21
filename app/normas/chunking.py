"""Chunking por artículo (y por "Disposición final" / "Disposiciones finales").

- Encabezados reconocidos: "Artículo N", "Art. N", también en Markdown ("## Artículo 2.", "**Art. 3**").
- Un chunk por artículo; si supera 1500 caracteres se parte por párrafos (sufijo _p1, _p2...).
- Las disposiciones finales forman su propio chunk (articulo = "Disposición final" / "Disposiciones finales").
- El texto anterior al primer artículo (título, avisos) NO se indexa: no es una regla citable.
- Sin artículos ni disposiciones -> [] (la ingesta marca requiere_revision y no indexa).
- id determinístico: "{codigo}_{version}_art-{n}" (o "_df-{k}"), sanitizado -> re-subir hace upsert, no duplica.
- `texto_indexado` lleva el prefijo [<nombre_norma> v<version> · Art. <n>].
"""
from __future__ import annotations

import re
from typing import Any

ART_RE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*(?:Art[íi]culo|Art\.)[ \t]+(\d+)",
                    re.IGNORECASE | re.MULTILINE)
DISP_RE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*(Disposici[oó]n(?:es)?[ \t]+"
                     r"(?:complementarias?[ \t]+)?finale?s?)", re.IGNORECASE | re.MULTILINE)
# Encabezados de otro nivel (capítulos, títulos, anexos) cortan el artículo anterior
CORTE_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*(?:Cap[íi]tulo|T[íi]tulo|Anexo|Secci[óo]n)\b",
                      re.IGNORECASE | re.MULTILINE)
MAX_ART = 1500
OVERLAP = 100


def sanitizar(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-") or "x"


def chunk_id(codigo: str, version: str, sufijo: str, parte: int = 0) -> str:
    base = f"{sanitizar(codigo)}_{sanitizar(version)}_{sanitizar(sufijo)}"
    return base if parte == 0 else f"{base}_p{parte}"


def _partir(texto: str, size: int, overlap: int) -> list[str]:
    if len(texto) <= size:
        return [texto]
    out, i = [], 0
    while i < len(texto):
        out.append(texto[i:i + size])
        if i + size >= len(texto):
            break
        i += size - overlap
    return out


def _partir_articulo(texto: str) -> list[str]:
    """Parte un artículo largo por párrafos (o por tamaño si no hay párrafos)."""
    if len(texto) <= MAX_ART:
        return [texto]
    partes, actual = [], ""
    for par in re.split(r"\n\s*\n|\n", texto):
        if actual and len(actual) + len(par) + 1 > MAX_ART:
            partes.append(actual)
            actual = ""
        actual = f"{actual}\n{par}" if actual else par
    if actual:
        partes.append(actual)
    out: list[str] = []
    for p in partes:
        out.extend(_partir(p, MAX_ART, OVERLAP))
    return out


def _pagina_de(offset: int, cortes: list[tuple[int, int]]) -> int:
    pag = cortes[0][1] if cortes else 1
    for ini, p in cortes:
        if offset >= ini:
            pag = p
    return pag


def _limpiar(cuerpo: str) -> str:
    """Quita las marcas de encabezado Markdown (#) al inicio de línea; el resto del texto queda literal."""
    return re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", cuerpo).strip()


def chunkear(paginas: list[tuple[int, str]], meta: dict[str, Any]) -> list[dict[str, Any]]:
    doc_id = meta["doc_id"]
    codigo = meta.get("codigo") or doc_id
    nombre = meta.get("nombre_norma") or codigo
    version = str(meta.get("version") or "1")

    texto, cortes = "", []
    for pag, t in paginas:
        cortes.append((len(texto), pag))
        texto += t.rstrip() + "\n"

    marcas: list[tuple[int, str, str]] = []  # (offset, tipo, numero/etiqueta)
    for m in ART_RE.finditer(texto):
        marcas.append((m.start(), "art", m.group(1)))
    for m in DISP_RE.finditer(texto):
        marcas.append((m.start(), "df", m.group(1)))
    cortes_sec = [m.start() for m in CORTE_RE.finditer(texto)]
    marcas.sort()
    if not marcas:
        return []

    chunks: list[dict[str, Any]] = []
    usados: set[str] = set()
    n_df = 0
    for i, (ini, tipo, etiqueta) in enumerate(marcas):
        fin = marcas[i + 1][0] if i + 1 < len(marcas) else len(texto)
        fin = min([c for c in cortes_sec if ini < c < fin] or [fin])
        cuerpo = _limpiar(texto[ini:fin])
        if not cuerpo:
            continue
        primera = cuerpo.split("\n", 1)[0]
        if tipo == "art":
            articulo = etiqueta
            sufijo = f"art-{etiqueta}"
            etiq_idx = f"Art. {etiqueta}"
            seccion = re.sub(r"^\W*(Art[íi]culo|Art\.)\s+\d+\s*[.:°º\-*]*\s*", "", primera, flags=re.I)
        else:
            n_df += 1
            articulo = " ".join(etiqueta.split()).capitalize()
            sufijo = f"df-{n_df}"
            etiq_idx = articulo
            seccion = articulo
        seccion = seccion.strip(" *.")[:120] or None
        for k, parte in enumerate(_partir_articulo(cuerpo)):
            cid = chunk_id(codigo, version, sufijo, k)
            j = 1
            while cid in usados:  # número de artículo repetido en el documento
                cid = chunk_id(codigo, version, f"{sufijo}-r{j}", k)
                j += 1
            usados.add(cid)
            chunks.append({
                "chunk_id": cid, "doc_id": doc_id, "articulo": articulo, "seccion": seccion,
                "pagina": _pagina_de(ini, cortes), "texto": parte,
                "texto_indexado": f"[{nombre} v{version} · {etiq_idx}] {parte}",
            })
    return chunks
