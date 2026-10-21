"""Interfaz Streamlit (cliente delgado de la API). Plan B del bot y consola de revisión.

Uso:  .venv/Scripts/streamlit run ui/streamlit_app.py --server.port 8501
API:  API_BASE_URL (env o .env), por defecto http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from app.config import get_settings
    API = os.environ.get("API_BASE_URL") or get_settings().api_base_url
except Exception:
    API = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
API = API.rstrip("/") + "/api"

st.set_page_config(page_title="Normativa · Panel", page_icon=":material/balance:", layout="wide")

# Sistema visual compartido con /chat y /login (tokens idénticos). Solo CSS propio: nunca datos del servidor.
_CSS = """<style>
:root{
  --bg:#F6F7F9; --surface:#FFFFFF; --surface-2:#F3F5F7; --line:#E3E7EB; --line-2:#ECEFF2;
  --text:#0F1A1F; --muted:#5B6770; --faint:#8A949C;
  --acc:#0F4C5C; --acc-hover:#0B3B48; --acc-soft:#E8F0F2;
  --ok:#1E7A4F; --ok-bg:#E8F4EE; --warn:#9A6200; --warn-bg:#FBF1DE; --bad:#B3362F; --bad-bg:#FBEAE8;
  --r-sm:8px; --r:12px; --shadow:0 1px 2px rgba(16,24,40,.04);
  --sans:"Geist","Segoe UI",system-ui,-apple-system,sans-serif; --mono:"Geist Mono","Cascadia Mono",Consolas,monospace;
}
/* chrome de Streamlit fuera */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stAppDeployButton"],
#MainMenu, footer, [data-testid="stStatusWidget"] { display:none !important; }
.stApp { background:var(--bg); }
.stMainBlockContainer, .block-container { max-width:1320px; padding:28px 40px 72px !important; }
html, body, .stApp { font-family:var(--sans); -webkit-font-smoothing:antialiased; }
h1, h2, h3, h4 { letter-spacing:-0.02em !important; color:var(--text); }
h3 { font-size:1.45rem !important; font-weight:650 !important; padding:0 0 2px !important; }
h4 { font-size:1.08rem !important; font-weight:600 !important; padding:18px 0 4px !important; }
[data-testid="stCaptionContainer"] { color:var(--muted); }
code { font-family:var(--mono) !important; font-size:.88em !important; padding:1px 6px !important; border-radius:6px; }

/* cabecera de producto */
.nx-top { display:flex; align-items:center; gap:12px; padding:2px 0 22px; }
.nx-mark { width:30px; height:30px; border-radius:8px; background:var(--acc); display:grid; place-items:center; flex:none; }
.nx-name { font-size:17px; font-weight:650; letter-spacing:-0.02em; color:var(--text); }
.nx-name span { color:var(--faint); font-weight:500; }
.nx-sep { flex:1; }
.nx-link { font:500 13.5px/1 var(--sans); color:var(--muted) !important; text-decoration:none !important; padding:8px 12px;
  border-radius:999px; border:1px solid transparent; transition:background .15s, border-color .15s, color .15s; }
.nx-link:hover { color:var(--text) !important; background:var(--surface); border-color:var(--line); }
.nx-salir { color:var(--text) !important; border-color:var(--line); background:var(--surface); }
.nx-env { font:500 12.5px/1 var(--mono); color:var(--muted); background:var(--surface); border:1px solid var(--line);
  border-radius:999px; padding:7px 12px; display:inline-flex; align-items:center; gap:8px; }
.nx-env i { width:7px; height:7px; border-radius:50%; background:var(--ok); display:inline-block; }

/* pestañas: control segmentado (react-aria en Streamlit 1.66) */
.stTabs > div { border:none !important; box-shadow:none !important; }
.stTabs [role="tablist"]::before, .stTabs [role="tablist"]::after { display:none !important; }
.stTabs [role="tablist"] { gap:2px; background:#E9ECF0; padding:4px; border-radius:10px; width:fit-content;
  max-width:100%; overflow-x:auto; border:none !important; box-shadow:none !important; }
.stTabs [data-testid="stTab"] { height:auto; padding:7px 16px; border-radius:8px; background:transparent; margin:0 !important;
  color:var(--muted); transition:background .15s, color .15s, box-shadow .15s; }
.stTabs [data-testid="stTab"] p { font-size:15px; font-weight:550; color:inherit; }
.stTabs [data-testid="stTab"]:hover { color:var(--text); }
.stTabs [data-testid="stTab"][aria-selected="true"] { background:var(--surface); color:var(--text);
  box-shadow:0 1px 2px rgba(16,24,40,.08), 0 0 0 1px rgba(16,24,40,.04); }
.stTabs .react-aria-SelectionIndicator { display:none !important; }
.stTabs [data-testid="stTabPanel"] { padding-top:26px; }
.stTabs .stTabs [role="tablist"] { background:transparent; padding:0; gap:4px; }
.stTabs .stTabs [data-testid="stTab"] { padding:6px 12px; }
.stTabs .stTabs [data-testid="stTab"] p { font-size:14px; }
.stTabs .stTabs [data-testid="stTab"][aria-selected="true"] { background:var(--acc-soft); color:var(--acc); box-shadow:none; }
.stTabs .stTabs [data-testid="stTabPanel"] { padding-top:20px; }
/* chips de multiselect: suaves, no bloque sólido */
[data-testid="stMultiSelect"] [data-tag] { background:var(--acc-soft) !important; color:var(--acc) !important; border-radius:999px !important; }
[data-testid="stMultiSelect"] [data-tag] span, [data-testid="stMultiSelect"] [data-tag] button { color:var(--acc) !important; }

/* superficies */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]),
div[data-testid="stVerticalBlock"][class*="border"] { background:var(--surface); border-color:var(--line) !important;
  border-radius:var(--r) !important; box-shadow:var(--shadow); }
[data-testid="stExpander"] details { background:var(--surface); border:1px solid var(--line); border-radius:var(--r-sm); box-shadow:none; }
[data-testid="stExpander"] summary { padding:12px 16px; }
[data-testid="stExpander"] summary:hover { color:var(--acc); }
[data-testid="stDataFrame"] { background:var(--surface); border-radius:var(--r-sm); }
[data-testid="stForm"] { background:var(--surface); border:1px solid var(--line); border-radius:var(--r); padding:24px; }
[data-testid="stAlert"] > div { border-radius:var(--r-sm); }
[data-testid="stFileUploaderDropzone"] { background:var(--surface); border:1px dashed #C9D1D7; border-radius:var(--r); }

/* métricas: tarjetas KPI */
[data-testid="stMetric"] { padding:2px 2px 4px; }
[data-testid="stMetricLabel"] p { font-size:12px !important; font-weight:600; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); }
[data-testid="stMetricValue"] { font-size:2.1rem !important; font-weight:600; letter-spacing:-0.03em;
  font-variant-numeric:tabular-nums; color:var(--text); }

/* botones */
.stButton button, .stFormSubmitButton button, .stDownloadButton button { font-weight:550; transition:all .15s;
  box-shadow:var(--shadow); }
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] { background:var(--acc); border-color:var(--acc); }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover { background:var(--acc-hover); border-color:var(--acc-hover); }
[data-testid="stBaseButton-secondary"]:hover { border-color:var(--acc); color:var(--acc); }

