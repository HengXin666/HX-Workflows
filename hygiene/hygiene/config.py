"""Load the small hygiene config file."""

from __future__ import annotations

from pathlib import Path

from hygiene.models import Config


def _scalar(value: str) -> str | bool:
    if value in {"true", "false"}:
        return value == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_config(text: str) -> Config:
    data: dict[str, object] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is None or not isinstance(data.get(current), list):
                raise ValueError(f"列表项没有对应的键: {line.strip()}")
            values = data[current]
            assert isinstance(values, list)
            values.append(str(_scalar(line.strip()[2:].strip())))
            continue
        if line.startswith(" ") or ":" not in line:
            raise ValueError(f"无法解析配置行: {line}")
        current = None
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value == "":
            data[key] = []
            current = key
        elif raw_value == "[]":
            data[key] = []
        else:
            data[key] = _scalar(raw_value)

    def text_value(name: str) -> str:
        value = data.get(name)
        if not isinstance(value, str) or value == "":
            raise ValueError(f"配置缺少 {name}")
        return value

    def flag(name: str, default: bool) -> bool:
        value = data.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"配置 {name} 必须是 true 或 false")
        return value

    def words(name: str) -> tuple[str, ...]:
        value = data.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"配置 {name} 必须是字符串列表")
        return tuple(value)

    roots = words("skill_roots")
    if not roots:
        raise ValueError("配置 skill_roots 不能为空")
    return Config(
        owner=text_value("owner"),
        report_repository=text_value("report_repository"),
        actor=text_value("actor"),
        include_forks=flag("include_forks", False),
        include_archived=flag("include_archived", False),
        skill_roots=roots,
        preferred_sources=words("preferred_sources"),
        protected_branches=words("protected_branches"),
        protected_branch_globs=words("protected_branch_globs"),
        ignored_repositories=words("ignored_repositories"),
    )


def load_config(path: Path) -> Config:
    return parse_config(path.read_text(encoding="utf-8"))
