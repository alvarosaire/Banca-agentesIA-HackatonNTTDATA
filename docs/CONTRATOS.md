# Contratos entre módulos (caso B2 normativa)

## Módulos compartidos (no cambiar la firma sin avisar)
- `app/normas/db.py`: `db.query(sql, params)`, `db.execute(sql, params)`, `db.connect()`, `db.TEMAS`, `db.now()`. Esquema en `SCHEMA`.
- `app/normas/vector.py`: `get_store().upsert(records)`, `.search(text, top_k, filter, rerank_top_n) -> [Hit]`, `fecha_int("2025-03-01") -> 20250301`.
- `app/agents/*`: `Agent`, `Flow`, `engine`, `@tool`, `Run.meta = {"agente","canal","usuario"}` (se guarda en la tabla `trazas`).
- `app/llm_client.py`: `get_llm().chat(messages, tools=None, temperature=0)`.

## Registro en Pinecone
`id = "{codigo}_{version}_art-{n}"` (disposiciones finales: `_df-{k}`; artículo largo partido: `_p{k}`), sanitizado a
`[A-Za-z0-9._-]`. Es determinístico: re-subir/re-indexar hace upsert y no duplica.
`text` = texto con prefijo `[<nombre_norma> v<version> · Art. <n>]`.
Metadatos: `doc_id, codigo, nombre_norma, entidad, tipo (externa|interna), tema (tema principal del doc), temas
(lista de temas de las afirmaciones de ese artículo; se omite si no hay), articulo, pagina, fecha_emision (int),
fecha_vigencia (int), estado, version, reemplaza_a`.
`estado` en el índice: `vigente | por_entrar_en_vigencia | reemplazada | pendiente_revision | rechazada | procesando`.
El Agente 2 filtra `estado = vigente`, así que nunca usa normas pendientes de revisión, rechazadas o en proceso.

## Frente A — Agente 1: ingesta y validación (`app/normas/ingesta.py`, `validacion.py`, `api_documentos.py`)
Pipeline (cada paso deja una fila en `trazas_ingesta`): recepcion (archivo, sha256) -> metadatos (frontmatter o LLM
con confianza 0-1 por campo) -> chunks -> afirmaciones (1 llamada LLM por documento) -> indexacion -> candidatos ->
comparacion -> discrepancia_confirmada / discrepancia_descartada (motivo) -> publicacion -> decision_usuario.

| Ruta | Entrada | Salida |
|---|---|---|
| `POST /api/documentos` | multipart `archivo` (PDF/DOCX/TXT/MD) | **202** `{doc_id, codigo, estado_ingesta, duplicado, mensaje}`; procesa en un hilo. **400** si el nombre empieza con `00_GUIA` o contiene `NO_SUBIR`, o formato no soportado. Duplicado (mismo sha256): `duplicado: true`, no reindexa |
| `GET /api/documentos` | — | `[{doc_id, codigo, nombre_norma, entidad, tipo_fuente, tema, fecha_emision, fecha_vigencia, estado, estado_inferido, version, reemplaza_a, reemplazada_por, publicado, requiere_confirmacion, estado_ingesta, etapa, mensaje_ingesta, n_chunks, run_id, created_at}]` |
| `GET /api/documentos/{doc_id}/progreso` | — | `{doc_id, etapa, mensaje, n_discrepancias_altas, estado, publicado, estado_ingesta}`; `etapa`: `recibido | extrayendo_metadatos | indexando | comparando | informe | listo | requiere_revision | error` |
| `GET /api/documentos/{doc_id}` | — | lo de la lista + `archivo, sha256, metadatos_json` (objeto: `campos, fuente, origen, faltantes, baja_confianza, advertencias, deroga`) + `confianza_json` (objeto campo -> 0-1) + `chunks:[{chunk_id, articulo, seccion, pagina, texto}]` + `afirmaciones:[{id, chunk_id, articulo, tema, valor, unidad, condicion, sentido, cita_literal}]` + `discrepancias` (formato de abajo) |
| `GET /api/documentos/{doc_id}/traza` | — | `[{id, doc_id, paso, entrada, salida, modelo, tokens, latencia_ms, timestamp, usuario}]` ordenado (entrada/salida como objeto JSON cuando aplica) |
| `POST /api/documentos/{doc_id}/decision` | `{decision: "aprobar_publicar" | "publicar_igual" | "rechazar", usuario, comentario}` | `{ok, estado, publicado}` |
| `GET /api/discrepancias?doc_id=&tema=&estado=&severidad=` | — | `[{id, tema, severidad, tipo, doc_a, chunk_a, art_a, version_a, cita_a, doc_b, chunk_b, art_b, version_b, cita_b, prevalece, motivo, explicacion, estado, estado_revision, comentario, run_id, created_at, nombre_a, nombre_b}]` |
| `POST /api/discrepancias/{id}/revisar` | `{decision: "confirmada" | "descartada", comentario}` | `{ok}` |