/* pills (st.badge y :color-badge[]) */
[data-testid="stMarkdownContainer"] span[class*="badge"], .stBadge span { border-radius:999px !important; }
.stMarkdown blockquote { border-left:2px solid var(--line); color:var(--text); padding-left:14px; margin:6px 0; }
@media (prefers-reduced-motion: reduce){ *{ transition:none !important; animation:none !important; } }
</style>"""
st.markdown(_CSS, unsafe_allow_html=True)

CONF = {"alta": ("Confianza alta", "green"), "media": ("Confianza media", "orange"),
        "sin_respaldo": ("Sin respaldo normativo", "red")}


def api(method: str, path: str, **kw):
    """Llama a la API; devuelve (datos, error). Nunca lanza excepción."""
    try:
        r = requests.request(method, API + path, timeout=kw.pop("timeout", 30), **kw)
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}: {r.text[:300]}"
        return r.json(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


AREAS_DEFAULT = ["Riesgos", "Créditos", "Agencias", "Cumplimiento", "Marketing", "Cobranzas", "Call Center",
                 "Operaciones", "Auditoría", "Legal", "Contabilidad", "Archivo", "RRHH"]


def areas_lista() -> list[str]:
    """Lista cerrada de áreas desde GET /api/areas (cacheada en sesión); si falla, la constante local."""
    cache = st.session_state.get("_areas_cache")
    if isinstance(cache, list) and cache:
        return cache
    r, e = api("GET", "/areas", timeout=10)
    if not e and isinstance(r, list) and r and all(isinstance(x, str) for x in r):
        st.session_state["_areas_cache"] = r
        return r
    return list(AREAS_DEFAULT)


def areas_de(x) -> list[str]:
    """areas_aplica puede llegar como lista, string JSON, string separado por comas o None."""
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                import json
                x = json.loads(s)
            except Exception:
                x = s.strip("[]").replace('"', "").split(",")
        else:
            x = s.split(",")
    if not isinstance(x, (list, tuple)):
        return []
    return [str(a).strip() for a in x if a is not None and str(a).strip()]


def pill(texto, color: str = "gray") -> None:
    """Pill de estado (mismo estilo en todo el panel)."""
    try:
        st.badge(str(texto), color=color)
    except Exception:
        st.caption(str(texto))


def mostrar_respuesta(r: dict) -> None:
    st.markdown(r.get("respuesta", ""))
    txt, color = CONF.get(r.get("confianza"), (str(r.get("confianza")), "gray"))
    pill(txt, color)
    st.caption(f"score {r.get('top_score')} · {r.get('latencia_ms')} ms · run {r.get('run_id')}")
    for a in r.get("advertencias") or []:
        st.warning(a)
    for c in r.get("citas") or []:
        with st.expander(f"{c.get('nombre_norma')} · Art. {c.get('articulo')} · v{c.get('version')} · "
                         f"{c.get('estado')}"):
            if c.get("cita_literal"):
                st.markdown(f"> {c['cita_literal']}")
            st.caption(c.get("texto", ""))
            st.caption(f"chunk: {c.get('chunk_id', '')}")


def _cabecera() -> None:
    """Barra de producto: nombre a la izquierda, entorno (host de la API) discreto a la derecha."""
    import html as _html
    from urllib.parse import urlparse
    host = _html.escape(urlparse(API).netloc or API)
    try:  # Caddy envía el usuario autenticado en X-Usuario (forward_auth)
        hdr = st.context.headers
        usuario = hdr.get("X-Usuario") or hdr.get("x-usuario")
    except Exception:
        usuario = None
    usuario_html = (f"<div class='nx-env' title='Sesión iniciada'><i></i>{_html.escape(usuario)} · administrador</div>"
                    if usuario else f"<div class='nx-env' title='API'><i></i>{host}</div>")
    st.markdown(
        "<div class='nx-top'><div class='nx-mark'><svg width='16' height='16' viewBox='0 0 24 24' fill='none' "
        "stroke='#fff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M12 3v18M5 7h14"
        "M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0z'/></svg></div>"
        "<div class='nx-name'>Normativa · Agentes de IA <span>&nbsp;/&nbsp; Panel de administración</span></div>"
        f"<div class='nx-sep'></div>{usuario_html}"
        "<a class='nx-link' href='/chat' target='_self'>Ir al chat</a>"
        "<a class='nx-link nx-salir' href='/logout' target='_self'>Cerrar sesión</a></div>",
        unsafe_allow_html=True)


def seccion(titulo: str, sub: str | None = None) -> None:
    st.markdown(f"### {titulo}")
    if sub:
        st.caption(sub)


_cabecera()
tabs = st.tabs(["Consultar", "Normas", "Trazas", "Dashboard", "KPIs", "Usuarios"])

# ------------------------------------------------------------------ Consultar
with tabs[0]:
    seccion("Consultar", "Consulta directa al agente de normativa vigente, con citas verificables.")
    if "chat" not in st.session_state:
        st.session_state.chat = []
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.markdown(m["content"])
            else:
                mostrar_respuesta(m["content"]) if isinstance(m["content"], dict) else st.error(m["content"])
    pregunta = st.chat_input("Escribe tu consulta normativa…")
    if pregunta:
        st.session_state.chat.append({"role": "user", "content": pregunta})
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.chat_message("assistant"):
            with st.spinner("Buscando en la normativa vigente…"):
                r, err = api("POST", "/consultas", json={"pregunta": pregunta, "canal": "web",
                                                         "usuario": "web:streamlit"}, timeout=120)
            if err:
                st.error(f"No se pudo consultar: {err}")
                st.session_state.chat.append({"role": "assistant", "content": f"Error: {err}"})
            else:
                mostrar_respuesta(r)
                st.session_state.chat.append({"role": "assistant", "content": r})

# ------------------------------------------------------------------ Agente 1 · Normas (ingesta y validación)
ETAPAS = [("extrayendo_metadatos", "Extrayendo metadatos"), ("indexando", "Indexando"),
          ("comparando", "Comparando"), ("informe", "Informe")]
ETAPA_IDX = {"recibido": 0, "extrayendo_metadatos": 1, "indexando": 2, "comparando": 3, "informe": 4, "listo": 5}
FINALES = {"listo", "requiere_revision", "error"}
CHIP = {"vigente": ("", "green"), "reemplazada": ("", "gray"), "pendiente_revision": ("", "orange"),
        "rechazada": ("", "red"), "derogada": ("", "red"), "por_entrar_en_vigencia": ("", "blue")}
# Mismos colores que las pills (texto semántico sobre fondo suave)
CHIP_BG = {"vigente": ("#E8F4EE", "#1E7A4F"), "reemplazada": ("#EEF1F4", "#5B6770"),
           "pendiente_revision": ("#FBF1DE", "#9A6200"), "rechazada": ("#FBEAE8", "#B3362F"),
           "derogada": ("#FBEAE8", "#B3362F"), "por_entrar_en_vigencia": ("#E8F0F2", "#0F4C5C")}
SEV = {"alta": ("ALTA", "red"), "media": ("MEDIA", "orange"), "informativa": ("INFORMATIVA", "blue")}
_MD_ESPECIALES = "\\`*_{}[]<>()#+-.!|~$"


def plano(x) -> str:
    """Quita el énfasis markdown (**x**, __x__) de las citas literales."""
    return ("" if x is None else str(x)).replace("**", "").replace("__", "")


def hay_valores(serie) -> bool:
    """True si hay algún valor numérico distinto de 0 (si todo es 0 se muestra «sin datos», no un gráfico vacío)."""
    try:
        v = pd.to_numeric(pd.Series(pd.DataFrame(serie).to_numpy().ravel()), errors="coerce").fillna(0)
        return bool((v != 0).any())
    except Exception:
        return False


def md(x) -> str:
    """Escapa texto del servidor para mostrarlo literal dentro de markdown."""
    s = "" if x is None else str(x)
    return "".join("\\" + ch if ch in _MD_ESPECIALES else ch for ch in s)


def _js(x):
    """metadatos_json/confianza_json pueden llegar como dict o como string JSON."""
    if isinstance(x, (dict, list)):
        return x
    if isinstance(x, str) and x.strip():
        try:
            import json
            return json.loads(x)
        except Exception:
            return {}
    return {}


def chip_txt(estado) -> str:
    return str(estado or "?")


def chip(estado) -> None:
    e = str(estado or "?")
    pill(e, CHIP.get(e, ("", "gray"))[1])


def _pasos_txt(etapa: str | None) -> str:
    """':green[✓ Extrayendo metadatos]  ›  **Indexando**  ›  :gray[Comparando]  ›  :gray[Informe]'."""
    i = ETAPA_IDX.get(etapa or "", 0)
    partes = []
    for n, (_, lbl) in enumerate(ETAPAS, start=1):
        if n < i or etapa == "listo":
            partes.append(f":green[✓ {lbl}]")
        elif n == i:
            partes.append(f"**{lbl}…**")
        else:
            partes.append(f":gray[{lbl}]")
    return "  ›  ".join(partes)


def _subir_y_seguir(nombre: str, contenido: bytes, timeout_s: float = 180, intervalo: float = 1.5,
                    areas: list[str] | None = None) -> dict:
    """POST del archivo y sondeo de /progreso con estado visual. Devuelve un resumen; nunca lanza."""
    res = {"archivo": nombre, "doc_id": None, "resultado": "error", "mensaje": ""}
    with st.status(f"{nombre} · enviando…", expanded=True) as stt:
        r, err = api("POST", "/documentos", files={"archivo": (nombre, contenido)},
                     data={"areas": ", ".join(areas)} if areas else None, timeout=180)
        if err:
            res["mensaje"] = err
            stt.update(label=f"{nombre} · rechazado", state="error")
            st.error(f"No se aceptó el archivo: {err}")
            return res
        r = r if isinstance(r, dict) else {}
        res["doc_id"] = r.get("doc_id")
        if r.get("duplicado"):
            res.update(resultado="duplicado", mensaje=r.get("mensaje") or "")
            stt.update(label=f"{nombre} · ya cargado, no se reindexó", state="complete", expanded=False)
            st.info(f"Ya cargado ({r.get('codigo') or r.get('doc_id')}), no se reindexó.")
            return res
        if not res["doc_id"]:
            res["mensaje"] = "El servidor no devolvió doc_id"
            stt.update(label=f"{nombre} · respuesta inesperada", state="error")
            return res
        barra = st.progress(0.0, text="recibido")
        pasos = st.empty()
        t0, p, fallos = time.time(), {}, 0
        while time.time() - t0 < timeout_s:
            p, e2 = api("GET", f"/documentos/{res['doc_id']}/progreso", timeout=15)
            if e2:
                fallos += 1
                if fallos >= 3:
                    st.warning(f"Progreso no disponible ({e2}). El documento sigue procesándose en el servidor.")
                    res.update(resultado="sin_progreso", mensaje=e2)
                    stt.update(label=f"{nombre} · enviado, sin seguimiento", state="complete")
                    return res
                p = {}
            else:
                fallos = 0
                p = p if isinstance(p, dict) else {}
                etapa = p.get("etapa") or "recibido"
                barra.progress(min(ETAPA_IDX.get(etapa, 4), 5) / 5,
                               text=f"{etapa} · {str(p.get('mensaje') or '')[:120]}")
                pasos.markdown(_pasos_txt(etapa))
                stt.update(label=f"{nombre} · {etapa}")
                if etapa in FINALES:
                    break
            time.sleep(intervalo)
        etapa = (p or {}).get("etapa")
        res["mensaje"] = (p or {}).get("mensaje") or ""
        if etapa == "listo":
            res["resultado"] = "listo"
            altas = p.get("n_discrepancias_altas") or 0
            if p.get("estado") == "pendiente_revision":
                stt.update(label=f"{nombre} · indexado, requiere revisión ({altas} ALTAS)", state="complete")
                st.warning(f"Requiere revisión: {altas} discrepancias ALTAS — no se publica para el Agente 2 "
                           "hasta decisión humana.")
            elif altas:  # la norma nueva prevalece (p. ej. externa) y queda publicada; las ALTAS señalan a otras
                stt.update(label=f"{nombre} · publicada, {altas} discrepancias ALTAS abiertas en otras normas",
                           state="complete")
                st.info(f"Publicada: esta norma prevalece. Quedan {altas} discrepancias ALTAS abiertas sobre normas "
                        "internas que deben adecuarse (ver Informe de discrepancias).")
            else:
                stt.update(label=f"{nombre} · listo, estado {p.get('estado') or '?'}", state="complete",
                           expanded=False)
        elif etapa == "requiere_revision":
            res["resultado"] = "requiere_revision"
            stt.update(label=f"{nombre} · no se indexó (requiere revisión)", state="error")
            st.warning("No se indexó: el contenido no parece una norma válida (texto ilegible o sin artículos). "
                       f"{res['mensaje']}")
        elif etapa == "error":
            stt.update(label=f"{nombre} · error en la ingesta", state="error")
            st.error(f"Error en la ingesta: {res['mensaje']}")
        else:
            res["resultado"] = "timeout"
            stt.update(label=f"{nombre} · sigue en proceso (más de {int(timeout_s)} s)", state="complete")
            st.info("La ingesta continúa en el servidor; actualiza la lista en unos momentos.")
    return res


def render_discrepancias(lista: list, kp: str, filtros: bool = True) -> None:
    """Informe de discrepancias A | B con severidad, prevalencia y botones de revisión."""
    lista = [d for d in (lista or []) if isinstance(d, dict)]
    if not lista:
        st.success("Sin discrepancias registradas.")
        return
    if filtros:
        f1, f2 = st.columns(2)
        sevs = f1.multiselect("Severidad", ["alta", "media", "informativa"], key=f"{kp}_fsev",
                              placeholder="todas")
        temas = sorted({str(d.get("tema")) for d in lista if d.get("tema")})
        tsel = f2.multiselect("Tema", temas, key=f"{kp}_ftema", placeholder="todos")
        lista = [d for d in lista if (not sevs or d.get("severidad") in sevs)
                 and (not tsel or str(d.get("tema")) in tsel)]
    orden = {"alta": 0, "media": 1, "informativa": 2}
    lista.sort(key=lambda d: orden.get(d.get("severidad"), 3))
    n_alta = sum(1 for d in lista if d.get("severidad") == "alta")
    st.caption(f"{len(lista)} discrepancia(s) · {n_alta} ALTA(s)")
    for d in lista:
        did = d.get("id")
        sev_lbl, color = SEV.get(d.get("severidad"), (str(d.get("severidad") or "?").upper(), "gray"))
        with st.container(border=True):
            st.markdown(f":{color}-badge[{sev_lbl}] &nbsp; **{md(d.get('tema') or '—')}**")
            st.caption(f"{md(d.get('tipo') or '—')} · #{md(did)} · revisión: "
                       f"{md(d.get('estado_revision') or d.get('estado') or '—')}")
            a, b = st.columns(2, gap="large")
            for col, lado, letra in ((a, "a", "A"), (b, "b", "B")):
                with col:
                    codigo = str(d.get("doc_" + lado) or "—").replace("`", "")
                    st.caption(f"DOCUMENTO {letra}")
                    st.markdown(f"`{codigo}` &nbsp;**{md(d.get('nombre_' + lado) or '')}**  \n"
                                f":gray[Art. {md(d.get('art_' + lado) or '—')} · versión "
                                f"{md(d.get('version_' + lado) or '—')}]")
                    cita = plano(d.get("cita_" + lado))
                    if cita:
                        st.markdown("> " + md(cita).replace("\n", "\n> "))
                    else:
                        st.caption("(sin cita literal)")
            if d.get("prevalece") or d.get("motivo"):
                st.success(f"**Prevalece: {md(d.get('prevalece') or '—')}** — {md(d.get('motivo') or '')}")
            if d.get("explicacion"):
                st.caption(md(d.get("explicacion")))
            if did is not None and (d.get("estado_revision") in (None, "", "pendiente")):
                com = st.text_input("Comentario (opcional)", key=f"{kp}_com_{did}")
                c1, c2, _ = st.columns([1, 1, 3])
                for col, dec, lbl in ((c1, "confirmada", "Confirmar"), (c2, "descartada", "Descartar")):
                    if col.button(lbl, key=f"{kp}_{dec}_{did}", width="stretch",
                                  type="primary" if dec == "confirmada" else "secondary"):
                        _, e2 = api("POST", f"/discrepancias/{did}/revisar",
                                    json={"decision": dec, "comentario": com})
                        if e2:
                            st.error(f"No se pudo registrar la revisión: {e2}")
                        else:
                            st.rerun()


def _decidir(doc_id, decision: str, comentario: str) -> None:
    r, e = api("POST", f"/documentos/{doc_id}/decision",
               json={"decision": decision, "usuario": "web:streamlit", "comentario": comentario})
    if e:
        st.error(f"No se pudo registrar la decisión: {e}")
    else:
        r = r if isinstance(r, dict) else {}
        st.session_state["a1_flash"] = (f"Decisión '{decision}' registrada · estado {r.get('estado')} · "
                                        f"publicado {r.get('publicado')}")
        st.rerun()


def banner_revision(doc: dict, n_altas: int, kp: str) -> None:
    with st.container(border=True):
        st.markdown(f":orange-badge[Requiere revisión] &nbsp; **{n_altas} discrepancias ALTAS** · "
                    "no se publica para el Agente 2 hasta decisión humana")
        st.caption(f"{md(doc.get('codigo') or doc.get('doc_id'))} · {md(doc.get('nombre_norma') or '')}")
        com = st.text_input("Comentario del revisor (opcional)", key=f"{kp}_dcom")
        c1, c2, c3 = st.columns(3)
        if c1.button("Aprobar y publicar", key=f"{kp}_aprobar", type="primary", width="stretch"):
            _decidir(doc.get("doc_id"), "aprobar_publicar", com)
        if c2.button("Rechazar", key=f"{kp}_rechazar", width="stretch"):
            _decidir(doc.get("doc_id"), "rechazar", com)
        if c3.button("Publicar de todas formas", key=f"{kp}_igual", width="stretch"):
            _decidir(doc.get("doc_id"), "publicar_igual", com)


def editor_areas(doc: dict, sel: str) -> None:
    """Áreas de aplicación del documento: fuente/confianza y edición manual (PUT /documentos/{id}/areas)."""
    actuales = areas_de(doc.get("areas_aplica"))
    lista = areas_lista()
    with st.container(border=True):
        st.markdown("**Áreas de aplicación**")
        fuente, confz = doc.get("areas_fuente"), doc.get("areas_confianza")
        try:
            conf_txt = f"{float(confz):.2f}" if confz is not None else "—"
        except (TypeError, ValueError):
            conf_txt = md(confz)
        st.caption(f"Actuales: {md(', '.join(actuales) or '—')} · fuente: {md(fuente or '—')} · "
                   f"confianza: {conf_txt}")
        if doc.get("areas_requiere_confirmacion") in (1, True, "1"):
            st.markdown(":orange-badge[Inferidas por IA] &nbsp; Confirma o corrige y guarda.")
        opciones = list(lista) + [a for a in actuales if a not in lista]
        nuevas = st.multiselect("Áreas a las que aplica la norma", opciones, default=actuales,
                                key=f"a1_areas_{sel}", placeholder="ninguna")
        if st.button("Guardar áreas", key=f"a1_areas_btn_{sel}"):
            r, e = api("PUT", f"/documentos/{sel}/areas", json={"areas": list(nuevas), "usuario": "web:streamlit"})
            if e:
                st.error(f"No se pudieron guardar las áreas: {e}")
            else:
                r = r if isinstance(r, dict) else {}
                guardadas = areas_de(r.get("areas_aplica")) or list(nuevas)
                st.session_state["a1_flash"] = f"Áreas guardadas: {', '.join(guardadas) or '—'}"
                st.session_state.pop(f"a1_areas_{sel}", None)
                st.rerun()


with tabs[1]:
    seccion("Normas", "Agente 1 · ingesta, validación contra el corpus y control humano antes de publicar.")
    if st.session_state.get("a1_flash"):
        st.success(st.session_state.pop("a1_flash"))

    docs, err_docs = api("GET", "/documentos")
    if err_docs:
        st.warning(f"El servicio de documentos no está disponible: {err_docs}")
    docs = [d for d in (docs or []) if isinstance(d, dict)] if isinstance(docs, list) else []
    por_id = {str(d.get("doc_id")): d for d in docs if d.get("doc_id") is not None}

    # Cola de pendientes de revisión (siempre arriba)
    pend = [d for d in docs if d.get("estado") == "pendiente_revision"]
    if pend:
        with st.container(border=True):
            st.markdown(f"**Pendientes de revisión** &nbsp; :orange-badge[{len(pend)}]")
            for d in pend:
                c1, c2 = st.columns([6, 1], vertical_alignment="center")
                codigo = str(d.get("codigo") or d.get("doc_id") or "").replace("`", "")
                c1.markdown(f"`{codigo}` &nbsp; {md(d.get('nombre_norma') or '')}  \n"
                            f":gray[v{md(d.get('version') or '?')} · {md(d.get('entidad') or '—')}]")
                if c2.button("Revisar", key=f"a1_rev_{d.get('doc_id')}", width="stretch"):
                    st.session_state["a1_doc_sel"] = str(d.get("doc_id"))
                    st.rerun()
    elif not err_docs:
        st.caption("No hay documentos pendientes de revisión.")

    sub = st.tabs(["Cargar normas", "Normas y detalle", "Informe de discrepancias"])

    # ---- Cargar
    with sub[0]:
        archivos = st.file_uploader("PDF, DOCX, TXT o MD (uno o varios)", type=["pdf", "docx", "txt", "md"],
                                    accept_multiple_files=True, key="a1_upl")
        areas_carga = st.multiselect(
            "Áreas de aplicación (opcional)", areas_lista(), key="a1_areas_carga",
            help="Se aplican a todos los archivos de esta carga y prevalecen sobre la ficha. "
                 "Si lo dejas vacío, se usan las áreas de la ficha del documento o las infiere la IA (para confirmar).")
        if archivos and st.button(f"Subir e indexar ({len(archivos)})", type="primary", key="a1_subir"):
            st.session_state["a1_cola"] = [(a.name, a.getvalue()) for a in archivos]
            st.session_state["a1_cola_areas"] = list(areas_carga)
        cola = st.session_state.pop("a1_cola", None)
        if cola:
            areas_cola = st.session_state.pop("a1_cola_areas", None) or None
            resultados = [_subir_y_seguir(n, c, areas=areas_cola) for n, c in cola]
            st.session_state["a1_resultados"] = resultados
        elif st.session_state.get("a1_resultados"):
            st.markdown("**Última carga**")
            st.dataframe(pd.DataFrame(st.session_state["a1_resultados"]), width="stretch", hide_index=True)

    # ---- Lista + detalle
    with sub[1]:
        if not docs:
            st.info("Aún no hay normas registradas." if not err_docs else "Lista no disponible.")
        else:
            c1, c1b, c2 = st.columns([3, 3, 1], vertical_alignment="bottom")
            estados = sorted({str(d.get("estado")) for d in docs if d.get("estado")})
            fest = c1.multiselect("Filtrar por estado", estados, key="a1_fest", placeholder="todos")
            areas_docs = {a for d in docs for a in areas_de(d.get("areas_aplica"))}
            opc_areas = list(areas_lista()) + sorted(areas_docs - set(areas_lista()))
            farea = c1b.multiselect("Filtrar por área", opc_areas, key="a1_farea", placeholder="todas")
            if c2.button("Actualizar", key="a1_ref", width="stretch"):
                st.rerun()
            vis = [d for d in docs if (not fest or str(d.get("estado")) in fest)
                   and (not farea or set(areas_de(d.get("areas_aplica"))) & set(farea))]
            filas = []
            for d in vis:
                notas = []
                if d.get("estado_inferido"):
                    notas.append("estado inferido")
                if d.get("requiere_confirmacion"):
                    notas.append("requiere confirmación")
                filas.append({"código": d.get("codigo") or d.get("doc_id"), "estado": d.get("estado"),
                              "chip": chip_txt(d.get("estado")), "norma": d.get("nombre_norma"),
                              "tipo": d.get("tipo_fuente"), "versión": d.get("version"),
                              "áreas": (", ".join(areas_de(d.get("areas_aplica"))) or "—") +
                                       (" · por confirmar (IA)"
                                        if d.get("areas_requiere_confirmacion") in (1, True, "1") else ""),
                              "reemplaza a": d.get("reemplaza_a") or "—",
                              "reemplazada por": d.get("reemplazada_por") or "—",
                              "publicado": "sí" if d.get("publicado") in (1, True, "1") else "no",
                              "notas": ", ".join(notas), "vigencia": d.get("fecha_vigencia") or "—",
                              "chunks": d.get("n_chunks"), "ingesta": d.get("estado_ingesta")})
            df = pd.DataFrame(filas)
            if not df.empty:
                def _bg(v):
                    if v not in CHIP_BG:
                        return ""
                    fondo, tinta = CHIP_BG[v]
                    return f"background-color: {fondo}; color: {tinta}; font-weight: 600"
                st.dataframe(df.style.map(_bg, subset=["estado"]), width="stretch", hide_index=True,
                             column_config={"chip": None}, row_height=40)
            ids = [str(d.get("doc_id")) for d in vis if d.get("doc_id") is not None]
            sel_prev = st.session_state.get("a1_doc_sel")
            if sel_prev and sel_prev not in ids and sel_prev in por_id:
                ids = [sel_prev] + ids
            if ids:
                if st.session_state.get("a1_doc_sel") not in ids:
                    pend_ids = [i for i in ids if por_id.get(i, {}).get("estado") == "pendiente_revision"]
                    st.session_state["a1_doc_sel"] = (pend_ids or ids)[0]
                sel = st.selectbox("Documento", ids, key="a1_doc_sel",
                                   format_func=lambda i: f"{chip_txt(por_id.get(i, {}).get('estado'))} · "
                                                         f"{por_id.get(i, {}).get('codigo') or i} · "
                                                         f"{str(por_id.get(i, {}).get('nombre_norma') or '')[:70]}")
                base = por_id.get(sel, {})
                det, e_det = api("GET", f"/documentos/{sel}")
                if e_det:
                    st.warning(f"Detalle no disponible: {e_det}")
                det = det if isinstance(det, dict) else {}
                doc = {**base, **det}
                ds, e_ds = api("GET", "/discrepancias", params={"doc_id": sel})
                discs = ds if isinstance(ds, list) else (det.get("discrepancias") or [])
                n_altas = sum(1 for x in discs if isinstance(x, dict) and x.get("severidad") == "alta")

                with st.container(border=True):
                    st.caption(str(doc.get("codigo") or sel))
                    st.markdown(f"### {md(doc.get('nombre_norma') or doc.get('codigo') or sel)}")
                    h = st.columns(6)
                    with h[0]:
                        st.markdown(":gray[ESTADO]")
                        chip(doc.get("estado"))
                    for col, lbl, val in ((h[1], "TIPO", doc.get("tipo_fuente")), (h[2], "VERSIÓN", doc.get("version")),
                                          (h[3], "PUBLICADO",
                                           "sí" if doc.get("publicado") in (1, True, "1") else "no"),
                                          (h[4], "REEMPLAZA A", doc.get("reemplaza_a")),
                                          (h[5], "REEMPLAZADA POR", doc.get("reemplazada_por"))):
                        col.markdown(f":gray[{lbl}]  \n{md(val or '—')}")
                if doc.get("estado_inferido") or doc.get("requiere_confirmacion"):
                    st.info("Estado inferido automáticamente" +
                            (" · requiere confirmación humana" if doc.get("requiere_confirmacion") else ""))
                if doc.get("estado") == "pendiente_revision":
                    banner_revision(doc, n_altas, kp=f"a1d_{sel}")
                editor_areas(doc, sel)

                dt = st.tabs(["Metadatos", "Afirmaciones", "Discrepancias", "Traza"])
                with dt[0]:
                    meta = _js(doc.get("metadatos_json")) or {k: doc.get(k) for k in
                           ["codigo", "nombre_norma", "entidad", "tipo_fuente", "fecha_emision", "fecha_vigencia",
                            "version", "estado"] if k in doc}
                    conf = _js(doc.get("confianza_json"))
                    if isinstance(meta, dict) and meta:
                        st.dataframe(pd.DataFrame([{"campo": k, "valor": str(v) if v is not None else "",
                                                    "confianza": str(conf.get(k, "")) if isinstance(conf, dict)
                                                    else ""} for k, v in meta.items()]),
                                     width="stretch", hide_index=True)
                    else:
                        st.info("Sin metadatos.")
                    st.caption(f"Fecha emisión: {md(doc.get('fecha_emision') or '—')} · vigencia: "
                               f"{md(doc.get('fecha_vigencia') or '—')} · fragmentos: {md(doc.get('n_chunks') or 0)}")
                with dt[1]:
                    af = [x for x in (det.get("afirmaciones") or []) if isinstance(x, dict)]
                    if af:
                        st.dataframe(pd.DataFrame(af)[[c for c in ["articulo", "tema", "valor", "unidad",
                                     "condicion", "cita_literal"] if c in pd.DataFrame(af).columns]],
                                     width="stretch", hide_index=True)
                    else:
                        st.info("Sin afirmaciones extraídas.")
                with dt[2]:
                    if e_ds and not det.get("discrepancias"):
                        st.warning(f"Discrepancias no disponibles: {e_ds}")
                    else:
                        render_discrepancias(discs, kp=f"a1dd_{sel}")
                with dt[3]:
                    tr, e_tr = api("GET", f"/documentos/{sel}/traza")
                    if e_tr:
                        st.warning(f"Traza no disponible: {e_tr}")
                    elif not tr:
                        st.info("Sin pasos registrados.")
                    else:
                        tdf = pd.DataFrame([x for x in tr if isinstance(x, dict)])
                        st.dataframe(tdf[[c for c in ["timestamp", "paso", "modelo", "tokens", "latencia_ms",
                                                      "usuario", "entrada", "salida"] if c in tdf.columns]],
                                     width="stretch", hide_index=True)
                        if "latencia_ms" in tdf.columns:
                            st.caption(f"Latencia total: {pd.to_numeric(tdf['latencia_ms'], errors='coerce').sum():.0f} ms"
                                       f" · {len(tdf)} pasos")

    # ---- Informe global de discrepancias
    with sub[2]:
        ds_all, e_all = api("GET", "/discrepancias")
        if e_all:
            st.warning(f"El servicio de discrepancias no está disponible: {e_all}")
        else:
            render_discrepancias(ds_all if isinstance(ds_all, list) else [], kp="a1g")

# ------------------------------------------------------------------ Trazas
with tabs[2]:
    seccion("Trazas", "Ejecución paso a paso de cada agente: entradas, herramientas, fragmentos con score y salida.")
    ICON = {"input": "Entrada", "thought": "Razonamiento", "tool_call": "Herramienta", "tool_result": "Resultado",
            "final": "Final", "error": "Error", "verificacion": "Verificación", "handoff": "Traspaso",
            "guardrail": "Control"}

    def _detalle_run(run_id: str) -> None:
        """Detalle de una ejecución de agente (tabla trazas): pasos, herramientas y fragmentos con score."""
        det, err = api("GET", f"/trazas/{run_id}")
        if err:
            st.warning(err)
            return
        st.markdown(f"**Entrada:** {md(det.get('entrada'))}")
        st.markdown(f"**Salida:** {md(det.get('salida'))}")
        st.caption(f"modelo {det.get('modelo')} · {det.get('latencia_ms')} ms · {det.get('estado')} · "
                   f"inicio {det.get('started_at') or '-'} · fin {det.get('finished_at') or '-'}")
        for p in det.get("pasos", []):
            data = p.get("data") or {}
            with st.expander(f"**{p.get('n')}** · {ICON.get(p.get('type'), p.get('type'))} · {p.get('agent')} — "
                             f"{(p.get('summary') or '')[:110]}"):
                if p.get("type") == "tool_call":
                    st.json(data.get("arguments", {}))
                elif p.get("type") == "tool_result":
                    res = data.get("result", {})
                    hits = (res.get("hits") or res.get("no_vigentes") or res.get("fragmentos")
                            or res.get("candidatos")) if isinstance(res, dict) else None
                    if hits and isinstance(hits, list):
                        dfh = pd.DataFrame(hits)
                        cols = [c for c in ["id", "chunk_id", "best_score", "score", "rerank_score", "doc_id",
                                            "articulo", "version", "estado"] if c in dfh.columns]
                        st.dataframe(dfh[cols] if cols else dfh, width="stretch", hide_index=True)
                    else:
                        st.json(res)
                else:
                    st.write(data.get("full") or p.get("summary"))
        if det.get("consulta"):
            st.markdown("**Resultado verificado**")
            st.json(det["consulta"], expanded=False)

    @st.dialog("Cómo razonó el agente", width="large")
    def _popup_razono(run_id: str, consulta: str) -> None:
        """Ventana emergente con la traza de una consulta del chat."""
        if consulta:
            st.caption(f"Consulta: {md(consulta[:300])}")
        _detalle_run(run_id)

    def _lista_runs(agentes: list[str], clave: str) -> None:
        filas = []
        for ag in agentes:
            ts, err = api("GET", "/trazas", params={"agente": ag, "limit": 100})
            if err:
                st.warning(f"No se pudieron cargar las trazas de {ag}: {err}")
            elif isinstance(ts, list):
                filas.extend(ts)
        if not filas:
            st.info("Aún no hay ejecuciones registradas para este agente.")
            return
        filas.sort(key=lambda t: t.get("created_at") or "", reverse=True)
        if clave == "a2":  # chat: una sola tabla, con el botón "Cómo razonó" en cada fila
            anchos = [1.5, 2, 1.5, 5, 1.2, 1.1, 1.6]
            cab = st.columns(anchos)
            for col, txt in zip(cab, ["Fecha", "Usuario", "Agente", "Consulta", "Estado", "Latencia", ""]):
                col.caption(txt.upper())
            for t in filas[:50]:
                c = st.columns(anchos, vertical_alignment="center")
                c[0].caption((t.get("created_at") or "")[5:16].replace("T", " "))
                c[1].caption(md((t.get("usuario") or "—").split(":")[0]))
                c[2].caption(md(t.get("agente") or ""))
                c[3].markdown(md((t.get("entrada") or "")[:120]))
                c[4].caption(md(t.get("estado") or ""))
                lat = t.get("latencia_ms")
                c[5].caption(f"{lat / 1000:.1f} s" if isinstance(lat, (int, float)) else "—")
                if c[6].button("Cómo razonó", key=f"rz_{t['run_id']}"):
                    _popup_razono(t["run_id"], t.get("entrada") or "")
            return
        df = pd.DataFrame(filas)
        st.dataframe(df[[c for c in ["created_at", "run_id", "agente", "canal", "usuario", "entrada", "estado",
                                     "latencia_ms"] if c in df.columns]], width="stretch", hide_index=True)
        opciones = {f"{t['run_id']} · {t.get('agente')} · {(t.get('entrada') or '')[:60]}": t["run_id"]
                    for t in filas}
        sel = st.selectbox("Ver detalle", list(opciones), key=f"sel_{clave}")
        if sel:
            _detalle_run(opciones[sel])

    t_a1, t_a2 = st.tabs(["Agente 1 · Ingesta y validación", "Agente 2 · Chat de consultas y casos"])

    with t_a1:
        st.caption("Pipeline de ingesta por documento (recepción, hash, metadatos con confianza, chunks, "
                   "afirmaciones, candidatos, comparación, discrepancias confirmadas/descartadas, decisión) "
                   "y ejecuciones del agente validador.")
        docs, err = api("GET", "/documentos")
        if err:
            st.warning(f"No se pudieron cargar los documentos: {err}")
            docs = []
        docs = docs if isinstance(docs, list) else []
        st.markdown("#### Traza de ingesta por documento")
        if not docs:
            st.info("Aún no hay documentos cargados.")
        else:
            op = {f"{d.get('codigo') or d.get('doc_id')} · {d.get('nombre_norma') or ''} · {d.get('estado') or ''}":
                  d.get("doc_id") for d in docs}
            sel_doc = st.selectbox("Documento", list(op), key="tr_doc")
            pasos, err = api("GET", f"/documentos/{op[sel_doc]}/traza") if sel_doc else ([], None)
            if err:
                st.warning(f"Traza de ingesta no disponible: {err}")
            elif not pasos:
                st.info("Este documento no tiene pasos de ingesta registrados.")
            else:
                dfp = pd.DataFrame(pasos)
                st.dataframe(dfp[[c for c in ["timestamp", "paso", "modelo", "tokens", "latencia_ms", "usuario"]
                                  if c in dfp.columns]], width="stretch", hide_index=True)
                if "latencia_ms" in dfp.columns:
                    st.caption(f"latencia total: {pd.to_numeric(dfp['latencia_ms'], errors='coerce').sum():.0f} ms "
                               f"· {len(dfp)} pasos")
                for p in pasos:
                    with st.expander(f"{p.get('paso')} · {p.get('latencia_ms') or 0} ms"):
                        c1, c2 = st.columns(2)
                        c1.markdown("**Entrada**")
                        c1.code(str(p.get("entrada") or ""), language="json")
                        c2.markdown("**Salida**")
                        c2.code(str(p.get("salida") or ""), language="json")
        st.markdown("#### Ejecuciones del agente validador")
        _lista_runs(["validador"], "a1")

    with t_a2:
        st.caption("Agente de consultas y agente analista de casos (chat): herramientas usadas, fragmentos "
                   "recuperados con ID y score, verificación de citas y salida.")
        _lista_runs(["consultas", "analista_casos"], "a2")

# ------------------------------------------------------------------ Dashboard
with tabs[3]:
    seccion("Dashboard", "Uso del sistema, cobertura normativa y discrepancias activas.")
    dash, err = api("GET", "/dashboard")
    if err:
        st.warning(f"No se pudo cargar el dashboard: {err}")
    else:
        u = dash.get("uso", {})
        m = st.columns(6)
        for col, lbl, val in ((m[0], "Consultas", u.get("consultas_total", 0)), (m[1], "Hoy", u.get("consultas_hoy", 0)),
                              (m[2], "% cubiertas", f"{u.get('pct_cubiertas', 0)}%"),
                              (m[3], "Latencia p50", f"{u.get('latencia_p50_ms', 0)} ms"),
                              (m[4], "Documentos", u.get("documentos", 0)), (m[5], "Fragmentos", u.get("chunks", 0))):
            col.container(border=True).metric(lbl, val)
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown("**Normas más consultadas**")
            tn = dash.get("top_normas") or []
            if tn and hay_valores(pd.DataFrame(tn).get("n", pd.Series(dtype=float))):
                st.bar_chart(pd.DataFrame(tn).set_index("nombre_norma")["n"], horizontal=True, color="#0F4C5C")
            else:
                st.caption("Sin datos aún.")
        with c2.container(border=True):
            st.markdown("**Temas sin cobertura**")
            sc = dash.get("sin_cobertura") or []
            if not sc:
                st.caption("Todas las consultas tuvieron respaldo.")
            for s in sc:
                st.markdown(f"**{s['tema']}** · {s['n']} consulta(s)")
                for ej in s.get("ejemplos", []):
                    st.caption(f"• {ej}")
        disc = dash.get("discrepancias", {})
        with st.container(border=True):
            st.markdown(f"**Discrepancias activas** &nbsp; :gray-badge[{disc.get('total', 0)}]")
            if disc.get("por_tipo") and hay_valores(pd.Series(disc["por_tipo"])):
                st.bar_chart(pd.Series(disc["por_tipo"], name="n"), color="#0F4C5C")
            if disc.get("recientes"):
                st.dataframe(pd.DataFrame(disc["recientes"]), width="stretch", hide_index=True)

# ------------------------------------------------------------------ KPIs
SD = "sin datos"


def _g(d, *keys, default=None):
    """Acceso seguro a dicts anidados (tolera None / tipos inesperados)."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def _pct(v) -> str:
    try:
        return SD if v is None else f"{float(v) * 100:.1f} %"
    except (TypeError, ValueError):
        return SD


