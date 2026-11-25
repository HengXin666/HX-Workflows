"""Plan label changes: create, update, or rename obvious old equivalents. Never delete."""

from __future__ import annotations

from dataclasses import dataclass

from governance.config import LabelSpec


@dataclass(frozen=True)
class Label:
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class LabelOp:
    kind: str  # create / update / rename
    name: str  # the existing name for update and rename, the new name for create
    new_name: str
    color: str
    description: str

    def describe(self) -> str:
        if self.kind == "rename":
            return f"`{self.name}` → `{self.new_name}`"
        return f"`{self.new_name}`"


def plan_labels(existing: list[Label], desired: tuple[LabelSpec, ...]) -> list[LabelOp]:
    by_name = {label.name.lower(): label for label in existing}
    used: set[str] = set()
    ops: list[LabelOp] = []
    for spec in desired:
        current = by_name.get(spec.name.lower())
        if current is not None:
            used.add(current.name.lower())
            if (
                current.name != spec.name
                or current.color.lower() != spec.color.lower()
                or (current.description or "") != spec.description
            ):
                ops.append(LabelOp("update", current.name, spec.name, spec.color, spec.description))
            continue
        alias = next(
            (
                by_name[name.lower()]
                for name in spec.aliases
                if name.lower() in by_name and name.lower() not in used
            ),
            None,
        )
        if alias is not None:
            used.add(alias.name.lower())
            ops.append(LabelOp("rename", alias.name, spec.name, spec.color, spec.description))
            continue
        ops.append(LabelOp("create", spec.name, spec.name, spec.color, spec.description))
    return ops


def summarize(ops: list[LabelOp]) -> str:
    parts: list[str] = []
    for kind, word in (("create", "新建"), ("rename", "改名"), ("update", "更新")):
        chosen = [op for op in ops if op.kind == kind]
        if not chosen:
            continue
        if kind == "rename":
            parts.append(f"{word} " + ", ".join(op.describe() for op in chosen))
        else:
            parts.append(f"{word} {len(chosen)} 个")
    return "; ".join(parts)
