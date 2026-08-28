# Miro Bot Modules Package

# The package keeps the existing module layout intact. V8 installs a thin
# execution-first bridge around AIChatSystem so mutation requests enter the
# unified AgentHarness before the legacy conversational path can answer.
try:
    from . import ai_chat as _ai_chat
    from agent.ai_chat_bridge import install as _install_agent_bridge
    _install_agent_bridge(_ai_chat.AIChatSystem)
except Exception:
    # Never prevent the bot from importing its normal modules because an
    # optional agent bridge failed during startup. The runtime logs the actual
    # execution error when the bridge is unavailable.
    pass
