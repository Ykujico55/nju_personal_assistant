"""Git history for scoped, non-secret rule metadata in a dedicated managed repository."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

from personal_assistant.core.governance import validate_rule


class GitRuleHistory:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = asyncio.Lock()

    async def _git(self, *argv: str, check: bool = True) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.hooksPath=" + str(self.root / "no-hooks"),
            "-c",
            "user.name=Assistant Rules",
            "-c",
            "user.email=rules@localhost.invalid",
            "-c",
            "commit.gpgsign=false",
            *argv,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if check and process.returncode:
            raise ValueError("rule history operation failed")
        return output.decode("utf-8", errors="strict").strip()

    async def _ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            await self._git("init", "--quiet")
        if await self._git("remote"):
            raise ValueError("rule history must be a dedicated local repository")

    @staticmethod
    def _id(rule_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rule_id):
            raise ValueError("invalid rule identifier")
        return "rules/" + rule_id + ".json"

    async def record(self, rule: dict[str, Any]) -> str:
        path = self._id(rule["id"])
        value = rule["value"]
        validate_rule(value["scope"], value["preferences"])
        safe = {
            "id": rule["id"],
            "version": rule["version"],
            "value": {k: value[k] for k in ["scope", "preferences", "state"] if k in value},
        }
        async with self.lock:
            await self._ready()
            (self.root / "rules").mkdir(exist_ok=True)
            (self.root / path).write_text(
                json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            await self._git("add", "--", path)
            if await self._git("diff", "--cached", "--name-only"):
                await self._git(
                    "commit", "--quiet", "--no-gpg-sign", "-m", "Review rule metadata", "--", path
                )
            return await self._git("rev-parse", "HEAD")

    async def read(self, revision: str, rule_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise ValueError("history revision must be an exact commit hash")
        async with self.lock:
            await self._ready()
            result = json.loads(await self._git("show", revision + ":" + self._id(rule_id)))
            validate_rule(result["value"]["scope"], result["value"]["preferences"])
            return cast(dict[str, Any], result)

    async def versions(self, rule_id: str) -> list[str]:
        async with self.lock:
            await self._ready()
            lines = await self._git("log", "--format=%H", "--", self._id(rule_id), check=False)
            return [line for line in lines.splitlines() if re.fullmatch(r"[0-9a-f]{40,64}", line)]