def _num(v, fmt="{:.1f}", suf="") -> str:
    try:
        return SD if v is None else fmt.format(float(v)) + suf
    except (TypeError, ValueError):
        return SD


def _desglose(d) -> str:
    d = d if isinstance(d, dict) else {}
    return " · ".join(f"{k}: {v}" for k, v in d.items()) if d else SD


def _lista(x):
    return ", ".join(map(str, x)) if isinstance(x, list) else x


def _kpi_pct(col, titulo: str, k, unidad: str = "consultas", extra: str | None = None) -> None:
    with col.container(border=True):
        st.metric(titulo, _pct(_g(k, "valor")))
        num, den = _g(k, "num", default=0), _g(k, "den", default=0)
        st.caption(f"{num} de {den} {unidad}" if den else f"0 de 0 {unidad} · {SD}")
        if extra:
            st.caption(extra)


def _df(rows, cols: list[str]):
    if not isinstance(rows, list) or not rows:
        return None
    df = pd.DataFrame([r for r in rows if isinstance(r, dict)])
    if df.empty:
        return None
    sel = [c for c in cols if c in df.columns]
    return df[sel] if sel else df


def _barras_area(rows, nombre: str) -> None:
    """Barras {area, n}; «sin datos» si vacío o con forma inesperada."""
    dfa = _df(rows, ["area", "n"])
    if dfa is None or not {"area", "n"} <= set(dfa.columns):
        st.caption(SD)
        return
    dfa = dfa.copy()
    dfa["area"] = dfa["area"].fillna("sin área").astype(str)
    dfa["n"] = pd.to_numeric(dfa["n"], errors="coerce").fillna(0)
    if dfa.empty or not dfa["n"].sum():
        st.caption(SD)
        return
    st.bar_chart(dfa.groupby("area")["n"].sum().rename(nombre), color="#0F4C5C")


