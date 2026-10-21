"""Pack banca: analista de riesgo -> oficial de crédito, con aprobación humana del desembolso."""
from app.agents.agent import Agent
from app.agents.flow import Flow
from sectors.banca import tools  # noqa: F401  registra tools y validadores

analista = Agent(
    name="analista_riesgo",
    role=(
        "Eres analista de riesgo crediticio. Consulta cada solicitud y el perfil de su cliente. "
        "Política: score >= 600, sin mora y cuota estimada (monto/plazo) <= 40% del ingreso mensual "
        "menos deudas. Si alguna solicitud cumple, traspasa al oficial_credito indicando solicitud, "
        "monto y tasa. Explica siempre por qué cada solicitud cumple o no."
    ),
    tools=["consultar_solicitud", "consultar_cliente"],
    handoffs=["oficial_credito"],
)

oficial = Agent(
    name="oficial_credito",
    role="Eres oficial de crédito. Aprueba exactamente los créditos indicados por el analista y resume el resultado.",
    tools=["aprobar_credito"],
)

FLOW = Flow(agents=[analista, oficial])

CASO_EJEMPLO = (
    "Evalúa las solicitudes SOL-001 y SOL-002. Aprueba las que cumplan la política con tasa anual "
    "de 18% y explica el motivo de las que no la cumplan."
)