Valores:
- `documentos.estado`: `vigente | por_entrar_en_vigencia | reemplazada | pendiente_revision | rechazada | procesando`.
  `estado_inferido = 1` si el documento no declaraba estado (se infiere por fechas). `tipo_fuente`: `externa` si la
  entidad es reguladora (SBS, SMV, BCRP, UIF, SUNAT, MEF, Congreso, Ministerio...), si no `interna`.
- `estado_ingesta`: `procesando | indexado | requiere_revision | error`. `requiere_revision` = poco texto o sin
  artículos: **no se indexa**; `mensaje_ingesta` explica qué revisar.
- `requiere_confirmacion = 1`: falta un metadato obligatorio, alguno tiene confianza < 0.7, o hay advertencias
  (fecha_vigencia < fecha_emision, reemplazo de una norma con fecha posterior, que no se aplica).
- Discrepancia `severidad`: `alta | media | informativa`; `tipo`: `contradice | mas_estricta | actualiza`;
  `estado`: `abierta | resuelta`; `prevalece` = doc_id que prevalece y `motivo` = por qué (jerarquía calculada en código).
  - ALTA: interna menos estricta (o distinta) que una externa vigente. Prevalece la externa.
  - MEDIA: interna vs interna, o externa vs externa: prevalece la más reciente (vigencia, luego emisión).
  - INFORMATIVA: interna más estricta que la externa (`mas_estricta`), o diferencia contra la versión que la norma
    nueva reemplaza (`actualiza`).
- Control humano: si el documento nuevo es la norma **interna** de una discrepancia ALTA, queda
  `estado = pendiente_revision`, `publicado = 0` y sus chunks con `estado = pendiente_revision` en el índice. Si el
  documento nuevo es la **externa**, se publica (prevalece) y la ALTA queda abierta señalando la interna a adecuar.
  `aprobar_publicar` publica y marca sus discrepancias `resuelta`; `publicar_igual` publica y las deja `abierta`
  (el Agente 2 sigue advirtiendo); `rechazar` -> `estado = rechazada`, `publicado = 0`, sus discrepancias `resuelta`.
- Reemplazo: `reemplaza_a` declarado o cláusula de derogación (deróguese / déjese sin efecto / queda sin efecto + código).
  Se aplica al **publicar** la norma nueva: la anterior pasa a `reemplazada` (SQLite e índice), con `reemplazada_por` y
  `estado_actualizado_at`, y sus discrepancias abiertas pasan a `resuelta`. Si la anterior se carga después, queda
  `reemplazada` al cargarse. Nunca se borra nada.
- El Agente 2 (`consultas.py`, `casos.py`) lee `discrepancias` con `tipo = 'contradice'`, `estado_revision != 'descartada'`
  y `estado != 'resuelta'`, usando `doc_a/doc_b/chunk_a/chunk_b`.

