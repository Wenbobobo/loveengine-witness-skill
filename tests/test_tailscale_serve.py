from __future__ import annotations

import json
import subprocess
from typing import Callable

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.tailscale_serve import TailscaleServeManager


PUBLIC_URL = "https://witness.example-tailnet.ts.net"


def _foreground_serve_config(
    *,
    hostname: str = "witness.example-tailnet.ts.net",
    https_port: int = 443,
    path: str = "/",
    target: str = "http://127.0.0.1:8781",
    extra_handlers: dict | None = None,
) -> dict:
    handlers = {path: {"Proxy": target}}
    handlers.update(extra_handlers or {})
    return {
        "Foreground": {
            "test-session": {
                "TCP": {str(https_port): {"HTTPS": True}},
                "Web": {
                    f"{hostname}:{https_port}": {"Handlers": handlers}
                },
            }
        }
    }


class FakeProcess:
    def __init__(self, on_terminate: Callable[[], None]) -> None:
        self.returncode: int | None = None
        self.on_terminate = on_terminate

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.on_terminate()
        self.returncode = 0

    def kill(self) -> None:
        self.on_terminate()
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None
        return self.returncode


class FakeTailscale:
    def __init__(self, *, remove_on_terminate: bool = True) -> None:
        self.serve: dict = {}
        self.commands: list[tuple[str, ...]] = []
        self.started: list[tuple[str, ...]] = []
        self.remove_on_terminate = remove_on_terminate
        self.serve_status_calls = 0
        self.change_on_serve_status_call: int | None = None
        self.terminate_replacement: dict | None = None
        self.started_serve: dict | None = None
        self.backend_state = "Running"
        self.dns_name = "witness.example-tailnet.ts.net."
        self.started_backend_state: str | None = None
        self.started_dns_name: str | None = None

    def runner(
        self, arguments: tuple[str, ...], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(arguments)
        tail = arguments[1:]
        if tail == ("status", "--json"):
            value = {
                "BackendState": self.backend_state,
                "Self": {"DNSName": self.dns_name},
            }
        elif tail == ("serve", "status", "--json"):
            self.serve_status_calls += 1
            if self.change_on_serve_status_call == self.serve_status_calls:
                self.serve = {"Web": {"foreign": {"Proxy": "http://127.0.0.1:9999"}}}
            value = self.serve
        elif len(tail) == 3 and tail[0] == "serve" and tail[2] == "off":
            self.serve = {}
            value = {}
        else:
            raise AssertionError(f"unexpected command: {arguments}")
        return subprocess.CompletedProcess(
            list(arguments), 0, stdout=json.dumps(value), stderr=""
        )

    def starter(self, arguments: tuple[str, ...]) -> FakeProcess:
        self.started.append(arguments)
        target = arguments[-1]
        self.serve = self.started_serve or _foreground_serve_config(target=target)
        initial_backend_state = self.backend_state
        initial_dns_name = self.dns_name
        if self.started_backend_state is not None:
            self.backend_state = self.started_backend_state
        if self.started_dns_name is not None:
            self.dns_name = self.started_dns_name

        def terminate() -> None:
            self.backend_state = initial_backend_state
            self.dns_name = initial_dns_name
            if self.terminate_replacement is not None:
                self.serve = self.terminate_replacement
            elif self.remove_on_terminate:
                self.serve = {}

        return FakeProcess(terminate)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _manager(fake: FakeTailscale) -> TailscaleServeManager:
    return TailscaleServeManager(
        participant_port=8781,
        expected_public_base_url=PUBLIC_URL,
        duration_seconds=900,
        runner=fake.runner,
        starter=fake.starter,
        poll_interval_seconds=0.001,
    )


def test_tailscale_preflight_blocks_existing_serve_or_funnel_without_mutation() -> None:
    fake = FakeTailscale()
    fake.serve = {"AllowFunnel": {"witness.example-tailnet.ts.net:443": True}}

    result = _manager(fake).preflight()

    assert result["safe_to_run"] is False
    assert result["mutated_host"] is False
    assert "existing_serve_configuration" in result["reasons"]
    assert "existing_funnel_configuration" in result["reasons"]
    assert fake.started == []


def test_tailscale_foreground_mapping_restores_exact_initial_state() -> None:
    fake = FakeTailscale(remove_on_terminate=True)
    manager = _manager(fake)

    started = manager.start()
    stopped = manager.stop()

    assert started["foreground"] is True
    assert started["funnel_enabled"] is False
    assert stopped["restored"] is True
    assert stopped["used_exact_off"] is False
    assert fake.started == [
        ("tailscale", "serve", "--https=443", "http://127.0.0.1:8781")
    ]
    flattened = " ".join(" ".join(command) for command in fake.commands + fake.started)
    assert " reset" not in flattened
    assert " --bg" not in flattened
    assert " --yes" not in flattened
    assert "tailscale up" not in flattened


def test_tailscale_cleanup_uses_only_exact_owned_off_when_foreground_leaves_mapping() -> None:
    fake = FakeTailscale(remove_on_terminate=False)
    manager = _manager(fake)

    manager.start()
    result = manager.stop()

    assert result["restored"] is True
    assert result["used_exact_off"] is True
    assert ("tailscale", "serve", "--https=443", "off") in fake.commands
    assert all("reset" not in command for command in fake.commands)


def test_tailscale_start_rechecks_snapshot_and_rejects_race() -> None:
    fake = FakeTailscale()
    fake.change_on_serve_status_call = 3
    manager = _manager(fake)

    with pytest.raises(LoveEngineError) as caught:
        manager.start()

    assert caught.value.code == "tailscale_serve_state_changed"
    assert fake.started == []


def test_tailscale_cleanup_refuses_to_remove_unknown_replacement() -> None:
    fake = FakeTailscale(remove_on_terminate=False)
    fake.terminate_replacement = {
        "Web": {"foreign": {"Proxy": "http://127.0.0.1:9999"}}
    }
    manager = _manager(fake)
    manager.start()

    with pytest.raises(LoveEngineError) as caught:
        manager.stop()

    assert caught.value.code == "tailscale_state_not_restored"
    assert ("tailscale", "serve", "--https=443", "off") not in fake.commands


def test_tailscale_cleanup_refuses_same_target_with_foreign_handler() -> None:
    fake = FakeTailscale(remove_on_terminate=False)
    fake.terminate_replacement = _foreground_serve_config(
        extra_handlers={"/foreign": {"Text": "added by another operator"}}
    )
    manager = _manager(fake)
    manager.start()

    with pytest.raises(LoveEngineError) as caught:
        manager.stop()

    assert caught.value.code == "tailscale_state_not_restored"
    assert ("tailscale", "serve", "--https=443", "off") not in fake.commands


def test_tailscale_timeboxed_run_restores_at_deadline() -> None:
    fake = FakeTailscale(remove_on_terminate=True)
    clock = FakeClock()
    manager = TailscaleServeManager(
        participant_port=8781,
        expected_public_base_url=PUBLIC_URL,
        duration_seconds=60,
        runner=fake.runner,
        starter=fake.starter,
        poll_interval_seconds=10,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )

    result = manager.run_timeboxed()

    assert result["passed"] is True
    assert result["duration_seconds"] == 60
    assert result["stopped"]["restored"] is True
    assert clock.value == 60


@pytest.mark.parametrize(
    "serve_config",
    [
        _foreground_serve_config(hostname="other.example-tailnet.ts.net"),
        _foreground_serve_config(https_port=8443),
        _foreground_serve_config(path="/participant"),
        _foreground_serve_config(
            extra_handlers={
                "/foreign": {"Proxy": "http://127.0.0.1:8781"}
            }
        ),
        {
            "TCP": {"443": {"HTTPS": True}},
            "Web": {
                "witness.example-tailnet.ts.net:443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8781"}}
                }
            },
        },
    ],
)
def test_tailscale_readiness_rejects_same_target_without_exact_owned_exposure(
    serve_config: dict,
) -> None:
    fake = FakeTailscale()
    fake.started_serve = serve_config

    with pytest.raises(LoveEngineError) as caught:
        _manager(fake).start()

    assert caught.value.code == "tailscale_serve_exposure_mismatch"
    assert fake.serve == {}


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("started_backend_state", "Stopped"),
        ("started_dns_name", "other.example-tailnet.ts.net."),
    ],
)
def test_tailscale_readiness_rechecks_backend_and_dns_identity(
    attribute: str, value: str
) -> None:
    fake = FakeTailscale()
    setattr(fake, attribute, value)

    with pytest.raises(LoveEngineError) as caught:
        _manager(fake).start()

    assert caught.value.code == "tailscale_serve_identity_changed"
    assert fake.serve == {}


