"""Nova Forge Agent Registry — load, discover, and route agent definitions.

Agent definitions are YAML files in the agents/ directory. Each definition
specifies a system prompt, model preference, tool policy, and formation roles.
The registry loads all definitions on init and provides discovery by keyword,
routing by formation/role, and filtering by category.

Usage::

    from forge_registry import AgentRegistry

    registry = AgentRegistry()
    print(registry.list_names())
    agent = registry.get("backend-architect")
    agent = registry.route("feature-impl", "backend-impl")
    results = registry.discover("security")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# ── Default agents directory ────────────────────────────────────────────────

_AGENTS_DIR = Path(__file__).parent / "agents"


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass
class FormationRoleMapping:
    """Maps an agent to a formation + role."""
    formation: str
    role: str


@dataclass
class OwnershipSpec:
    """File ownership patterns for an agent."""
    directories: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)


@dataclass
class AgentDefinition:
    """A loaded agent definition."""
    name: str
    description: str
    category: str               # "spec", "specialist", "meta"
    model_preference: str       # "smart" or "fast"
    effort_level: str           # "max", "high", "medium", "low"
    tool_policy: str            # "full", "coding", "testing", "readonly", "minimal"
    formation_roles: list[FormationRoleMapping] = field(default_factory=list)
    ownership: OwnershipSpec = field(default_factory=OwnershipSpec)
    system_prompt: str = ""
    source_file: Optional[Path] = None

    @property
    def is_spec_agent(self) -> bool:
        return self.category == "spec"

    @property
    def is_specialist(self) -> bool:
        return self.category == "specialist"


# ── Registry ────────────────────────────────────────────────────────────────


class AgentRegistry:
    """Loads and indexes agent definitions from YAML files.

    Provides lookup by name, discovery by keyword, and routing by
    formation + role.
    """

    def __init__(self, agents_dir: Path | None = None) -> None:
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._agents: dict[str, AgentDefinition] = {}
        self._role_index: dict[tuple[str, str], list[str]] = {}
        self._load_all()

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, name: str) -> AgentDefinition:
        """Get an agent definition by exact name.

        Raises:
            KeyError: If no agent with that name exists.
        """
        if name not in self._agents:
            available = ", ".join(sorted(self._agents.keys()))
            raise KeyError(
                f"Unknown agent {name!r}. Available: {available}"
            )
        return self._agents[name]

    def list_names(self) -> list[str]:
        """Return sorted list of all agent names."""
        return sorted(self._agents.keys())

    def list_all(self) -> list[AgentDefinition]:
        """Return all agent definitions sorted by name."""
        return [self._agents[n] for n in self.list_names()]

    def list_by_category(self, category: str) -> list[AgentDefinition]:
        """Filter agents by category ("spec", "specialist", "meta")."""
        return [a for a in self.list_all() if a.category == category]

    def discover(self, keyword: str, max_results: int = 10) -> list[AgentDefinition]:
        """Fuzzy search agents by keyword against name + description.

        Returns agents sorted by relevance (best match first).
        """
        keyword_lower = keyword.lower()
        scored: list[tuple[float, AgentDefinition]] = []

        for agent in self._agents.values():
            # Score against name and description
            name_score = _fuzzy_score(keyword_lower, agent.name.lower())
            desc_score = _fuzzy_score(keyword_lower, agent.description.lower())

            # Exact substring match in name gets a big boost
            if keyword_lower in agent.name.lower():
                name_score = max(name_score, 0.8)

            # Exact substring match in description
            if keyword_lower in agent.description.lower():
                desc_score = max(desc_score, 0.5)

            # Check formation roles too
            role_score = 0.0
            for fr in agent.formation_roles:
                if keyword_lower in fr.role.lower() or keyword_lower in fr.formation.lower():
                    role_score = 0.6

            best = max(name_score, desc_score, role_score)
            if best > 0.3:
                scored.append((best, agent))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [agent for _, agent in scored[:max_results]]

    def route(self, formation_name: str, role_name: str) -> Optional[AgentDefinition]:
        """Find the best agent for a formation + role combination.

        Returns the first matching agent, or None if no agent maps to that role.
        """
        key = (formation_name, role_name)
        agent_names = self._role_index.get(key, [])
        if agent_names:
            return self._agents[agent_names[0]]

        # Fallback: check partial role name matches
        for agent in self._agents.values():
            for fr in agent.formation_roles:
                if fr.formation == formation_name and role_name in fr.role:
                    return agent
                if fr.role == role_name and formation_name in fr.formation:
                    return agent

        return None

    def route_or_default(
        self, formation_name: str, role_name: str, default_name: str = "spec-implementer"
    ) -> AgentDefinition:
        """Route with fallback to a default agent."""
        result = self.route(formation_name, role_name)
        if result is not None:
            return result
        try:
            return self.get(default_name)
        except KeyError:
            # Last resort: return the first available agent
            if self._agents:
                return next(iter(self._agents.values()))
            raise KeyError("No agents loaded — cannot route or provide default")

    def reload(self) -> int:
        """Reload all agent definitions from disk. Returns count loaded."""
        self._agents.clear()
        self._role_index.clear()
        self._load_all()
        return len(self._agents)

    @property
    def count(self) -> int:
        """Number of loaded agents."""
        return len(self._agents)

    # ── Loading ─────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load all .yml files from the agents directory."""
        if not self._agents_dir.is_dir():
            logger.warning("Agents directory not found: %s", self._agents_dir)
            return

        yml_files = sorted(self._agents_dir.glob("*.yml"))
        if not yml_files:
            logger.warning("No .yml files found in %s", self._agents_dir)
            return

        for path in yml_files:
            try:
                agent = self._load_one(path)
                self._agents[agent.name] = agent
                # Build role index
                for fr in agent.formation_roles:
                    key = (fr.formation, fr.role)
                    self._role_index.setdefault(key, []).append(agent.name)
            except Exception as exc:
                logger.warning("Failed to load agent from %s: %s", path, exc)

        logger.debug(
            "Loaded %d agent definitions from %s", len(self._agents), self._agents_dir
        )

    @staticmethod
    def _load_one(path: Path) -> AgentDefinition:
        """Parse a single YAML agent definition file."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected YAML dict, got {type(raw).__name__}")

        name = raw.get("name", path.stem)

        # Parse formation roles
        formation_roles: list[FormationRoleMapping] = []
        for entry in raw.get("formation_roles", []):
            if isinstance(entry, dict):
                formation_roles.append(FormationRoleMapping(
                    formation=entry.get("formation", ""),
                    role=entry.get("role", ""),
                ))

        # Parse ownership
        own_raw = raw.get("ownership_patterns", {})
        ownership = OwnershipSpec(
            directories=own_raw.get("directories", []),
            files=own_raw.get("files", []),
            patterns=own_raw.get("patterns", []),
        )

        return AgentDefinition(
            name=name,
            description=raw.get("description", ""),
            category=raw.get("category", "specialist"),
            model_preference=raw.get("model_preference", "fast"),
            effort_level=raw.get("effort_level", "high"),
            tool_policy=raw.get("tool_policy", "coding"),
            formation_roles=formation_roles,
            ownership=ownership,
            system_prompt=raw.get("system_prompt", ""),
            source_file=path,
        )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _fuzzy_score(query: str, target: str) -> float:
    """Simple fuzzy match score between 0.0 and 1.0."""
    if not query or not target:
        return 0.0
    return SequenceMatcher(None, query, target).ratio()
