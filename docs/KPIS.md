# KPIs (GET /api/kpis)

Regla general: todo sale de lo que registran los agentes en SQLite. Ninguna cifra está escrita a mano.
`valor: null` = **sin datos** (no hay filas con ese dato o el denominador es 0). Las filas anteriores a la
instrumentación tienen las columnas nuevas en NULL y **no** se cuentan (no se rellenan con suposiciones).
Filtro `desde`/`hasta` sobre `created_at` (fecha `AAAA-MM-DD` o fecha-hora ISO). Cálculo: `app/normas/kpis.py`.

| # | KPI | Definición / fórmula | Fuente | Sin datos |
|---|---|---|---|---|
| 1 | Respuestas con cita | filas con `n_citas_verificadas >= 1` / filas con dato. Cita verificada = el texto citado está literalmente en el chunk y la norma está vigente | `consultas.n_citas_verificadas` | ninguna consulta instrumentada |
| 2 | Sin cobertura | (`motivo_sin_cobertura = no_encontro` + filas con cita pero `top_score < umbral`) / filas con dato. `umbral` es parámetro; por defecto U2 del backend activo | `consultas.motivo_sin_cobertura`, `top_score` | idem |
| 3 | Con advertencia | filas con `advertencia_conflicto` o `advertencia_obsoleta` / filas con dato. Las marcas las pone el código determinista (discrepancia registrada sobre lo citado / versión reemplazada o derogada involucrada) | `consultas.advertencia_*` | idem |
| 4 | Confianza promedio | AVG(`confianza_score`), con `confianza_score` = score de la mejor cita verificada × (verificadas / propuestas); 0 si no hay verificadas. Más la distribución alta/media/sin_respaldo | `consultas.confianza_score`, `confianza` | idem |
| 5 | Discrepancias | discrepancias no descartadas, por severidad y tipo (estado actual, sin filtro de fechas) | `discrepancias` | total 0 |
| 6 | Normas obsoletas | documentos con estado reemplazada/derogada y quién la reemplaza | `documentos.estado`, `reemplazada_por` (o `reemplaza_a` de la nueva) | total 0 |
| 7 | Afectados por norma | por documento: otros documentos con los que comparte discrepancias + el que lo reemplaza | `discrepancias`, `documentos` | lista vacía |
| 8 | Tiempo de respuesta | `finished_at − started_at` de cada traza (promedio, mediana, por agente) | `trazas.started_at`, `finished_at` (ms) | trazas sin ambos campos se excluyen |
| 9 | Tiempo ahorrado (**ESTIMADO**) | consultas × max(0, `tiempo_manual_min` − promedio de los agentes consultas/analista_casos) en horas. `tiempo_manual_min` es un **supuesto editable** (parámetro, por defecto 15 min), no una medición | `consultas`, `trazas` | `horas: null` si no hay consultas o tiempos |
| 10 | Trazas completas | trazas terminadas con entrada no vacía + al menos un fragmento con id y score en los `tool_result` + salida + modelo + started_at + finished_at. Lista las incompletas y qué les falta | `trazas` | null si no hay trazas terminadas |
| 11 | Top normas / brechas | top: documentos más citados (`citas_json`). Brechas: temas de las consultas sin cobertura (según KPI 2) + vacíos de los dictámenes de casos (tabla `brechas`), por tema con 3 ejemplos | `consultas`, `brechas` | listas vacías |

Tendencia: consultas por bucket (`10min | hora | dia`) con `n` y `sin_cobertura`; brechas por tema.

Notas:
- `n_citas_verificadas` cuenta citas verificadas antes de aplicar el umbral: si la respuesta se marcó
  sin respaldo por score bajo, cuenta como cita y como `score_bajo` en el KPI 2.
- En casos, "propuestas" = evaluaciones que trajo el LLM; "verificadas" = evaluaciones vigentes con cita literal.
- **Los scores de Pinecone (rerank bge-reranker-v2-m3, 0–1) y del índice local (similitud léxica, valores
  bajos) no son comparables**: el umbral por defecto depende del backend (`backend` en la respuesta) y no
  conviene comparar `confianza_promedio` entre backends.
- Agente 1 (validador): guarda un Run en `trazas` (agente `validador`, con los fragmentos recuperados del índice con id
  y score) solo cuando la validación recuperó fragmentos o registró discrepancias. Una validación sin nada que
  recuperar (primer documento de un tema) queda trazada paso a paso en `trazas_ingesta`, no en `trazas`.
  Discrepancias por severidad: `alta | media | informativa` (las tres claves siempre presentes en `por_severidad`).
- Datos de demo: `scripts/poblar_demo.py` genera interacciones reales contra la API (nada inventado).
