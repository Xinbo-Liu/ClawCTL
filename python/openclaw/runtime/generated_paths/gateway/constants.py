"""Gateway 运行态派生产物常量。"""
from __future__ import annotations

from pathlib import Path

GATEWAY_AGENT_CORE_FILE_NAMES = (
    'AGENTS.md',
    'SOUL.md',
    'TOOLS.md',
    'IDENTITY.md',
    'USER.md',
    'HEARTBEAT.md',
    'BOOTSTRAP.md',
    'MEMORY.md',
)
GATEWAY_MAIN_AGENT_ID = 'main'
GATEWAY_MAIN_AGENT_NAME = 'OpenClaw 路由 Agent'
GATEWAY_ROUTER_WORKSPACE_ID = 'router_local_ro'
GATEWAY_ROUTER_WORKSPACE_ENTRY_ID = 'workspace_router_local_ro'
GATEWAY_DEFAULT_SESSION_LABEL = 'main'
GATEWAY_DEFAULT_SESSION_UUID_PREFIX = 'openclaw-gateway-default-session'
GATEWAY_UI_SKILL_GOVERNANCE_CONTRACT_ID = 'gateway.ui_skill_governance'
GATEWAY_HEALTHCHECK_SCRIPT_SOURCE_REL = Path('config/gateway/healthchecks/gateway-tcp-liveness.cjs')
GATEWAY_HEALTHCHECK_SCRIPT_STATE_REL = Path('healthchecks/gateway-tcp-liveness.cjs')
GATEWAY_INTERACTIVE_DEFAULTS = {
    'thinkingDefault': 'high',
    'verboseDefault': 'on',
    'timeoutSeconds': 1800,
}
GATEWAY_AGENT_INTERACTIVE_DEFAULTS = {
    'thinkingDefault': GATEWAY_INTERACTIVE_DEFAULTS['thinkingDefault'],
    'reasoningDefault': 'stream',
}
