from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .gateway import RunTokens

_ENV_PASS = ("PATH", "TMPDIR", "TZ", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
_ENV_FORWARD = ("SLASHDHAN_TODAY", "SLASHDHAN_NOW")
_RUN_KEEP_S = 24 * 3600


@dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool
    timed_out: bool
    dhan_calls: dict[str, int]

    def text(self) -> str:
        head = f"exit_code={self.exit_code} duration={self.duration_ms}ms"
        if self.truncated:
            head += " TRUNCATED"
        parts = [head, "--- stdout ---\n" + (self.stdout.rstrip("\n") or "(empty)")]
        if self.stderr.strip():
            parts.append("--- stderr ---\n" + self.stderr.rstrip("\n"))
        return "\n".join(parts)


class Runner:
    """Runs one script in a fresh `python -I` whose only credential is a short-lived run token."""

    def __init__(self, settings: Settings, tokens: RunTokens, gateway_url: str) -> None:
        self._settings = settings
        self._tokens = tokens
        self._gateway_url = gateway_url
        settings.workspace_dir.mkdir(parents=True, exist_ok=True)
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def workspace(self) -> Path:
        return self._settings.workspace_dir

    def _env(self, run_token: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in _ENV_PASS}
        env.update(
            {
                "HOME": str(self.workspace),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "SLASHDHAN_GATEWAY_URL": self._gateway_url,
                "SLASHDHAN_RUN_TOKEN": run_token,
                "SLASHDHAN_CACHE_DIR": str(self._settings.cache_dir),
                "SLASHDHAN_CALL_BUDGET": str(self._settings.call_budget),
                "SLASHDHAN_SCRIP_MASTER_URL": f"{self._gateway_url}/scrip-master.csv",
            }
        )
        for key in _ENV_FORWARD:
            if os.environ.get(key):
                env[key] = os.environ[key]
        return env

    def run(self, code: str, timeout_s: int | None = None) -> RunResult:
        s = self._settings
        timeout = max(1, min(int(timeout_s or s.script_timeout_s), s.script_max_timeout_s))
        runs = self.workspace / "_runs"
        runs.mkdir(exist_ok=True)
        _prune(runs)
        script = runs / f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.py"
        script.write_text(code, encoding="utf-8")
        token = self._tokens.issue(ttl_s=timeout + 30)
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-u", str(script)],
                cwd=self.workspace,
                env=self._env(token),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                out_b, err_b = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                out_b, err_b = proc.communicate()
        finally:
            dhan_calls = self._tokens.revoke(token)
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, t1 = _cap(out_b.decode("utf-8", "replace"), s.max_output_bytes)
        stderr, t2 = _cap(err_b.decode("utf-8", "replace"), s.max_output_bytes // 2)
        if timed_out:
            note = (
                f"[timed out after {timeout}s and was killed. Fetch less, or pass a larger "
                f"timeout_seconds (max {s.script_max_timeout_s}).]"
            )
            stderr = f"{stderr}\n{note}" if stderr else note
        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=-9 if timed_out else proc.returncode,
            duration_ms=duration_ms,
            truncated=t1 or t2,
            timed_out=timed_out,
            dhan_calls=dhan_calls,
        )


def _cap(text: str, limit: int) -> tuple[str, bool]:
    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text, False
    head = data[:limit].decode("utf-8", "ignore")
    note = (
        f"[output truncated: {len(data) - limit} bytes omitted. Print less; save large data to a "
        "file in the workspace and print a summary.]"
    )
    return f"{head}\n{note}", True


def _prune(runs: Path) -> None:
    cutoff = time.time() - _RUN_KEEP_S
    for f in runs.glob("run_*.py"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