def fetch_kpis(params: dict):
    """GET /api/kpis; devuelve (datos, error). Nunca lanza."""
    return api("GET", "/kpis", params=params, timeout=60)


with tabs[4]:
    import datetime as _dt

    seccion("KPIs", "Valor medible de los agentes, calculado desde consultas, trazas y discrepancias registradas.")
    hoy = _dt.date.today()
    f = st.columns([1, 1, 1, 1, 1.3])
    desde = f[0].date_input("Desde", value=hoy, key="k_desde")
    hasta = f[1].date_input("Hasta", value=hoy, key="k_hasta")
    gran = f[2].selectbox("Granularidad tendencia", ["10min", "hora", "dia"], index=1, key="k_gran")
    umbral_txt = f[3].text_input("Umbral de score (vacío = backend)", value="", key="k_umbral")
    t_manual = f[4].number_input("Tiempo manual de referencia (min)", min_value=0.0, value=15.0, step=1.0,
                                 key="k_tman")
    params = {"desde": str(desde), "hasta": str(hasta), "granularidad": gran, "tiempo_manual_min": t_manual}
    if umbral_txt.strip():
        try:
            params["umbral"] = float(umbral_txt.replace(",", "."))
        except ValueError:
            st.warning("Umbral inválido: se usa el del backend.")

    try:
        kp, err = fetch_kpis(params)
    except Exception as e:  # el fetch no debería lanzar, pero por si acaso
        kp, err = None, f"{type(e).__name__}: {e}"
    if err or not isinstance(kp, dict):
        st.warning(f"No se pudieron cargar los KPIs: {err or 'respuesta inválida'}")
        kp = {}
    try:
        cal, prev = _g(kp, "calidad", default={}), _g(kp, "prevencion", default={})
        efi, traz = _g(kp, "eficiencia", default={}), _g(kp, "trazabilidad", default={})
        tend = _g(kp, "tendencia", default={})
        total = _g(cal, "total_consultas", default=0)
        st.caption(f"Backend de búsqueda: **{_g(kp, 'backend', default=SD)}** · umbral usado: "
                f"**{_g(kp, 'umbral', default='el del backend')}** · consultas en el rango: **{total}** "
                f"({_desglose(_g(cal, 'por_tipo'))}) · generado: {_g(kp, 'generado_at', default=SD)}")
        if not total:
            st.warning("Sin consultas registradas en el rango: los indicadores muestran «sin datos» "
                       "(no se inventan cifras).")

        AGENTE1 = ["validador"]
        AGENTE2 = ["consultas", "analista_casos"]
        tr = _g(efi, "tiempo_respuesta_s", default={})
        pa = _g(tr, "por_agente", default={})
        pa = pa if isinstance(pa, dict) else {}
        tc = _g(traz, "trazas_completas", default={})
        tc_pa = _g(tc, "por_agente", default={})
        tc_pa = tc_pa if isinstance(tc_pa, dict) else {}
        inc = _g(tc, "incompletas", default=[])
        inc = inc if isinstance(inc, list) else []

        def _tiempos(agentes: list[str], col) -> None:
            """KPI 8 para un grupo de agentes: promedio ponderado por n, desde la traza."""
            filas = [{"agente": a, "promedio_s": _g(pa[a], "promedio"), "mediana_s": _g(pa[a], "mediana"),
                      "n": _g(pa[a], "n", default=0)} for a in agentes if a in pa]
            con = [f for f in filas if f["promedio_s"] is not None and f["n"]]
            n = sum(f["n"] for f in con)
            prom = sum(f["promedio_s"] * f["n"] for f in con) / n if n else None
            with col.container(border=True):
                st.metric("8 · Tiempo promedio de respuesta", _num(prom, suf=" s"))
                st.caption(f"medido en la traza (inicio → fin) · n = {n}")
                if filas:
                    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)

        def _trazas(agentes: list[str], col) -> None:
            """KPI 10 para un grupo de agentes."""
            num = sum(_g(tc_pa.get(a), "num", default=0) for a in agentes)
            den = sum(_g(tc_pa.get(a), "den", default=0) for a in agentes)
            _kpi_pct(col, "10 · Trazas completas (objetivo 100 %)", {"valor": num / den if den else None,
                                                                      "num": num, "den": den},
                     unidad="ejecuciones")
            mias = [x for x in inc if isinstance(x, dict) and x.get("agente") in agentes]
            with col.expander(f"Trazas incompletas ({len(mias)})"):
                dfi = _df(mias, ["run_id", "agente", "faltan"])
                if dfi is not None:
                    if "faltan" in dfi.columns:
                        dfi["faltan"] = dfi["faltan"].apply(_lista)
                    st.dataframe(dfi, width="stretch", hide_index=True)
                else:
                    st.caption("ninguna")

        tab1, tab2, tab3 = st.tabs(["Agente 1 · Ingesta y validación",
                                    "Agente 2 · Chat de consultas y casos", "Cómo se calcula"])

        # ================================================================ Agente 1
        with tab1:
            st.caption("Agente validador: revisa cada norma cargada contra el corpus y registra contradicciones "
                       "y actualizaciones. Valor de prevención.")
            b = st.columns(3)
            disc = _g(prev, "discrepancias", default={})
            with b[0].container(border=True):
                st.metric("5 · Discrepancias detectadas", _g(disc, "total", default=0))
                st.caption(f"por severidad: {_desglose(_g(disc, 'por_severidad'))}")
                st.caption(f"por tipo: {_desglose(_g(disc, 'por_tipo'))}")
                ps = _g(disc, "por_severidad", default={})
                if isinstance(ps, dict) and ps and hay_valores(pd.Series(ps)):
                    st.bar_chart(pd.Series(ps, name="discrepancias"), color="#0F4C5C")
            obs = _g(prev, "normas_obsoletas", default={})
            with b[1].container(border=True):
                st.metric("6 · Normas obsoletas o reemplazadas", _g(obs, "total", default=0))
                dfo = _df(_g(obs, "lista"), ["doc_id", "nombre_norma", "estado", "reemplazada_por"])
                if dfo is not None:
                    st.dataframe(dfo, width="stretch", hide_index=True)
                else:
                    st.caption(SD)
            with b[2].container(border=True):
                st.markdown("**7 · Documentos afectados por norma**")
                dfa = _df(_g(prev, "afectados_por_norma"), ["nombre_norma", "doc_id", "n", "afectados"])
                if dfa is not None:
                    if "afectados" in dfa.columns:
                        dfa["afectados"] = dfa["afectados"].apply(_lista)
                    st.dataframe(dfa, width="stretch", hide_index=True)
                else:
                    st.caption(SD)
            e1 = st.columns(2)
            _tiempos(AGENTE1, e1[0])
            _trazas(AGENTE1, e1[1])
            with st.container(border=True):
                st.markdown("**Documentos por área de aplicación**")
                _barras_area(_g(kp, "por_area", "documentos"), "documentos")

        # ================================================================ Agente 2
        with tab2:
            st.caption("Agente de consultas y agente analista de casos (chat): calidad de las respuestas, "
                       "eficiencia, trazabilidad y brechas normativas.")
            st.markdown("#### Calidad de las respuestas")
            a = st.columns(4)
            _kpi_pct(a[0], "1 · Con cita verificable", _g(cal, "con_cita"))
            sc = _g(cal, "sin_cobertura", default={})
            _kpi_pct(a[1], "2 · Sin cobertura", sc,
                     extra=f"no encontró: {_g(sc, 'no_encontro', default=0)} · score bajo: "
                           f"{_g(sc, 'score_bajo', default=0)}")
            ad = _g(cal, "con_advertencia", default={})
            _kpi_pct(a[2], "3 · Con advertencia", ad,
                     extra=f"conflicto: {_g(ad, 'conflicto', default=0)} · obsoleta: {_g(ad, 'obsoleta', default=0)}")
            cp = _g(cal, "confianza_promedio", default={})
            with a[3].container(border=True):
                st.metric("4 · Confianza promedio", _num(_g(cp, "valor"), "{:.2f}"))
                st.caption(f"sobre {_g(cp, 'n', default=0)} consultas")
                dist = _g(cp, "distribucion", default={})
                if isinstance(dist, dict) and dist:
                    st.caption(f"alta: {dist.get('alta', 0)} · media: {dist.get('media', 0)} · "
                               f"sin respaldo: {dist.get('sin_respaldo', 0)}")
                else:
                    st.caption(f"distribución: {SD}")

            st.markdown("#### Eficiencia y trazabilidad")
            c = st.columns(3)
            _tiempos(AGENTE2, c[0])
            ta = _g(efi, "tiempo_ahorrado", default={})
            with c[1].container(border=True):
                pill("Estimado · supuesto editable", "orange")
                st.metric("9 · Tiempo ahorrado (estimado)", _num(_g(ta, "horas"), suf=" h"))
                st.caption(f"Supuesto: {_g(ta, 'supuesto', default=SD)}")
                st.caption(f"= {_g(ta, 'consultas', default=0)} consultas × "
                           f"({_g(ta, 'tiempo_manual_min', default=t_manual)} min − tiempo promedio del agente). "
                           "No es una medición: ajusta el tiempo manual arriba.")
            _trazas(AGENTE2, c[2])

            st.markdown("#### Normas consultadas y brechas")
            g = st.columns(2)
            with g[0].container(border=True):
                st.markdown("**11 · Top 10 normas más consultadas**")
                dft = _df(_g(traz, "top_normas"), ["nombre_norma", "doc_id", "n"])
                if dft is not None and "n" in dft.columns and hay_valores(dft["n"]):
                    dft = dft.head(10)
                    idx = "nombre_norma" if "nombre_norma" in dft.columns else dft.columns[0]
                    st.bar_chart(dft.set_index(idx)["n"], horizontal=True, color="#0F4C5C")
                else:
                    st.caption(SD)
            with g[1].container(border=True):
                st.markdown("**11 · Normas que faltan (brechas por tema)**")
                dfb = _df(_g(tend, "brechas_por_tema"), ["tema", "n"])
                if dfb is not None and {"tema", "n"} <= set(dfb.columns) and hay_valores(dfb["n"]):
                    st.bar_chart(dfb.set_index("tema")["n"], color="#0F4C5C")
                br = _g(traz, "brechas", default=[])
                if isinstance(br, list) and br:
                    with st.expander("Ejemplos por tema"):
                        for x in br:
                            if isinstance(x, dict):
                                st.markdown(f"**{x.get('tema', '?')}** · {x.get('n', 0)}")
                                for ej in (x.get("ejemplos") or [])[:3]:
                                    st.caption(f"• {ej}")
                else:
                    st.caption(SD)

            st.markdown("#### Por área")
            pa_cols = st.columns(2)
            with pa_cols[0].container(border=True):
                st.markdown("**Consultas por área del usuario**")
                _barras_area(_g(kp, "por_area", "consultas"), "consultas")
            with pa_cols[1].container(border=True):
                st.markdown("**Brechas por área del usuario**")
                bra = _g(kp, "por_area", "brechas", default=[])
                _barras_area(bra, "brechas")
                bra = [x for x in bra if isinstance(x, dict) and x.get("ejemplos")] if isinstance(bra, list) else []
                if bra:
                    with st.expander("Ejemplos por área"):
                        for x in bra:
                            st.markdown(f"**{md(x.get('area') or 'sin área')}** · {md(x.get('n', 0))}")
                            ejs = x.get("ejemplos")
                            for ej in (ejs if isinstance(ejs, list) else [ejs])[:3]:
                                st.caption(f"• {md(ej)}")

            st.markdown(f"#### Tendencia de consultas ({_g(tend, 'granularidad', default=gran)})")
            dfc = _df(_g(tend, "consultas"), ["t", "n", "sin_cobertura"])
            ycols = [x for x in ["n", "sin_cobertura"] if dfc is not None and x in dfc.columns]
            if dfc is not None and "t" in dfc.columns and ycols and hay_valores(dfc[ycols]):
                # barras: con un solo bucket una línea no se dibuja
                st.bar_chart(dfc.set_index("t")[ycols], stack=False,
                             color=["#0F4C5C", "#AEB8BF"][:len(ycols)])
            else:
                st.caption(SD)

        # ================================================================ Cómo se calcula
        with tab3:
            st.markdown(
                "Fuente: consultas, trazas, discrepancias y documentos registrados por los agentes en el rango "
                "filtrado. `null` ⇒ «sin datos».\n\n"
                "1. **% con cita verificable** = consultas con ≥1 cita verificada / total de consultas.\n"
                "2. **% sin cobertura** = consultas sin respaldo / total; desglose *no_encontro* (búsqueda sin "
                "resultados) y *score_bajo* (mejor score < umbral).\n"
                "3. **% con advertencia** = consultas con ≥1 advertencia / total; desglose *conflicto* "
                "(discrepancia entre normas) y *obsoleta* (norma no vigente involucrada).\n"
                "4. **Confianza promedio** = promedio sobre las n consultas con respaldo; distribución por nivel "
                "alta / media / sin_respaldo.\n"
                "5. **Discrepancias** = registros del agente validador, por severidad y por tipo.\n"
                "6. **Normas obsoletas** = documentos no vigentes (reemplazada / derogada) con su reemplazo.\n"
                "7. **Documentos afectados por norma** = documentos que referencian cada norma afectada.\n"
                "8. **Tiempo de respuesta** = latencia registrada en la traza (s): promedio y mediana, total y por "
                "agente.\n"
                "9. **Tiempo ahorrado (ESTIMADO)** = consultas × (tiempo manual de referencia − tiempo promedio "
                "del agente). El tiempo manual es un supuesto editable, no una medición.\n"
                "10. **% trazas completas** = ejecuciones con traza completa / ejecuciones; objetivo 100 %. Las "
                "incompletas indican qué falta.\n"
                "11. **Top normas** = normas más citadas (top 10); **brechas** = temas consultados sin cobertura, "
                "con conteo y ejemplos.")
    except Exception as e:  # nunca romper la pestaña ante una forma inesperada
        st.warning(f"Los KPIs llegaron con un formato inesperado: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ Usuarios
with tabs[5]:
    seccion("Usuarios", "Cuentas de acceso al chat y su área, usada para filtrar normas aplicables.")
    if st.session_state.get("u_flash"):
        st.success(st.session_state.pop("u_flash"))
    us, e_us = api("GET", "/usuarios")
    if e_us:
        st.warning(f"El servicio de usuarios no está disponible: {e_us}")
    else:
        dfu = _df(us if isinstance(us, list) else [], ["usuario", "rol", "area", "created_at"])
        if dfu is None:
            st.info("Aún no hay usuarios registrados.")
        else:
            dfu = dfu.rename(columns={"area": "área", "created_at": "creado"})
            st.dataframe(dfu, width="stretch", hide_index=True)

    st.markdown("#### Crear usuario")
    with st.form("u_form", clear_on_submit=False):
        f1, f2 = st.columns(2)
        nu = f1.text_input("Usuario", key="u_usuario")
        nc = f2.text_input("Clave", type="password", key="u_clave")
        f3, f4 = st.columns(2)
        nr = f3.selectbox("Rol", ["empleado", "admin"], key="u_rol")
        na = f4.selectbox("Área", areas_lista(), key="u_area")
        enviar_u = st.form_submit_button("Crear usuario", type="primary")
    if enviar_u:
        if not (nu or "").strip() or not nc:
            st.error("Usuario y clave son obligatorios.")
        else:
            r, e = api("POST", "/usuarios", json={"usuario": nu.strip(), "clave": nc, "rol": nr, "area": na})
            if e:
                st.error(f"No se pudo crear el usuario: {e}")
            else:
                st.session_state["u_flash"] = f"Usuario '{nu.strip()}' creado ({nr} · {na})."
                st.rerun()