def test_tailscale_failed_readiness_removes_only_exact_mapping_it_started() -> None:
    fake = FakeTailscale(remove_on_terminate=False)
    fake.started_dns_name = "other.example-tailnet.ts.net."

    with pytest.raises(LoveEngineError) as caught:
        _manager(fake).start()

    assert caught.value.code == "tailscale_serve_identity_changed"
    assert fake.serve == {}
    assert ("tailscale", "serve", "--https=443", "off") in fake.commands


def test_tailscale_readiness_rejects_funnel_permission_inside_foreground() -> None:
    fake = FakeTailscale()
    fake.started_serve = _foreground_serve_config()
    nested = fake.started_serve["Foreground"]["test-session"]
    nested["AllowFunnel"] = {"witness.example-tailnet.ts.net:443": True}

    with pytest.raises(LoveEngineError) as caught:
        _manager(fake).start()

    assert caught.value.code == "tailscale_serve_exposure_mismatch"
    assert fake.serve == {}


def test_tailscale_timeboxed_run_restores_when_interrupted() -> None:
    fake = FakeTailscale(remove_on_terminate=True)
    clock = FakeClock()

    def interrupt(_: float) -> None:
        raise RuntimeError("operator interrupted the bounded run")

    manager = TailscaleServeManager(
        participant_port=8781,
        expected_public_base_url=PUBLIC_URL,
        duration_seconds=60,
        runner=fake.runner,
        starter=fake.starter,
        clock=clock.monotonic,
        sleeper=interrupt,
    )

    with pytest.raises(RuntimeError, match="operator interrupted"):
        manager.run_timeboxed()

    assert fake.serve == {}
    assert manager.process is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"participant_port": 0, "expected_public_base_url": PUBLIC_URL},
        {"participant_port": 8781, "expected_public_base_url": "https://example.com"},
        {
            "participant_port": 8781,
            "expected_public_base_url": PUBLIC_URL + ":8443",
        },
        {
            "participant_port": 8781,
            "expected_public_base_url": PUBLIC_URL,
            "https_port": 8443,
        },
        {
            "participant_port": 8781,
            "expected_public_base_url": PUBLIC_URL,
            "duration_seconds": 901,
        },
    ],
)
def test_tailscale_manager_rejects_unbounded_or_non_tailnet_target(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        TailscaleServeManager(**kwargs)
