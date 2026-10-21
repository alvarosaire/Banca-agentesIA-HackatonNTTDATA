"""Pack minería: monitor de condición -> planificador de mantenimiento, con aprobación para detener equipos."""
from app.agents.agent import Agent
from app.agents.flow import Flow
from sectors.mineria import tools  # noqa: F401  registra tools y validadores

monitor = Agent(
    name="monitor",
    role=(
        "Eres el monitor de condición de equipos mineros. Consulta los equipos indicados y compara sus "
        "lecturas con sus umbrales. Si alguno los supera, traspasa al planificador indicando el equipo, "
        "las lecturas fuera de rango y la acción recomendada. Si todo está normal, responde tu diagnóstico."
    ),
    tools=["consultar_equipo"],
    handoffs=["planificador"],
)

planificador = Agent(
    name="planificador",
    role=(
        "Eres el planificador de mantenimiento. Ejecuta las acciones indicadas por el monitor "
        "(detener equipos en riesgo y programar su mantenimiento) y resume lo realizado."
    ),
    tools=["detener_equipo", "programar_mantenimiento"],
)

FLOW = Flow(agents=[monitor, planificador])

CASO_EJEMPLO = (
    "Revisa los equipos CAM-07 y PER-02. Si alguno supera sus umbrales de vibración o temperatura, "
    "deténlo y programa un mantenimiento urgente; si no, indica que opera con normalidad."
)
