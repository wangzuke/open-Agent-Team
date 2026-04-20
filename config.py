"""Global configuration for open-teams."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


CONFIG_FILE_NAME = "open_teams.json"


@dataclass
class OpenTeamsConfig:
    project_root: Path = field(default_factory=lambda: Path.cwd())
    workspace_dir: Path = field(default_factory=lambda: Path.cwd() / ".open_teams")
    teams_dir: Path = field(default=None)
    tasks_dir: Path = field(default=None)
    logs_dir: Path = field(default=None)
    inboxes_dir: Path = field(default=None)

    # LLM provider: "anthropic" or "openai" (covers any OpenAI-compatible API)
    provider: str = "anthropic"
    default_model: str = "claude-sonnet-4-6"
    leader_model: str = "claude-opus-4-6"
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    base_url: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_BASE_URL"))

    max_tokens: int = 16384
    max_turns: int = 200
    max_agent_turns: int = 50
    temperature: float = 0.0

    team_name: str = "default"

    def __post_init__(self):
        self.project_root = Path(self.project_root)
        self.workspace_dir = Path(self.workspace_dir)
        if self.teams_dir is None:
            self.teams_dir = self.workspace_dir / "teams"
        else:
            self.teams_dir = Path(self.teams_dir)
        if self.tasks_dir is None:
            self.tasks_dir = self.workspace_dir / "tasks"
        else:
            self.tasks_dir = Path(self.tasks_dir)
        if self.logs_dir is None:
            self.logs_dir = self.workspace_dir / "logs"
        else:
            self.logs_dir = Path(self.logs_dir)
        if self.inboxes_dir is None:
            self.inboxes_dir = self.workspace_dir / "inboxes"
        else:
            self.inboxes_dir = Path(self.inboxes_dir)

    def ensure_dirs(self):
        for d in [self.workspace_dir, self.teams_dir, self.tasks_dir,
                  self.logs_dir, self.inboxes_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def team_dir(self, team_name: str) -> Path:
        return self.teams_dir / team_name

    def team_tasks_dir(self, team_name: str) -> Path:
        return self.tasks_dir / team_name

    def team_inboxes_dir(self, team_name: str) -> Path:
        return self.inboxes_dir / team_name

    def agent_inbox_path(self, team_name: str, agent_name: str) -> Path:
        return self.team_inboxes_dir(team_name) / f"{agent_name}.json"

    def agent_log_path(self, team_name: str, agent_name: str) -> Path:
        return self.logs_dir / team_name / f"{agent_name}.jsonl"

    # ----- Serialization -----

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict (Path -> str, skip computed dirs)."""
        return {
            "provider": self.provider,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "leader_model": self.leader_model,
            "default_model": self.default_model,
            "max_tokens": self.max_tokens,
            "max_turns": self.max_turns,
            "max_agent_turns": self.max_agent_turns,
            "temperature": self.temperature,
            "team_name": self.team_name,
            "project_root": str(self.project_root),
            "workspace_dir": str(self.workspace_dir),
        }

    def save_to_file(self, path: Path | str):
        """Write current config to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_file(cls, path: Path | str) -> "OpenTeamsConfig":
        """Load config from a JSON file. Unknown keys are silently ignored."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "OpenTeamsConfig":
        """Build an OpenTeamsConfig from a plain dict, converting types."""
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {}
        for k, v in data.items():
            if k not in field_names:
                continue
            if v is None:
                filtered[k] = v
                continue
            field_obj = cls.__dataclass_fields__[k]
            if field_obj.type in ("Path", "Path | None") or k.endswith("_dir") or k == "project_root":
                filtered[k] = Path(v) if v else None
            else:
                filtered[k] = v
        return cls(**filtered)

    def merge(self, overrides: dict[str, Any]) -> "OpenTeamsConfig":
        """Return a new config with non-None overrides applied on top."""
        base = self.to_dict()
        for k, v in overrides.items():
            if v is not None:
                base[k] = v
        return self._from_dict(base)


_config: OpenTeamsConfig | None = None


def get_config() -> OpenTeamsConfig:
    global _config
    if _config is None:
        _config = OpenTeamsConfig()
    return _config


def init_config(**kwargs) -> OpenTeamsConfig:
    global _config
    _config = OpenTeamsConfig(**kwargs)
    _config.ensure_dirs()
    return _config


def load_and_init_config(
    config_file: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> OpenTeamsConfig:
    """Load config with priority: CLI args > config file > env vars > defaults.

    Parameters
    ----------
    config_file:
        Path to a JSON config file. If None, tries ``.open_teams/open_teams.json`` in cwd.
    cli_overrides:
        Dict of values from command-line flags (None values are ignored).
    """
    global _config

    # 1. Start with defaults (which already read env vars)
    if config_file:
        cfg_path = Path(config_file)
    else:
        cfg_path = Path.cwd() / ".open_teams" / CONFIG_FILE_NAME

    if cfg_path.exists():
        config = OpenTeamsConfig.load_from_file(cfg_path)
    else:
        config = OpenTeamsConfig()

    # 2. Overlay environment variables that aren't captured by defaults
    env_overrides: dict[str, Any] = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        env_overrides["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        env_overrides["api_key"] = os.environ["OPENAI_API_KEY"]
        if config.provider == "anthropic":
            env_overrides["provider"] = "openai"
    if os.environ.get("ANTHROPIC_BASE_URL"):
        env_overrides["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
    if os.environ.get("OPENAI_BASE_URL") and not os.environ.get("ANTHROPIC_BASE_URL"):
        env_overrides["base_url"] = os.environ["OPENAI_BASE_URL"]
    if env_overrides:
        config = config.merge(env_overrides)

    # 3. Overlay CLI args (highest priority)
    if cli_overrides:
        clean = {k: v for k, v in cli_overrides.items() if v is not None}
        if clean:
            config = config.merge(clean)

    # 4. Ensure workspace_dir is relative to project_root if default
    if str(config.workspace_dir) == str(Path.cwd() / ".open_teams"):
        config.workspace_dir = config.project_root / ".open_teams"
        config.__post_init__()

    config.ensure_dirs()
    _config = config
    return config
