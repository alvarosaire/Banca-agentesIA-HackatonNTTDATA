# Casos de uso al subir una norma — Agente 1 (ingesta y validación)

Todos los documentos son **ficticios** (entidades «SBS — DOCUMENTO SINTÉTICO PARA DEMO» y «Banco Andino (ficticio) —
DOCUMENTO SINTÉTICO PARA DEMO»). Las rutas son relativas a `normas/`; los archivos nuevos están en `casos_uso/`.
Lo que se describe es el comportamiento **real** del código (`app/normas/ingesta.py`, `validacion.py`, `parsing.py`,
`api_documentos.py`), verificado con `scripts/verificar_casos_uso.py` (namespace `dev`, 2026-10-17).

**Dónde se ve (pestaña 🧾 Agente 1 · Normas):** *Cargar normas* (barra de etapas y mensaje final) · *Normas y detalle*
(tabla con estado de color, publicado, notas «estado inferido / requiere confirmación»; ficha del documento con
banner rojo de revisión y botones; subpestañas Metadatos, Afirmaciones, Discrepancias, Traza) · *Informe de
discrepancias* (tarjetas A | B con cita literal, severidad y «Prevalece»). Arriba siempre: cola «🟠 Pendientes de revisión».

## A. Flujo normal

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 1 | Externa nueva sin conflictos | `02_res-sint-2023-009.pdf` | ninguna | Ficha leída (confianza 1.0), tipo `externa` (SBS), chunks por artículo, sin pares → `vigente`, `publicado=1` | ✅ «listo · estado vigente»; chip verde; «Sin discrepancias registradas» | PASS |
| 2 | Interna nueva sin conflictos | `25_pol-pdp-2024-020.pdf` | ninguna | Igual que 1 con tipo `interna` (Banco Andino) | Igual que 1 | PASS |

## B. Vigencia y reemplazo

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 3 | Nueva versión con `Reemplaza a` | `01_…2019-014.pdf` → `31_res-sint-2024-001.pdf`; `07_pol-rc-2022-001.pdf` → `08_pol-rc-2025-002.pdf` | cargar la antigua primero | Al publicar la nueva, la antigua pasa a `reemplazada` (`reemplazada_por`), se reindexa con ese estado y sus conflictos abiertos se resuelven. Diferencias contra la versión anterior = INFORMATIVA `actualiza` | Antigua con chip gris «reemplazada»; nueva muestra «Reemplaza a»; informe con INFORMATIVAs «prevalece la nueva»; traza paso `reemplazo` | PASS |
| 4 | Versión antigua cargada DESPUÉS | `31_…` → `01_…` | 31 ya publicada | `reemplazante_publicado`: la 01 nace `reemplazada` (por 31), no se compara (`comparacion.omitida`) | 01 gris desde el inicio; traza «RES-SINT-2024-001 (ya cargada) declara reemplazar a esta norma» | PASS |
| 5 | Derogación por cláusula, sin `Reemplaza a` | `02_…` → `casos_uso/CU05_res-sint-2026-101_deroga_por_clausula.pdf` | 02 publicada | «Deróguese la RES-SINT-2023-009» en Disposiciones finales → `reemplaza_a` inferido (fuente `clausula_derogacion`, conf. 1.0); 02 → `reemplazada` | Metadatos: reemplaza_a con fuente cláusula; 02 gris | PASS |
| 6 | Reemplazo inválido (objetivo posterior) | `05_…2023-018.pdf` → `casos_uso/CU06_res-sint-2022-102_reemplazo_invalido.pdf` | 05 publicada | El reemplazo NO se aplica (05 sigue vigente); advertencia en `metadatos_json` y `requiere_confirmacion=1`; la CU06 igual se publica `vigente` | Nota «requiere confirmación»; traza `reemplazo` con `aplicado=false` + advertencia | PASS |
| 7 | Estado no declarado, vigencia futura | `casos_uso/CU07_res-sint-2026-103_vigencia_futura_sin_estado.pdf` (Estado «—», vigencia 2027-01-01) | ninguna | `estado_inferido=1` → `por_entrar_en_vigencia`, publicado. Ojo: no sirve de candidato en comparaciones (filtro `estado=vigente`) | Nota «estado inferido»; ℹ️ «Estado inferido automáticamente» | PASS |
| 8 | Estado no declarado, vigencia pasada | `casos_uso/CU08_pol-dig-2024-104_vigencia_pasada_sin_estado.pdf` | ninguna | `estado_inferido=1` → `vigente` | Igual que 7 con chip verde | PASS |

