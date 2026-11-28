"""Git adapter. Credentials go through GIT_ASKPASS environment variables, never through argv or remote URLs."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
ASKPASS = """#!/bin/sh
case "$1" in
  Username*|username*) printf '%s\\n' "$HX_MIRROR_USER" ;;
  *) printf '%s\\n' "$HX_MIRROR_PASS" ;;
esac
"""


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, timeout: int = 1800) -> None:
        self._timeout = timeout
        handle, path = tempfile.mkstemp(prefix="hx-mirror-askpass-", suffix=".sh")
        with os.fdopen(handle, "w", encoding="utf-8") as script:
            script.write(ASKPASS)
        os.chmod(path, stat.S_IRWXU)
        self._askpass = path

    def close(self) -> None:
        Path(self._askpass).unlink(missing_ok=True)

    def _run(self, args: list[str], cwd: Path | None, credentials: tuple[str, str] | None) -> str:
        env = dict(os.environ)
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": self._askpass, "LC_ALL": "C"})
        if credentials:
            env["HX_MIRROR_USER"], env["HX_MIRROR_PASS"] = credentials
        command = ["git", "-c", "credential.helper=", "-c", "core.askPass=" + self._askpass, *args]
        try:
            done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} 超过 {self._timeout} 秒") from None
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip()
            if credentials and credentials[1]:
                detail = detail.replace(credentials[1], "***")
            raise GitError(f"git {args[0]} 失败: {detail[-800:]}")
        return done.stdout

    def fetch_mirror(self, directory: Path, url: str, credentials: tuple[str, str] | None) -> None:
        """Keep a bare copy with exactly the source's branches and tags (no PR refs)."""
        if not (directory / "HEAD").exists():
            directory.mkdir(parents=True, exist_ok=True)
            self._run(["init", "--bare", "--quiet"], directory, None)
        self._run(["fetch", "--prune", "--force", "--quiet", url, *REFSPECS], directory, credentials)

    def has_refs(self, directory: Path) -> bool:
        return bool(self._run(["for-each-ref", "--count=1", "refs/heads", "refs/tags"], directory, None).strip())

    def push_mirror(self, directory: Path, url: str, credentials: tuple[str, str] | None, prune: bool) -> None:
        args = ["push", "--force", "--quiet"]
        if prune:
            args.append("--prune")
        self._run([*args, url, *REFSPECS], directory, credentials)
