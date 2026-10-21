# Checklist: del esqueleto al sector asignado

Tiempo objetivo: ~60–90 min. Partir del pack más cercano (`sectors/banca`, `sectors/mineria`,
`sectors/manufactura`) en vez de empezar de cero.

## 0. Elegir el caso (10 min)

- [ ] Definir **un** caso concreto y demostrable (no la solución completa del sector).
- [ ] Escribir en una frase: problema, quién aprueba y qué acción es sensible.
- [ ] Decidir si el pack existente sirve tal cual o se copia: `cp -r sectors/banca sectors/<nuevo>`.

## 1. Datos (`sectors/<sector>/data/*.json`)

- [ ] Reemplazar los datos sintéticos por datos del caso (si nos dan datos reales/dataset, convertirlos a JSON).
- [ ] Incluir al menos: un registro que **sí** dispare la acción y uno que **no** (para mostrar criterio).
- [ ] Incluir un registro que choque con un guardrail o validador (opcional, para mostrar control).

## 2. Tools (`sectors/<sector>/tools.py`)

- [ ] 1–2 tools de **consulta** (lectura de datos).
- [ ] 1–2 tools de **acción**; marcar con `sensitive=True` las que requieren aprobación humana.
- [ ] Descripciones y `params` claras en español (el LLM decide en base a ellas).
- [ ] Validadores `@validator("tool")` con las reglas de negocio clave (políticas, estados, límites).

## 3. Reglas (`sectors/<sector>/guardrails.json`)

- [ ] `max_values`: límites duros (ej. monto, cantidad).
- [ ] `approval_thresholds`: sobre qué valor se pide aprobación humana.
- [ ] `blocked_tools` / `require_approval_tools` si aplica.

## 4. Agentes y flujo (`sectors/<sector>/__init__.py`)

- [ ] Nombres y `role` de cada agente con el vocabulario del sector y la política explícita.
- [ ] `tools=[...]` de cada agente (consulta vs. acción separados).
- [ ] `handoffs=[...]` / `next={...}` del `Flow`.
- [ ] `thinking=True` solo si un agente necesita razonar más (por defecto OFF).
- [ ] `CASO_EJEMPLO`: el texto exacto que se usará en la demo.

## 5. Interfaz (`sectors/<sector>/ui.json`)

- [ ] `title`, `subtitle`, `case_label` con el lenguaje del sector.
- [ ] `tool_names` y `field_names` para todas las tools y campos que se muestran.
- [ ] (Opcional) textos comunes en `sectors/ui_base.json`.

## 6. Activar y probar

- [ ] `SECTOR=<sector>` en `.env` local y en el `.env` del VPS (`ssh root@169.58.187.13 'nano ~/hacktaton/.env'`).
- [ ] `python scripts/run_example.py` → termina en `completed` con la aprobación.
- [ ] `python scripts/run_example.py --rechazar` → el agente respeta el rechazo.
- [ ] Probar la interfaz local: lanzar, aprobar con comentario, ver resultado.
- [ ] Correr el caso 2–3 veces: el flujo debe ser estable (si varía, precisar el `role` o el caso).

## 7. Desplegar y verificar

- [ ] `git add -A && git commit -m "Sector <x>"`
- [ ] `git push vps main && ssh root@169.58.187.13 'cd ~/hacktaton && ./deploy.sh'`
- [ ] `python scripts/verify_remote.py http://169.58.187.13` → `completed`.
- [ ] Probar la URL desde otro dispositivo / red (ej. datos del celular).
- [ ] Subir el repo público para los jurados y poner la URL en README y PITCH.

## 8. Documentación y pitch

- [ ] README: secciones **Problema**, **Sector** y **Código** (URL).
- [ ] PITCH.md: completar todos los `[ ]`, con 2–3 métricas de impacto y sus supuestos.
- [ ] Ensayar la demo completa con cronómetro (≤ 90 s).
- [ ] Plan B: captura/video del flujo completo por si falla la red.