## C. Contradicciones

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 9 | Interna menos estricta que externa vigente | `31_…` → `09_man-cr-2025-004.pdf` | 31 publicada | ALTA en `cuota_ingreso` (50% vs 40%) y `refinanciacion_plazo` (60 vs 30 días), prevalece 31 → `pendiente_revision`, `publicado=0`, chunks en el índice con estado `pendiente_revision` (el Agente 2 no los usa) | 🟠 «indexado · requiere revisión (2 ALTAS)»; cola de pendientes; banner rojo con Aprobar / Rechazar / Publicar de todas formas; informe A \| B | PASS |
| 10 | Interna vs interna | `08_…`, `17_ci-2025-012.pdf` → `18_ci-2025-013.pdf` | 08 y 17 publicadas | MEDIA vs 08 y 17 (S/ 20,000 vs S/ 30,000); entre internas prevalece la más reciente (18); se publica. (Variante 10b con la fuente `.md`: mismo resultado) | Tarjetas MEDIA «Entre normas internas prevalece la más reciente» | PASS |
| 11 | Interna más estricta | `05_…` → `11_man-hip-2024-006.pdf` | 05 publicada | INFORMATIVA `mas_estricta` (85% vs 90%, 25 vs 30 años), prevalece la interna; se publica | Tarjetas azules INFORMATIVA; chip verde | PASS |
| 12 | Externa posterior cambia valor de externa anterior | `31_…` → `06_res-sint-2025-003.pdf` | 31 publicada | MEDIA externa vs externa (45 vs 30 días), prevalece 06 (más reciente); ambas siguen vigentes; se publica | Tarjeta MEDIA «Entre normas externas prevalece la más reciente» | PASS |
| 13 | Externa nueva con interna ya cargada en conflicto | `09_…` → `31_…` (09 primero) | 09 publicada (sin 31) | ALTA 31↔09 (doc_a=31), prevalece 31, **31 se publica** (la ALTA solo bloquea si la nueva es la interna). La 09 **sigue vigente y publicada** con la ALTA abierta (no pasa a pendiente) | En 31: discrepancias ALTA; la carga muestra «Publicada: esta norma prevalece. Quedan N discrepancias ALTAS abiertas…» | PASS (comportamiento real) |
| 14 | Valores equivalentes en otra unidad | `03_…`, `31_…` → `casos_uso/CU14_pro-ros-2026-105_unidades_equivalentes.pdf` | 03 y 31 publicadas | Normalización en código: 1 día calendario = 24 h (vs 03) y 120 meses = 10 años (vs 31 y 03) → «iguales: sin conflicto», sin LLM | «Sin discrepancias»; Traza → `comparacion` con pares «iguales» | PASS |
| 15 | Días hábiles vs calendario | `31_…` → `casos_uso/CU15_pro-crc-2026-106_habiles_vs_calendario.pdf` | 31 publicada | No se convierten: par `incomparable_dias_habiles_vs_calendario` enviado al LLM; con «día 20 calendario» vs «quinto día hábil» lo confirma → ALTA con nota «(unidades distintas…)», `pendiente_revision` | Banner de revisión; motivo muestra «480 vs 5» (ver bugs) | PASS |
| 16 | Misma regla y mismo valor | `31_…`, `08_…` → `casos_uso/CU16_pol-exp-2026-107_mismo_valor.pdf` | 31 y 08 publicadas | 40% y 10 años iguales → «iguales: sin conflicto»; se publica | «Sin discrepancias»; traza con pares «iguales» | PASS |

