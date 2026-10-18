"""Load the small hygiene config file."""

from __future__ import annotations

from pathlib import Path

from hygiene.models import Config


def _strip_comment(line: str) -> str:
    quote = ""
    kept: list[str] = []
    for char in line:
        if quote:
            kept.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            kept.append(char)
            continue
        if char == "#":
            break
        kept.append(char)
    return "".join(kept).rstrip()


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _scalar(value: str) -> str | bool:
    if value in {"true", "false"}:
        return value == "true"
    return _unquote(value)


def parse_config(text: str) -> Config:
    data: dict[str, object] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is None or not isinstance(data.get(current), list):
                raise ValueError(f"列表项没有对应的键: {line.strip()}")
            values = data[current]
            assert isinstance(values, list)
            values.append(_unquote(line.strip()[2:].strip()))
            continue
        if line.startswith(" ") or ":" not in line:
            raise ValueError(f"无法解析配置行: {line}")
        current = None
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if key in data:
            raise ValueError(f"配置重复键: {key}")
        if raw_value in {"", "[]"}:
            data[key] = []
            current = key if raw_value == "" else None
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
        include_private=flag("include_private", False),
        skill_roots=roots,
        preferred_sources=words("preferred_sources"),
        protected_branches=words("protected_branches"),
        protected_branch_globs=words("protected_branch_globs"),
        ignored_repositories=words("ignored_repositories"),
    )


def load_config(path: Path) -> Config:
    return parse_config(path.read_text(encoding="utf-8"))
