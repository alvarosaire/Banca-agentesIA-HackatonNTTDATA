"""Pack manufactura: inspector de calidad -> planificador de producción, con aprobaciones humanas."""
from app.agents.agent import Agent
from app.agents.flow import Flow
from sectors.manufactura import tools  # noqa: F401  registra tools y validadores

inspector = Agent(
    name="inspector",
    role=(
        "Eres el inspector de calidad. Consulta los lotes indicados y compara su tasa de defectos con su "
        "límite. Si algún lote lo supera, traspasa al planificador indicando qué lote retener y cuántas "
        "unidades reponer en qué línea. Si todos cumplen, responde que pueden liberarse."
    ),
    tools=["consultar_lote"],
    handoffs=["planificador"],
)

planificador = Agent(
    name="planificador",
    role=(
        "Eres el planificador de producción. Ejecuta lo indicado por el inspector: retener lotes "
        "defectuosos y replanificar la producción para reponer unidades. Resume lo realizado."
    ),
    tools=["retener_lote", "replanificar_linea"],
)

FLOW = Flow(agents=[inspector, planificador])

CASO_EJEMPLO = (
    "Revisa los lotes L-2041 y L-2042. Retén los que superen su límite de defectos y replanifica en su "
    "misma línea la producción para reponer las unidades retenidas."
)
