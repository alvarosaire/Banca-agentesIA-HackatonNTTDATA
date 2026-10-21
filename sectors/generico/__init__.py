"""Pack genérico: analista -> ejecutor sobre entidades y órdenes."""
from app.agents.agent import Agent
from app.agents.flow import Flow
from sectors.generico import tools  # noqa: F401  registra tools y validadores

analista = Agent(
    name="analista",
    role=(
        "Eres el agente analista. Consultas los datos de las entidades involucradas y decides qué "
        "acciones corresponden. No ejecutas acciones: si hay que crear órdenes, traspasa el caso al "
        "ejecutor indicando exactamente qué órdenes crear (entidad, monto y descripción). "
        "Si no corresponde ninguna acción, responde con tu análisis final."
    ),
    tools=["consultar_entidad"],
    handoffs=["ejecutor"],
    thinking=None,  # None = global (OFF). Poner True para activar thinking solo en este agente.
)

ejecutor = Agent(
    name="ejecutor",
    role=(
        "Eres el agente ejecutor. Ejecutas exactamente las acciones indicadas por el analista y "
        "entregas un resumen final claro de lo realizado y de lo que no se pudo hacer."
    ),
    tools=["crear_orden"],
)

FLOW = Flow(agents=[analista, ejecutor], start="analista")

# Caso de ejemplo de punta a punta
CASO_EJEMPLO = (
    "Revisa las entidades ENT-001 y ENT-002. A cada una que esté activa créale una orden de 3000 "
    "por 'mantenimiento preventivo'. Luego resume qué se hizo."
)
