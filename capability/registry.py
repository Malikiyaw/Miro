"""Tool Registry 2.0 — section 3 of MIRO V11.

The LLM does not receive 150 tools blindly. CapabilityDiscovery selects
the right ToolDefinition set for the current intent.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .contract import ToolCategory, ToolDefinition, ToolResult


class ToolRegistry:
    """In-memory registry for canonical Miro capabilities."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._executors: Dict[str, Callable[..., Any]] = {}
        self._verifiers: Dict[str, Callable[..., Any]] = {}
        self._aliases: Dict[str, str] = {}

    # -- registration --
    def register(
        self,
        tool: ToolDefinition,
        *,
        executor: Optional[Callable[..., Any]] = None,
        verifier: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not tool.name:
            raise ValueError("ToolDefinition.name is required")
        if tool.name in self._tools and self._tools[tool.name].version != tool.version:
            # Allow re-registration at a new version.
            pass
        self._tools[tool.name] = tool
        if executor is not None:
            self._executors[tool.name] = executor
        if verifier is not None:
            self._verifiers[tool.name] = verifier

    def alias(self, alias: str, target: str) -> None:
        if target not in self._tools:
            raise KeyError(f"alias target {target!r} is not registered")
        self._aliases[alias] = target

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._executors.pop(name, None)
        self._verifiers.pop(name, None)
        for a, t in list(self._aliases.items()):
            if t == name or a == name:
                self._aliases.pop(a, None)

    # -- accessors --
    def get(self, name: str) -> ToolDefinition:
        key = self._aliases.get(name, name)
        if key not in self._tools:
            raise KeyError(f"tool {name!r} not registered")
        return self._tools[key]

    def exists(self, name: str) -> bool:
        return name in self._tools or name in self._aliases

    def resolve(self, name: str) -> str:
        """Return the canonical name for a tool or alias."""
        return self._aliases.get(name, name)

    def get_executor(self, name: str) -> Optional[Callable[..., Any]]:
        key = self._aliases.get(name, name)
        return self._executors.get(key)

    def get_verifier(self, name: str) -> Optional[Callable[..., Any]]:
        key = self._aliases.get(name, name)
        return self._verifiers.get(key)

    def all_names(self) -> List[str]:
        return sorted(self._tools)

    def all(self) -> List[ToolDefinition]:
        return [self._tools[n] for n in sorted(self._tools)]

    def by_category(self, category: ToolCategory) -> List[ToolDefinition]:
        return [t for t in self._tools.values() if t.category == category]

    # -- search / discovery --
    def search(self, query: str, *, limit: int = 25) -> List[ToolDefinition]:
        """Token-substring search across name, description, keywords."""
        q = (query or "").lower().strip()
        if not q:
            return list(self.all())[:limit]
        tokens = [t for t in re.split(r"[\s,]+", q) if t]
        scored: List[Tuple[int, ToolDefinition]] = []
        for tool in self._tools.values():
            hay = " ".join(
                [tool.name, tool.description, *tool.keywords, *tool.intent_examples]
            ).lower()
            score = sum(1 for tok in tokens if tok in hay)
            if score:
                scored.append((score, tool))
        scored.sort(key=lambda p: (-p[0], p[1].name))
        return [t for _, t in scored[:limit]]

    def discover(
        self, intent: str, *, limit: int = 12
    ) -> List[ToolDefinition]:
        """Semantic-ish discovery: keyword + intent-example match.

        Used by CapabilityDiscovery in the agent runtime; bare LLM does
        not see this directly.
        """
        scored: List[Tuple[int, ToolDefinition]] = []
        tokens = [t for t in re.split(r"[\W_]+", (intent or "").lower()) if t]
        if not tokens:
            return list(self.all())[:limit]
        for tool in self._tools.values():
            score = 0
            for tok in tokens:
                if tok in tool.name.lower():
                    score += 3
                if tok in tool.description.lower():
                    score += 2
                if any(tok in k.lower() for k in tool.keywords):
                    score += 2
                if any(tok in ex.lower() for ex in tool.intent_examples):
                    score += 1
            if score > 0:
                scored.append((score, tool))
        scored.sort(key=lambda p: (-p[0], p[1].name))
        return [t for _, t in scored[:limit]]

    def capabilities(self) -> Dict[str, int]:
        """Return counts grouped by ToolCategory."""
        out: Dict[str, int] = defaultdict(int)
        for tool in self._tools.values():
            out[tool.category.value] += 1
        return dict(out)

    def validate(self, name: str) -> List[str]:
        """Return a list of contract violations. Empty = OK."""
        tool = self.get(name)
        problems: List[str] = []
        if not tool.description:
            problems.append("description is required")
        if not tool.executor and name not in self._executors:
            problems.append("executor is required (or bound via register)")
        if tool.mutates_state and tool.danger_level == DangerLevel.LOW.value:
            # Mismatch: mutation but no risk. Just warn, don't fail.
            pass
        return problems

    def health(self) -> Dict[str, Any]:
        """Return a snapshot of the registry's health for observability."""
        missing_exec = [n for n in self._tools if n not in self._executors]
        return {
            "total_tools": len(self._tools),
            "total_aliases": len(self._aliases),
            "missing_executors": missing_exec,
            "capabilities": self.capabilities(),
        }

    def dependency_graph(self) -> Dict[str, List[str]]:
        """Return adjacency list for the tool dependency graph."""
        return {n: list(self._tools[n].dependencies) for n in sorted(self._tools)}


GLOBAL_REGISTRY = ToolRegistry()
