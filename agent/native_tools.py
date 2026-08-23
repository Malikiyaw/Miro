"""Provider-native tool schema adapter for Miro V9."""
from agent.tool_registry import TOOL_SPECS, tool_registry


def provider_tool_schemas():
    """Return OpenAI-compatible function-tool definitions from the canonical registry."""
    schemas=[]
    for name in sorted(TOOL_SPECS):
        spec=tool_registry.get(name)
        props={}; required=[]
        for key, rule in (spec.get("parameters") or {}).items():
            rule=rule if isinstance(rule,dict) else {}
            typ=rule.get("type","string")
            if typ not in {"string","integer","number","boolean","array","object"}: typ="string"
            prop={"type":typ}
            if typ=="array": prop["items"]={"type":"integer"}
            props[key]=prop
            if rule.get("required"): required.append(key)
        schemas.append({"type":"function","function":{"name":name,"description":spec.get("description","")[:500],"parameters":{"type":"object","properties":props,"required":required,"additionalProperties":False}}})
    return schemas


def install_on_bot(bot):
    """Install the canonical schemas once; provider clients read this attribute."""
    schemas=provider_tool_schemas()
    bot.agent_tool_schemas=schemas
    return schemas