## Frente B — Consultas, trazas y dashboard (`app/normas/api_consultas.py`)
| Ruta | Entrada | Salida |
|---|---|---|
| `POST /api/consultas` | `{pregunta, canal, usuario}` | `{respuesta, confianza: alta|media|sin_respaldo, cubierta, citas:[{doc_id, nombre_norma, articulo, version, estado, texto}], advertencias:[str], tema, top_score, run_id, latencia_ms}` |
| `GET /api/trazas?agente=&limit=` | — | `[{run_id, agente, canal, usuario, modelo, entrada, salida, latencia_ms, estado, created_at}]` |
| `GET /api/trazas/{run_id}` | — | fila completa con `pasos` (lista de Step) |
| `GET /api/dashboard` | — | `{top_normas:[{doc_id, nombre_norma, n}], sin_cobertura:[{tema, n, ejemplos}], discrepancias:{total, por_tipo, recientes}, uso:{consultas_total, consultas_hoy, pct_cubiertas, latencia_p50_ms, documentos, chunks}}` |

Canales (clientes delgados, sin lógica): bot de Telegram y Streamlit llaman a estos endpoints vía `API_BASE_URL`.

## Frente C — Chat de casos (`app/normas/api_chat.py`, `app/normas/casos.py`, `app/static/chat.html` en `/chat`)
Interfaz de chat separada (reemplaza a Telegram). El usuario describe o sube (PDF/DOCX/TXT) un caso concreto y el
agente lo evalúa contra las normas cargadas; también responde consultas simples y preguntas de seguimiento.

| Ruta | Entrada | Salida |
|---|---|---|
| `POST /api/chat` | multipart: `session_id`, `mensaje`, `archivo?` | ver abajo |
| `GET /api/chat/ejemplos` | — | `[{id, titulo, texto}]` casos de ejemplo para la demo |
| `GET /api/chat/{session_id}` | — | `[{rol: "usuario"|"agente", contenido, tipo, payload, run_id, created_at}]` |

Respuesta de `POST /api/chat`:
```json
{
  "session_id": "s1", "run_id": "r_1", "tipo": "consulta | caso", "latencia_ms": 9000,
  "respuesta": "texto breve para la burbuja del chat",
  "confianza": "alta | media | sin_respaldo",
  "citas": [{"doc_id", "nombre_norma", "articulo", "version", "estado", "texto"}],
  "advertencias": ["..."],
  "dictamen": null | {
    "resumen_caso": "...",
    "hechos": [{"dato": "Ingreso neto mensual", "valor": "S/ 6,000"}],
    "evaluaciones": [{"regla", "doc_id", "nombre_norma", "articulo", "version", "estado", "cita",
                      "resultado": "cumple | no_cumple | indeterminado", "explicacion"}],
    "calculos": [{"descripcion", "expresion", "resultado"}],
    "conflictos": ["..."], "vacios": ["aspectos del caso sin norma que los regule"],
    "conclusion": "procede | procede_con_observaciones | no_procede | requiere_escalamiento",
    "acciones": ["..."]
  }
}
```

## KPIs (`app/normas/kpis.py`, `app/normas/api_kpis.py`, sección KPIs en `ui/streamlit_app.py`)
Regla: todo sale de datos registrados por los agentes. `valor: null` = **sin datos** (la UI muestra "sin datos").
Cada porcentaje trae `num` y `den` para poder explicar de dónde sale.

