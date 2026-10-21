"""Caso B2 Banca: normativa y políticas internas.

Módulos compartidos (no cambiar el contrato sin avisar al equipo):
  db.py      SQLite: documentos, chunks (+FTS5), discrepancias, consultas, trazas
  vector.py  Vector store: Pinecone (índice con embeddings integrados) o índice local de respaldo
  api.py     Router /api que junta api_documentos (ingesta/validación) y api_consultas (consultas/UX)
Ver docs/CONTRATOS.md.
"""
