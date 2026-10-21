# Pitch — Normativa Bancaria · Agentes de IA

> Duración: 5 min. Caso **B2 Banca: normativa y políticas internas**. Equipo: [nombres].

## 1. Problema (30 s)

- **Quién lo sufre:** analistas de crédito, gerentes de agencia y el área de Cumplimiento.
- **Qué pasa hoy:** para evaluar una operación hay que encontrar, entre reglamentos SBS, políticas internas y
  manuales con varias versiones, la regla vigente que aplica. Se hace a mano, y dos personas pueden concluir
  distinto.
- **Cuánto cuesta:** [dato a validar: horas por consulta compleja, aprobaciones fuera de política, observaciones
  de auditoría o del regulador].

> Frase gancho: "Hoy, un analista puede aprobar un crédito con una política que ya fue reemplazada, y nadie se
> entera hasta la auditoría."

## 2. Solución (30 s)

Un equipo de agentes que **lee, valida y aplica** la normativa del banco:
- Al cargar una norma, detecta qué versión reemplaza y si contradice a otra.
- Ante un caso concreto, lo evalúa regla por regla y entrega un dictamen con citas exactas.
- Nunca inventa: cada cita se verifica contra el texto, y si no hay norma lo dice.

## 3. Agentes de IA (45 s)

| Agente | Qué hace | Herramientas |
|---|---|---|
| Ingesta | Extrae metadatos, divide por artículo, indexa y marca versiones reemplazadas | parsing, LLM, Pinecone |
| Validador | Encuentra contradicciones y actualizaciones entre normas, con citas de ambos lados | búsqueda de candidatos, registro verificado |
| Analista de casos | Extrae hechos, evalúa cada regla con cálculos, revisa conflictos y vacíos, dictamina | búsqueda vigente, artículos, conflictos, versiones anteriores, calculadora |
| Consultas | Responde preguntas cortas con documento, artículo y versión | búsqueda + rerank, conflictos |

- **Control humano:** las discrepancias se confirman o descartan en el panel; los casos con conflicto
  recomiendan escalar.
- **Trazabilidad:** cada paso, herramienta, fragmento (ID y score) y cálculo queda registrado y se ve en pantalla.
- **Abierto:** el LLM se cambia en un solo módulo, y el transporte entre agentes está listo para NANDA.

## 4. Demo (2 min) — en vivo

URL: **https://169.58.187.13/chat**

1. Clic en **CASO-01** (crédito de S/ 70,000). Dictamen **no procede**: endeudamiento (900 + 2,150) / 6,000 =
   50.8 % > 35 %, scoring 610 < 620 (con la v1 habría pasado), monto sobre la autonomía del gerente.
   Abrir **"Ver razonamiento del agente"**.
2. Panel → Normas: subir **SINT-03** (Manual de Agencias). El validador marca la contradicción: S/ 80,000 frente
   a S/ 50,000 de la política.
3. Repetir CASO-01: ahora advierte el conflicto entre normas y recomienda escalar a Cumplimiento.
4. Preguntar: "¿Qué política aplica a préstamos garantizados con criptoactivos?" → "No encontré una norma
   vigente…". Mostrar el **Dashboard**: aparece como tema sin cobertura.

> Plan B: si falla la red, correr local con `uvicorn app.main:app` y el índice de respaldo.
> Tener grabado un video del flujo completo.

## 5. Impacto (45 s)

Hipótesis a validar con el banco (no son mediciones):

| Métrica | Hoy (manual) | Con agentes |
|---|---|---|
| Tiempo para evaluar un caso contra la normativa | [ej. 30–60 min] | 20–35 s medidos en la demo |
| Uso de versiones reemplazadas | No se detecta | Advertido en cada respuesta |
| Contradicciones entre normas | Se descubren en auditoría | Se detectan al cargar la norma |
| Normas que faltan | Desconocidas | Dashboard de temas sin cobertura |
| Trazabilidad | Parcial | 100 % de los pasos registrados |

> Supuestos: [volumen de casos al mes, costo hora del analista, costo de una observación regulatoria].

## 6. Código (15 s)

- Repositorio: **https://github.com/vega012/hacktaton**
- Stack: Python, FastAPI, Pinecone (embeddings integrados + rerank), SQLite, Streamlit, LLM vía API compatible
  con OpenAI, Docker Compose, Caddy.
- Próximos pasos: cargar el corpus real del banco y de la SBS, OCR para PDFs escaneados, integración con el
  flujo de aprobación de créditos, conexión NANDA.