## D. Integridad y metadatos

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 17 | Re-subir el mismo archivo | `31_…` (otra vez) | 31 cargada | sha256 igual → `202 duplicado=true`, no reindexa (vectores 10→10); traza `recepcion` con duplicado | ♻️ «ya cargado, no se reindexó» | PASS |
| 18 | Mismo contenido, otro nombre | `casos_uso/CU18_copia_de_05_otro_nombre.pdf` | 05 cargada | Duplicado por sha256 → devuelve el doc_id de 05 | Igual que 17 | PASS |
| 19 | Mismo código y versión, contenido distinto | `casos_uso/CU19_res-sint-2023-018_corregida_misma_version.pdf` | 05 cargada | No es duplicado (otro sha): se crea `RES-SINT-2023-018_v1.0` con chunks propios; **ambas quedan vigentes y publicadas**, sin reemplazo ni advertencia | Dos filas con el mismo código; la nueva con doc_id `…_v1.0` | PASS (comportamiento real; ver brechas) |
| 20 | Sin ficha ni frontmatter | `casos_uso/CU20_sin_ficha_solo_texto.pdf` | ninguna | Metadatos por LLM con confianza por campo (código/nombre/entidad 0.9–1.0 según la ejecución; fechas y versión null con 0.0), `requiere_confirmacion=1`, estado inferido; doc_id `DOC-<sha8>` | Notas «estado inferido, requiere confirmación»; Metadatos con columna confianza | PASS |
| 21 | Ficha incompleta (sin fecha de vigencia) | `casos_uso/CU21_ci-2026-108_ficha_incompleta.pdf` | ninguna | El campo faltante se pide al LLM, que devuelve null → `fecha_vigencia=null`, en `faltantes`, `requiere_confirmacion=1` | Nota «requiere confirmación» | PASS |
| 22 | Fechas incoherentes | `casos_uso/CU22_ci-2026-109_fechas_incoherentes.pdf` | ninguna | Advertencia «fecha_vigencia (2026-01-01) es anterior a fecha_emision…», `requiere_confirmacion=1`; se publica | Nota «requiere confirmación»; advertencia en Metadatos/Traza | PASS |

## E. Formatos y rechazos

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 23 | DOCX con ficha | `casos_uso/CU23_pol-tec-2026-110_ficha.docx` | ninguna | python-docx → misma ficha (origen `frontmatter`), 3 chunks, publicada | Igual que caso 2 | PASS |
| 24 | TXT con ficha / MD con YAML | `casos_uso/CU24a_pro-ate-2026-111_ficha.txt`, `casos_uso/CU24b_pro-rec-2026-112_frontmatter.md` | ninguna | Ambos leen los metadatos declarados, 3 chunks, publicados | Igual que caso 2 | PASS |
| 25 | Sin artículos reconocibles | `casos_uso/CU25_pol-eti-2026-113_sin_articulos.pdf` | ninguna | «No se detectaron artículos numerados» → `requiere_revision`, 0 chunks, 0 vectores, estado `pendiente_revision` | 🟠 «no se indexó (requiere revisión)» + mensaje | PASS |
| 26 | PDF escaneado sin texto | `casos_uso/CU26_escaneado_sin_texto.pdf` (solo imagen) | ninguna | pypdf y pdfplumber sin texto → «Texto vacío o casi vacío (¿PDF escaneado sin OCR?)», sin llamar al LLM, `requiere_revision`, doc_id `DOC-<sha8>` | Igual que 25 | PASS |
| 27 | Formato no soportado | `casos_uso/CU27_tabla_no_soportada.xlsx`, `casos_uso/CU27_foto_no_soportada.jpg` | ninguna | `POST /api/documentos` → **400** «Formato no soportado: usa PDF, DOCX, TXT o MD»; nada se registra (el uploader de Streamlit ya filtra por extensión) | ❌ «rechazado» | PASS |
| 28 | Material interno (NO_SUBIR / 00_GUIA) | `casos_uso/CU28_notas_internas_NO_SUBIR.md` | ninguna | **400** «material interno…»; la ingesta directa lanza `ArchivoRechazado` | ❌ «rechazado» | PASS |

