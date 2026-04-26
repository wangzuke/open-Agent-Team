"""SystemPromptBuilder: assembles full system prompts for leaders and teammates."""

import os
import platform
from datetime import datetime, timezone
from pathlib import Path

from config import OpenTeamsConfig
from . import templates, roles


class SystemPromptBuilder:
    """Builds complete system prompts for leader and teammate agents."""

    def __init__(self, config: OpenTeamsConfig):
        self.config = config

    def _detect_shell_context(self) -> dict[str, str]:
        system_name = platform.system()

        comspec = os.environ.get("COMSPEC", "")
        shell_env = os.environ.get("SHELL", "")
        ps_module_path = os.environ.get("PSModulePath", "")

        shell_path = ""
        shell_family = ""

        if system_name == "Windows":
            if ps_module_path:
                shell_family = "powershell"
                shell_path = (
                    os.environ.get("POWERSHELL_EXE")
                    or os.environ.get("PSHOME")
                    or "powershell"
                )
            elif comspec:
                shell_path = comspec
                shell_family = "cmd"
            else:
                shell_family = "powershell"
        else:
            if shell_env:
                shell_path = shell_env
                shell_name = Path(shell_env).name.lower()
                if "zsh" in shell_name:
                    shell_family = "zsh"
                elif "bash" in shell_name:
                    shell_family = "bash"
                elif "fish" in shell_name:
                    shell_family = "fish"
                else:
                    shell_family = shell_name or "sh"
            else:
                shell_family = "sh"

        shell_path = shell_path or shell_family or "unknown"

        if system_name == "Windows" and shell_family == "powershell":
            shell_guidance = (
                "Use Windows PowerShell-compatible commands. Avoid Unix shell idioms such as "
                "`mkdir -p`, `ls -la`, `touch`, `cat <<EOF`, and `&&` chains. Prefer "
                "`Get-ChildItem`, `Get-Content`, `New-Item -ItemType Directory -Force`, "
                "`Test-Path`, and other PowerShell-friendly syntax."
            )
            shell_examples = (
                "List files: `Get-ChildItem -Force`; create directory: "
                "`New-Item -ItemType Directory -Force -Path backend/src`; "
                "check path exists: `Test-Path backend/src`; read file: `Get-Content path`."
            )
        elif system_name == "Windows":
            shell_guidance = (
                "Use Windows command syntax. Avoid Unix-only flags and shell constructs unless "
                "you have verified they work in this shell."
            )
            shell_examples = (
                "List files: `dir`; create directory: `mkdir backend\\src`; "
                "check path exists: `if exist backend\\src ...`."
            )
        else:
            shell_guidance = (
                "Use POSIX shell-compatible commands for filesystem and process operations."
            )
            shell_examples = (
                "List files: `ls -la`; create directory: `mkdir -p backend/src`; "
                "check path exists: `test -d backend/src`."
            )

        return {
            "platform": system_name,
            "shell_family": shell_family,
            "shell_path": shell_path,
            "shell_guidance": shell_guidance,
            "shell_examples": shell_examples,
        }

    def build_leader_prompt(
        self,
        team_name: str,
        tool_names: list[str],
        working_dir: str,
    ) -> str:
        """Return a fully assembled system prompt for the team leader."""
        shell_ctx = self._detect_shell_context()
        env = templates.ENVIRONMENT_TEMPLATE.format(
            working_dir=working_dir,
            platform=shell_ctx["platform"],
            shell_family=shell_ctx["shell_family"],
            shell_path=shell_ctx["shell_path"],
            shell_guidance=shell_ctx["shell_guidance"],
            shell_examples=shell_ctx["shell_examples"],
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            team_name=team_name,
            agent_name="team-lead",
            agent_type="leader",
            tool_names=", ".join(tool_names),
        )
        return "\n".join([
            templates.BASE_SYSTEM_PROMPT,
            roles.LEADER_PROMPT,
            env,
            templates.TOOL_USAGE_INSTRUCTIONS,
            templates.COLLABORATION_INSTRUCTIONS,
        ])

    def build_teammate_prompt(
        self,
        agent_name: str,
        agent_type: str,
        team_name: str,
        tool_names: list[str],
        working_dir: str,
    ) -> str:
        """Return a fully assembled system prompt for a non-leader teammate."""
        role_instr = roles.ROLE_MAP.get(agent_type, roles.CODER_ROLE)
        shell_ctx = self._detect_shell_context()
        teammate = roles.TEAMMATE_PROMPT_TEMPLATE.format(
            agent_name=agent_name,
            agent_type=agent_type,
            team_name=team_name,
            role_instructions=role_instr,
        )
        env = templates.ENVIRONMENT_TEMPLATE.format(
            working_dir=working_dir,
            platform=shell_ctx["platform"],
            shell_family=shell_ctx["shell_family"],
            shell_path=shell_ctx["shell_path"],
            shell_guidance=shell_ctx["shell_guidance"],
            shell_examples=shell_ctx["shell_examples"],
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            team_name=team_name,
            agent_name=agent_name,
            agent_type=agent_type,
            tool_names=", ".join(tool_names),
        )
        return "\n".join([
            templates.BASE_SYSTEM_PROMPT,
            teammate,
            env,
            templates.TOOL_USAGE_INSTRUCTIONS,
            templates.COLLABORATION_INSTRUCTIONS,
        ])
