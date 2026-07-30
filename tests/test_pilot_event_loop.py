from __future__ import annotations

import asyncio
import os

import pytest

from loveengine_witness.pilot_demo import _run_pilot_with_platform_loop


@pytest.mark.skipif(os.name != "nt", reason="Windows-only event-loop behavior")
def test_pilot_demo_uses_selector_loop_for_windows_restart_lifecycle() -> None:
    original_policy = asyncio.get_event_loop_policy()

    async def is_selector_loop() -> bool:
        return isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)

    assert _run_pilot_with_platform_loop(is_selector_loop()) is True
    assert asyncio.get_event_loop_policy() is original_policy