## F. Decisión humana (sobre un documento en `pendiente_revision`, p. ej. el caso 9)

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 29 | ✅ Aprobar y publicar | `09_…` | caso 9 | `vigente`, `publicado=1`, índice `vigente`; sus discrepancias (doc_a) pasan a `resuelta`; traza `decision_usuario` | Mensaje «Decisión 'aprobar_publicar' registrada»; sale de la cola | PASS |
| 30 | 📤 Publicar de todas formas | `12_pro-rep-2024-007.pdf` (en la verificación, otra ALTA pendiente) | 31 publicada → 12 | `vigente`, `publicado=1`, pero la ALTA **sigue abierta** en el informe | Sale de la cola; ALTA visible en el informe | PASS |
| 31 | ⛔ Rechazar | `13_pol-doc-2023-008.pdf` (en la verificación) | 31 publicada → 13 | `rechazada`, `publicado=0`, índice `rechazada`; todas sus discrepancias → `resuelta` | Chip «rechazada»; sale de la cola | PASS |

En la demo los tres botones se aplican sobre el caso 9; la verificación usa 12 y 13 para probar los tres desde el
mismo estado inicial (`pendiente_revision` con ALTA abierta).

## G. Áreas de aplicación

Lista cerrada (`app/normas/areas.py`): Riesgos, Créditos, Agencias, Cumplimiento, Marketing, Cobranzas, Call Center,
Operaciones, Auditoría, Legal, Contabilidad, Archivo, RRHH. Se compara sin tildes ni mayúsculas y acepta variantes
obvias («callcenter», «RR.HH.», «Recursos Humanos»); lo que no está en la lista se descarta y queda en la traza.
Las áreas **no** cambian `requiere_confirmacion` del documento: tienen su propio `areas_requiere_confirmacion`.

| Nº | Caso | Archivo(s) | Orden / precondición | Qué hace el sistema | Qué se ve | Verificación |
|---|---|---|---|---|---|---|
| 32 | Áreas declaradas en la ficha | `casos_uso/CU32_pol-pub-2026-132_areas_en_ficha.pdf` (fila «Áreas de aplicación: Marketing, Call Center, Legal») | ninguna | `areas_aplica = [Marketing, Call Center, Legal]`, `areas_fuente = ficha`, confianza 1.0, `areas_requiere_confirmacion = 0`; metadato `areas_aplica` (lista) en cada chunk del índice; resultado en el paso `metadatos` de la traza | Chips de áreas en el documento | PASS |
| 33 | Áreas en el frontmatter | `casos_uso/CU33_pro-cob-2026-133_areas_frontmatter.md` (`areas_aplica: Cobranzas, legal, RR.HH.`) | ninguna | Normaliza a `[Cobranzas, Legal, RRHH]`, `areas_fuente = frontmatter`, requiere 0 | Igual que 32 | PASS |
| 34 | Sin áreas declaradas | `02_res-sint-2023-009.pdf` (cualquier norma sin la fila) | ninguna | UNA llamada corta al LLM restringida a la lista → `areas_fuente = llm`, confianza del LLM, `areas_requiere_confirmacion = 1`; si el LLM falla: lista vacía y requiere 1; nunca un área fuera de la lista | «Áreas sugeridas: confirmar» | PASS |
| 35 | Corrección humana de áreas | `PUT /api/documentos/RES-SINT-2024-001/areas` `{areas: [Créditos, Riesgos]}` | 31 cargada | 400 si un área no está en la lista; si es válida: `areas_fuente = manual`, confianza 1, requiere 0, re-upsert de sus chunks con la nueva lista y el **mismo estado**, paso `edicion_areas` con antes/después | Chips actualizados | PASS |
| 36 | Norma de otra área en el chat | `POST /api/chat` como `marketing` (área Marketing): «¿límite de la relación cuota-ingreso…?» | 35 (31 sin Marketing) | Cada cita trae `areas_aplica` y `nota_area` = «Esta norma no es de tu área (Marketing), pero te aplica.»; la respuesta trae `area_usuario`; se guarda en `consultas.area_usuario` y en el historial | Nota bajo la cita | PASS |
| 37 | Caso con varias áreas | `POST /api/chat` con el caso de ejemplo 1 (`corpus/casos/CASO-01.txt`) | base cargada | `dictamen.areas_a_coordinar` = unión (orden de la lista) de las áreas de las normas citadas en el dictamen; cada evaluación con `doc_id` trae `areas_aplica` y `nota_area` | «Áreas a coordinar» en el dictamen | PASS |

