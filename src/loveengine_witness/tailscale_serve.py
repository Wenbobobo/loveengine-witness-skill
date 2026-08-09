"""Fail-closed ownership wrapper for one foreground Tailscale Serve mapping."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence
from urllib.parse import urlsplit

from .errors import LoveEngineError


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


RunCommand = Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]
StartCommand = Callable[[tuple[str, ...]], ProcessHandle]


def _default_run(
    arguments: tuple[str, ...], timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise LoveEngineError("tailscale_unavailable", type(exc).__name__, 4) from exc


def _default_start(arguments: tuple[str, ...]) -> ProcessHandle:
    try:
        return subprocess.Popen(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise LoveEngineError("tailscale_serve_start_failed", str(exc), 4) from exc


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _configuration_empty(value: object) -> bool:
    if isinstance(value, dict):
        return all(_configuration_empty(item) for item in value.values())
    if isinstance(value, list):
        return all(_configuration_empty(item) for item in value)
    return value in {None, False, 0, ""}


def _funnel_projection(value: object) -> dict[str, Any]:
    """Extract Funnel permissions from a raw Tailscale ServeConfig."""

    if not isinstance(value, dict):
        return {}
    projection: dict[str, Any] = {}
    allow_funnel = value.get("AllowFunnel")
    if not _configuration_empty(allow_funnel):
        projection["AllowFunnel"] = allow_funnel
    foreground = value.get("Foreground")
    if isinstance(foreground, dict):
        projected_foreground = {
            session_id: projected
            for session_id, config in foreground.items()
            if (projected := _funnel_projection(config))
        }
        if projected_foreground:
            projection["Foreground"] = projected_foreground
    return projection


def _owned_serve_mapping(
    value: object, *, hostname: str, https_port: int, target: str
) -> bool:
    """Match the one Serve exposure this manager is allowed to own."""

    if not isinstance(value, dict) or set(value) != {"Foreground"}:
        return False
    foreground = value.get("Foreground")
    if not isinstance(foreground, dict) or len(foreground) != 1:
        return False
    session_id, config = next(iter(foreground.items()))
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(config, dict)
        or set(config) != {"TCP", "Web"}
    ):
        return False
    if config.get("TCP") != {str(https_port): {"HTTPS": True}}:
        return False
    web = config.get("Web")
    if not isinstance(web, dict) or len(web) != 1:
        return False
    endpoint, endpoint_config = next(iter(web.items()))
    if not isinstance(endpoint, str) or not isinstance(endpoint_config, dict):
        return False
    parsed_endpoint = urlsplit(
        endpoint if "://" in endpoint else f"https://{endpoint}"
    )
    try:
        endpoint_port = parsed_endpoint.port
    except ValueError:
        return False
    if (
        parsed_endpoint.scheme != "https"
        or parsed_endpoint.hostname != hostname
        or endpoint_port != https_port
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
        or parsed_endpoint.path
        or parsed_endpoint.query
        or parsed_endpoint.fragment
    ):
        return False
    return endpoint_config == {"Handlers": {"/": {"Proxy": target}}}


@dataclass(frozen=True)
class TailscaleServeSnapshot:
    backend_state: str
    dns_name: str
    serve_hash: str
    funnel_hash: str
    serve_empty: bool
    funnel_empty: bool
    serve_config: dict[str, Any] = field(repr=False)
    funnel_config: dict[str, Any] = field(repr=False)

    def report(self) -> dict[str, Any]:
        return {
            "backend_state": self.backend_state,
            "dns_name": self.dns_name,
            "serve_hash": self.serve_hash,
            "funnel_hash": self.funnel_hash,
            "serve_empty": self.serve_empty,
            "funnel_empty": self.funnel_empty,
        }


class TailscaleServeManager:
    """Own exactly one temporary Serve handler without touching other config."""

    def __init__(
        self,
        *,
        participant_port: int,
        expected_public_base_url: str,
        duration_seconds: int = 900,
        https_port: int = 443,
        executable: str = "tailscale",
        runner: RunCommand = _default_run,
        starter: StartCommand = _default_start,
        command_timeout_seconds: float = 10.0,
        readiness_timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= participant_port <= 65535:
            raise ValueError("participant_port must be between 1 and 65535")
        if https_port != 443:
            raise ValueError("https_port must be 443")
        if not 60 <= duration_seconds <= 900:
            raise ValueError("duration_seconds must be between 60 and 900")
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        if readiness_timeout_seconds <= 0:
            raise ValueError("readiness_timeout_seconds must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        parsed = urlsplit(expected_public_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".ts.net")
            or parsed.hostname.count(".") < 3
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("expected_public_base_url must be an exact ts.net HTTPS origin")
        self.participant_port = participant_port
        self.expected_public_base_url = expected_public_base_url
        self.duration_seconds = duration_seconds
        self.https_port = https_port
        self.executable = executable
        self.runner = runner
        self.starter = starter
        self.command_timeout_seconds = command_timeout_seconds
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.target = f"http://127.0.0.1:{participant_port}"
        self.expected_dns_name = parsed.hostname
        self.initial: TailscaleServeSnapshot | None = None
        self.active: TailscaleServeSnapshot | None = None
        self.process: ProcessHandle | None = None
        self.used_exact_off = False

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command = (self.executable, *arguments)
        result = self.runner(command, self.command_timeout_seconds)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise LoveEngineError(
                "tailscale_command_failed",
                f"{' '.join(arguments)}: exit {result.returncode}: {detail}",
                4,
            )
        return result

    def _json(self, arguments: Sequence[str]) -> dict[str, Any]:
        result = self._run(arguments)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LoveEngineError(
                "tailscale_status_invalid", "command did not return JSON", 4
            ) from exc
        if not isinstance(value, dict):
            raise LoveEngineError(
                "tailscale_status_invalid", "status must be a JSON object", 4
            )
        return value

    def snapshot(self) -> TailscaleServeSnapshot:
        status = self._json(("status", "--json"))
        backend_state = status.get("BackendState")
        self_value = status.get("Self")
        if not isinstance(backend_state, str) or not isinstance(self_value, dict):
            raise LoveEngineError(
                "tailscale_status_invalid", "missing BackendState or Self", 4
            )
        dns_name = self_value.get("DNSName")
        if not isinstance(dns_name, str) or not dns_name:
            raise LoveEngineError(
                "tailscale_status_invalid", "missing Tailscale DNS name", 4
            )
        dns_name = dns_name.rstrip(".").lower()
        serve = self._json(("serve", "status", "--json"))
        funnel = _funnel_projection(serve)
        return TailscaleServeSnapshot(
            backend_state=backend_state,
            dns_name=dns_name,
            serve_hash=_canonical_hash(serve),
            funnel_hash=_canonical_hash(funnel),
            serve_empty=_configuration_empty(serve),
            funnel_empty=_configuration_empty(funnel),
            serve_config=serve,
            funnel_config=funnel,
        )

    def preflight(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        reasons = []
        if snapshot.backend_state != "Running":
            reasons.append("tailscale_not_running")
        if snapshot.dns_name != self.expected_dns_name:
            reasons.append("tailscale_hostname_mismatch")
        if not snapshot.serve_empty:
            reasons.append("existing_serve_configuration")
        if not snapshot.funnel_empty:
            reasons.append("existing_funnel_configuration")
        return {
            "schema_version": "loveengine.tailscale-serve-preflight/1",
            "safe_to_run": not reasons,
            "reasons": reasons,
            "mutated_host": False,
            "snapshot": snapshot.report(),
            "participant_target": self.target,
            "public_base_url": self.expected_public_base_url,
            "duration_seconds": self.duration_seconds,
        }

    def _same_initial_state(self, snapshot: TailscaleServeSnapshot) -> bool:
        assert self.initial is not None
        return (
            snapshot.backend_state == self.initial.backend_state
            and snapshot.dns_name == self.initial.dns_name
            and snapshot.serve_hash == self.initial.serve_hash
            and snapshot.funnel_hash == self.initial.funnel_hash
        )

    def _owns_mapping(self, snapshot: TailscaleServeSnapshot) -> bool:
        return (
            snapshot.backend_state == "Running"
            and snapshot.dns_name == self.expected_dns_name
            and snapshot.funnel_empty
            and _owned_serve_mapping(
                snapshot.serve_config,
                hostname=self.expected_dns_name,
                https_port=self.https_port,
                target=self.target,
            )
        )

    def _await_configured(self) -> TailscaleServeSnapshot:
        deadline = self.clock() + self.readiness_timeout_seconds
        while True:
            process = self.process
            if process is None:
                raise LoveEngineError("tailscale_serve_start_failed", "no process", 4)
            if process.poll() is not None:
                raise LoveEngineError(
                    "tailscale_serve_start_failed",
                    "foreground Serve process exited before readiness",
                    4,
                )
            snapshot = self.snapshot()
            if snapshot.backend_state != "Running" or (
                snapshot.dns_name != self.expected_dns_name
            ):
                raise LoveEngineError(
                    "tailscale_serve_identity_changed",
                    "BackendState or DNS identity changed while applying Serve",
                    4,
                )
            if not snapshot.funnel_empty:
                raise LoveEngineError(
                    "tailscale_serve_exposure_mismatch",
                    "Funnel became active while applying Serve",
                    4,
                )
            if self._owns_mapping(snapshot):
                return snapshot
            if not snapshot.serve_empty:
                raise LoveEngineError(
                    "tailscale_serve_exposure_mismatch",
                    "Serve did not expose the owned hostname, port, root handler, and target",
                    4,
                )
            if self.clock() >= deadline:
                raise LoveEngineError(
                    "tailscale_serve_readiness_timeout",
                    "owned Serve mapping was not observed",
                    4,
                )
            self.sleeper(self.poll_interval_seconds)

    def start(self) -> dict[str, Any]:
        if self.process is not None:
            raise RuntimeError("Tailscale Serve is already started")
        self.active = None
        self.used_exact_off = False
        initial = self.snapshot()
        self.initial = initial
        preflight = self.preflight()
        if not preflight["safe_to_run"]:
            raise LoveEngineError(
                "tailscale_serve_preflight_blocked",
                ",".join(preflight["reasons"]),
                4,
            )
        before_start = self.snapshot()
        if not self._same_initial_state(before_start):
            raise LoveEngineError(
                "tailscale_serve_state_changed",
                "Tailscale identity, Serve, or Funnel state changed after preflight",
                4,
            )
        command = (
            self.executable,
            "serve",
            f"--https={self.https_port}",
            self.target,
        )
        self.process = self.starter(command)
        try:
            active = self._await_configured()
        except Exception:
            self.stop()
            raise
        self.active = active
        return {
            "started": True,
            "public_base_url": self.expected_public_base_url,
            "participant_target": self.target,
            "duration_seconds": self.duration_seconds,
            "initial_snapshot": initial.report(),
            "active_snapshot": active.report(),
            "foreground": True,
            "funnel_enabled": False,
        }

    def _stop_process(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def stop(self) -> dict[str, Any]:
        started_by_manager = self.process is not None
        self._stop_process()
        if self.initial is None:
            return {"stopped": True, "restored": True, "used_exact_off": False}
        current = self.snapshot()
        if not self._same_initial_state(current):
            active = self.active
            if (
                (
                    active is not None
                    and current.serve_hash == active.serve_hash
                    and current.funnel_hash == active.funnel_hash
                )
                or (
                    active is None
                    and started_by_manager
                    and self.initial.serve_empty
                    and self.initial.funnel_empty
                )
            ) and self._owns_mapping(current):
                self._run(("serve", f"--https={self.https_port}", "off"))
                self.used_exact_off = True
                current = self.snapshot()
        restored = self._same_initial_state(current)
        result = {
            "stopped": True,
            "restored": restored,
            "used_exact_off": self.used_exact_off,
            "initial_snapshot": self.initial.report(),
            "final_snapshot": current.report(),
        }
        if not restored:
            raise LoveEngineError(
                "tailscale_state_not_restored",
                "final identity/Serve/Funnel state does not match the initial snapshot",
                4,
            )
        return result

    def run_timeboxed(self) -> dict[str, Any]:
        """Expose the owned mapping until the configured deadline, then restore it."""

        started = self.start()
        started_at = self.clock()
        deadline = started_at + self.duration_seconds
        stopped: dict[str, Any] | None = None
        try:
            while self.clock() < deadline:
                process = self.process
                if process is None or process.poll() is not None:
                    raise LoveEngineError(
                        "tailscale_serve_process_exited",
                        "foreground Serve process exited during the bounded run",
                        4,
                    )
                remaining = deadline - self.clock()
                self.sleeper(min(self.poll_interval_seconds, remaining))
        finally:
            stopped = self.stop()
        return {
            "schema_version": "loveengine.tailscale-serve-run/1",
            "passed": True,
            "duration_seconds": self.duration_seconds,
            "started": started,
            "stopped": stopped,
        }

    def __enter__(self) -> TailscaleServeManager:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()
