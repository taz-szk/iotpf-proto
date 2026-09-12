from dataclasses import dataclass
from typing import Callable


@dataclass
class AgentTool:
    name: str
    description: str
    input_schema: dict
    read_only: bool
    handler: Callable


from . import alerts, dashboard, general, provisioning  # noqa: E402

TOOLS: list[AgentTool] = [
    *general.TOOLS,
    *provisioning.TOOLS,
    *dashboard.TOOLS,
    *alerts.TOOLS,
]
