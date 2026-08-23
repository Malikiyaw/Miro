"""Small compatibility patch that keeps native provider arguments intact."""
from agent.tool_registry import tool_registry
from agent.state import Observation


def install():
    from agent.runtime import AgentRuntime
    original_parse=AgentRuntime._parse_turn
    def parse(ai_result):
        summary, actions, final_answer, intent=original_parse(ai_result)
        normalized=[]
        for action in actions:
            if not isinstance(action,dict): continue
            args=action.get('parameters')
            if not isinstance(args,dict): args=action.get('arguments') if isinstance(action.get('arguments'),dict) else {}
            normalized.append({**action,'parameters':args})
        return summary,normalized,final_answer,intent
    # Must be a staticmethod: assigned onto the class it would otherwise
    # receive `self`, breaking every _parse_turn call with a TypeError.
    AgentRuntime._parse_turn=staticmethod(parse)
    return True