## Bugs y brechas observados

1. ~~PDF 18 no se indexa~~ **Corregido**: `MIN_CHARS` bajó de 120 a 60 (caracteres sin espacios, sin la ficha).
2. ~~Mensaje engañoso en la carga~~ **Corregido**: el aviso de revisión solo aparece si la norma quedó
   `pendiente_revision`; si prevalece y se publica, se informa que hay ALTAS abiertas sobre otras normas.
3. **Motivo con unidades mezcladas** (caso 15): `clasificar` compara el valor canónico en horas (20 días = 480) con
   días hábiles (5) y escribe «menos estricta (480 vs 5…)».
4. **Interna en conflicto sigue publicada** (caso 13): cuando llega la externa, la interna con ALTA abierta no pasa a
   `pendiente_revision`; el Agente 2 la sigue usando.
5. **Misma versión corregida** (caso 19): coexisten dos `vigente` con el mismo código y versión, sin advertencia.
6. Menores: un duplicado vía API igual escribe el archivo en `data/uploads/`; el doc_id de un documento sin ficha
   queda `DOC-<sha8>` aunque el LLM extraiga el código.

## Secuencia sugerida para la demo en vivo (≈ 20 min)

Base cargada con `seed_corpus.py --reset --normas-demo --hasta-base` (incluye 31, 01 reemplazada, 07 reemplazada).

1. **Norma limpia (3 min)** — subir `casos_uso/CU24b_…md` o `CU23_….docx`: barra de etapas, ficha leída con
   confianza 1.0, publicada. Mostrar Traza (pasos, modelo, tokens, latencia).
2. **Contradicción ALTA (4 min)** — subir `09_man-cr-2025-004.pdf`: aparece en «Pendientes de revisión», banner rojo,
   informe A | B con citas literales 50% vs 40% y 60 vs 30 días, «Prevalece RES-SINT-2024-001». Pulsar **Rechazar** o
   **Aprobar y publicar** y ver el cambio de estado.
3. **Más estricta ≠ conflicto (3 min)** — la 11 (MAN-HIP-2024-006) ya está en la base: en el Informe de discrepancias
   filtrar «informativa» y mostrar 85% vs 90% «más estricta, no es contradicción»; luego subir `06_res-sint-2025-003.pdf`
   (MEDIA externa vs externa, prevalece la más reciente).
4. **Normalización de unidades (3 min)** — `casos_uso/CU14_…pdf`: sin discrepancias; en Traza → `comparacion`
   se lee «1 dias_calendario vs 24 horas = iguales». Contrastar con `casos_uso/CU15_…pdf` (hábiles vs calendario → ALTA).
5. **Vigencia (3 min)** — `casos_uso/CU05_…pdf`: la cláusula «Deróguese…» deja la 02 en gris «reemplazada»;
   `casos_uso/CU07_…pdf`: «por_entrar_en_vigencia» inferido.
6. **Control de calidad (3 min)** — re-subir el mismo PDF (♻️ duplicado), `CU20_…` (metadatos por LLM con confianza
   y «requiere confirmación»), `CU26_…` (escaneado → no se indexa) y `CU27_….xlsx` (rechazo 400).

## Cómo regenerar y verificar

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/generar_casos_uso.py      # regenera normas/casos_uso/ y comprueba la ficha
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/verificar_casos_uso.py    # SOLO en namespace dev (aborta en 'demo'); hace reset
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/seed_corpus.py --reset --normas-demo --hasta-base   # deja la base de la demo
```

La verificación corre dos escenarios con reset (escenario 1: 09 → 31 → 01 para los casos 13 y 4; escenario 2: el resto
en el orden de las tablas) y llama a la ingesta directamente o vía `TestClient` (`POST /api/documentos` para 17, 18, 27,
28 y `POST /api/documentos/{id}/decision` para 29–31). Tarda ≈ 3 min (DeepSeek + Pinecone).