`GET /api/kpis?desde=&hasta=&umbral=&tiempo_manual_min=15&granularidad=hora`
(`desde`/`hasta`: fecha `AAAA-MM-DD` o fecha-hora ISO; `granularidad`: `10min | hora | dia`; `umbral` por defecto U2 del backend)
```json
{
  "rango": {"desde": "...", "hasta": "..."}, "backend": "pinecone | local", "umbral": 0.10, "generado_at": "...",
  "calidad": {
    "total_consultas": 40, "por_tipo": {"consulta": 32, "caso": 8},
    "con_cita":           {"valor": 0.85, "num": 34, "den": 40},
    "sin_cobertura":      {"valor": 0.15, "num": 6, "den": 40, "no_encontro": 4, "score_bajo": 2},
    "con_advertencia":    {"valor": 0.40, "num": 16, "den": 40, "conflicto": 9, "obsoleta": 11},
    "confianza_promedio": {"valor": 0.71, "n": 34, "distribucion": {"alta": 20, "media": 14, "sin_respaldo": 6}}
  },
  "prevencion": {
    "discrepancias": {"total": 3, "por_severidad": {"alta": 1, "media": 2}, "por_tipo": {"contradice": 1, "actualiza": 2}},
    "normas_obsoletas": {"total": 1, "lista": [{"doc_id", "nombre_norma", "estado", "reemplazada_por"}]},
    "afectados_por_norma": [{"doc_id", "nombre_norma", "n": 2, "afectados": ["SINT-02", "SINT-01"]}]
  },
  "eficiencia": {
    "tiempo_respuesta_s": {"promedio": 12.3, "mediana": 9.8, "n": 40,
                           "por_agente": {"consultas": {"promedio", "mediana", "n"}, "analista_casos": {...}, "validador": {...}}},
    "tiempo_ahorrado": {"estimado": true, "tiempo_manual_min": 15, "consultas": 40, "horas": 9.9,
                        "supuesto": "consultas × (tiempo manual de referencia − tiempo promedio del agente)"}
  },
  "trazabilidad": {
    "trazas_completas": {"valor": 1.0, "num": 44, "den": 44, "incompletas": [{"run_id", "agente", "faltan": ["fragmentos"]}]},
    "top_normas": [{"doc_id", "nombre_norma", "n"}],
    "brechas": [{"tema", "n", "ejemplos": ["..."]}]
  },
  "tendencia": {
    "granularidad": "hora",
    "consultas": [{"t": "2026-10-17T13:00", "n": 12, "sin_cobertura": 2}],
    "brechas_por_tema": [{"tema", "n"}]
  }
}
```

## Áreas de aplicación y usuarios por área
Lista cerrada (`app/normas/areas.py: AREAS`): Riesgos, Créditos, Agencias, Cumplimiento, Marketing, Cobranzas,
Call Center, Operaciones, Auditoría, Legal, Contabilidad, Archivo, RRHH.

- Documentos (`GET /api/documentos`, `GET /api/documentos/{doc_id}`) agregan: `areas_aplica: [str]`,
  `areas_fuente: ficha|frontmatter|llm|manual`, `areas_confianza: float|null`, `areas_requiere_confirmacion: 0|1`.
  Chunks en Pinecone: metadato `areas_aplica` (lista, filtrable con `$in`).
- `GET /api/areas` -> `[str]` (lista cerrada).
- `PUT /api/documentos/{doc_id}/areas` `{areas: [str], usuario}` -> `{ok, areas_aplica}` (actualiza BD + Pinecone,
  `areas_fuente=manual`, `areas_requiere_confirmacion=0`, paso `edicion_areas` en trazas_ingesta).
- `GET /api/usuarios` -> `[{usuario, rol: admin|empleado, area, created_at}]`;
  `POST /api/usuarios` `{usuario, clave, rol, area}` -> `{ok}` (400 si el área no está en la lista o el usuario existe).
- `GET /auth/me` -> `{usuario, rol, area}`; `/auth/check` agrega el header `X-Area`
  (percent-encoded porque las cabeceras HTTP son ASCII: `Cr%C3%A9ditos`; `/api/chat` la decodifica y, si no viene,
  busca el área del usuario `X-Usuario` en la tabla `usuarios`).
- `POST /api/chat`: cada cita agrega `areas_aplica: [str]` y `nota_area: str|null`
  ("Esta norma no es de tu área (<area>), pero te aplica." cuando el área del usuario no está en la norma);
  respuesta agrega `area_usuario`; `dictamen.areas_a_coordinar: [str]` (unión de áreas de las normas citadas).
- `GET /api/kpis` agrega `por_area: {consultas: [{area, n}], brechas: [{area, n, ejemplos}], documentos: [{area, n}]}`
  (consultas y brechas por área del USUARIO que preguntó; documentos por área de aplicación de la norma).
