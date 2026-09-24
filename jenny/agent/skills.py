"""Skills loader for agent capabilities."""

import json
import os
import re
from pathlib import Path

from jenny.utils.path import atomic_write

_ANDROID_SKILL_NOTE = (
    "\n\n[Platform compatibility note: Android is the only supported runtime. "
    "There is no shell, no pip, and no CLI tools; use python_exec as the only "
    "execution tool. Skill requirements may only reference python/python3 binaries.]\n"
)

try:
    import yaml
    _has_yaml = True
except ImportError:
    yaml = None
    _has_yaml = False

# Opening ---, YAML body (group 1), closing --- on its own line; supports CRLF.
_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


def _parse_frontmatter_simple(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if ":" in line:
            key, val = line.split(":", 1)
            val = val.strip().strip('"').strip("'")
            result[key.strip()] = val
    return result


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(self, workspace: Path, disabled_skills: set[str] | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.disabled_skills = disabled_skills or set()

    def _skill_entries_from_dir(
        self,
        base: Path,
        source: str,
        *,
        skip_names: set[str] | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, str]]:
        if not base.exists():
            return []
        entries: list[dict[str, str]] = []
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            name = skill_dir.name
            if skip_names is not None and name in skip_names:
                continue
            # Skip skills with disabled: true in frontmatter, unless the caller
            # explicitly wants them (e.g. a management UI listing).
            meta = self.get_skill_metadata(name)
            disabled = bool(meta and meta.get("disabled"))
            if disabled and not include_disabled:
                continue
            entries.append({"name": name, "path": str(skill_file), "source": source, "disabled": disabled})
        return entries

    def list_skills(self, filter_unavailable: bool = True, *, include_disabled: bool = False) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
            include_disabled: If True, include skills marked `disabled: true` in
                frontmatter (used by management UIs that need to show/toggle them,
                as opposed to the agent's own context building which should never
                see them).

        Returns:
            List of skill info dicts with 'name', 'path', 'source', 'disabled'.
        """
        skills = self._skill_entries_from_dir(self.workspace_skills, "workspace", include_disabled=include_disabled)

        if self.disabled_skills:
            skills = [s for s in skills if s["name"] not in self.disabled_skills]

        if filter_unavailable:
            return [skill for skill in skills if self._check_requirements(self._get_skill_meta(skill["name"]))]
        return skills

    def _adapt_skill_for_platform(self, body: str) -> str:
        """Append the Android platform compatibility note to a skill body."""
        return body + _ANDROID_SKILL_NOTE

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        path = self.workspace_skills / name / "SKILL.md"
        if path.exists():
            return self._adapt_skill_for_platform(path.read_text(encoding="utf-8"))
        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = [
            f"### Skill: {name}\n\n{self._strip_frontmatter(markdown)}"
            for name in skill_names
            if (markdown := self.load_skill(name))
        ]
        return "\n\n---\n\n".join(parts)

    def build_skills_summary(self, exclude: set[str] | None = None) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Args:
            exclude: Set of skill names to omit from the summary.

        Returns:
            Markdown-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        lines: list[str] = []
        for entry in all_skills:
            skill_name = entry["name"]
            if exclude and skill_name in exclude:
                continue
            meta = self._get_skill_meta(skill_name)
            available = self._check_requirements(meta)
            desc = self._get_skill_description(skill_name)
            if available:
                lines.append(f"- **{skill_name}** — {desc}  `{entry['path']}`")
            else:
                missing = self._get_missing_requirements(meta)
                suffix = f" (unavailable: {missing})" if missing else " (unavailable)"
                lines.append(f"- **{skill_name}** — {desc}{suffix}  `{entry['path']}`")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return ", ".join(
            [f"Missing: {command_name}" for command_name in required_bins if command_name not in {"python", "python3"}]
            + [f"ENV: {env_name}" for env_name in required_env_vars if not os.environ.get(env_name)]
        )

    def get_skill_availability(self, name: str) -> tuple[bool, str]:
        """Return whether a skill can run and why not when it cannot."""
        meta = self._get_skill_meta(name)
        available = self._check_requirements(meta)
        return available, "" if available else self._get_missing_requirements(meta)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end():].strip()
        return content

    # ── CRUD methods for workspace skills ──

    def is_workspace_skill(self, name: str) -> bool:
        """Check if a skill lives in the workspace directory (not builtin)."""
        return (self.workspace_skills / name / "SKILL.md").exists()

    def update_skill(
        self,
        name: str,
        *,
        description: str | None = None,
        content: str | None = None,
        disabled: bool | None = None,
    ) -> None:
        """Update a workspace skill's frontmatter and/or body.

        Raises:
            PermissionError: if the skill is builtin.
            FileNotFoundError: if the skill doesn't exist.
        """
        if not self.is_workspace_skill(name):
            raise PermissionError("cannot modify builtin skill")
        skill_path = self.workspace_skills / name / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"skill '{name}' not found")

        raw = skill_path.read_text(encoding="utf-8")
        fm_match = _STRIP_SKILL_FRONTMATTER.match(raw)
        body = raw[fm_match.end():] if fm_match else raw

        meta: dict = {}
        if fm_match:
            if _has_yaml:
                try:
                    meta = yaml.safe_load(fm_match.group(1)) or {}
                except yaml.YAMLError:
                    meta = {}
            else:
                meta = _parse_frontmatter_simple(fm_match.group(1)) or {}
        if not isinstance(meta, dict):
            meta = {}

        if description is not None:
            meta["description"] = description
        if disabled is not None:
            if disabled:
                meta["disabled"] = True
            else:
                meta.pop("disabled", None)

        if content is not None:
            body = f"\n{content}" if content and not content.startswith("\n") else content

        if _has_yaml:
            frontmatter = yaml.safe_dump(
                meta, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
        else:
            import json
            frontmatter = json.dumps(meta, indent=2)
        # Scrittura piena del file della skill: un processo ucciso a metà
        # lascerebbe un SKILL.md troncato — frontmatter a metà, quindi una skill
        # che non si carica più. Da qui l'helper unico invece di write_text.
        atomic_write(skill_path, f"---\n{frontmatter}---\n{body}")

    # ── Metadata parsing ──

    def _parse_jenny_metadata(self, raw: object) -> dict:
        """Extract jenny metadata from a frontmatter field.

        ``raw`` may be a dict (already parsed by yaml.safe_load) or a JSON str.
        """
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        else:
            return {}
        if not isinstance(data, dict):
            return {}
        payload = data.get("jenny", {})
        return payload if isinstance(payload, dict) else {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return all(
            cmd in {"python", "python3"}
            for cmd in required_bins
        ) and all(os.environ.get(var) for var in required_env_vars)

    def _get_skill_meta(self, name: str) -> dict:
        """Get jenny metadata for a skill (cached in frontmatter)."""
        raw_meta = self.get_skill_metadata(name) or {}
        return self._parse_jenny_metadata(raw_meta.get("metadata"))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        return [
            entry["name"]
            for entry in self.list_skills(filter_unavailable=True)
            if (meta := self.get_skill_metadata(entry["name"]) or {})
            and (
                self._parse_jenny_metadata(meta.get("metadata")).get("always")
                or meta.get("always")
            )
        ]

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None
        if _has_yaml:
            try:
                parsed = yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                return None
        else:
            parsed = _parse_frontmatter_simple(match.group(1))
        if not isinstance(parsed, dict):
            return None
        # yaml.safe_load returns native types (int, bool, list, etc.);
        # keep values as-is so downstream consumers get correct types.
        metadata: dict[str, object] = {}
        for key, value in parsed.items():
            metadata[str(key)] = value
        return metadata
