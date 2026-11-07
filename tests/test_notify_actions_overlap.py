# WashData - Home Assistant integration for appliance cycle monitoring via smart plugs.
# Copyright (C) 2026 Lukas Bandura
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""Notification actions dispatched back to back must all run, in order.

The manager fires notifications in bursts: the finished alert and then the
`clear_notification` that ends the Live Activity, the start alert and the first
live card. Each goes through `_run_notification_actions`, which schedules the
same `Script` fire-and-forget. Built with Home Assistant's default mode,
"single", every run that arrives while the previous one is still awaiting its
notify call is dropped with "<title> notification: Already running", so the
clear never reached the phone and the Live Activity stayed on the lock screen
with its chronometer counting into negative numbers.

Uses the real `hass` and a real `Script` (not the mock most modules use) with a
notify service that blocks until released, which is exactly the overlap a slow
push delivery creates.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.ha_washdata.manager import WashDataManager

ACTIONS = [
    {
        "action": "notify.slow_phone",
        "data": {"message": "{{ message }}"},
    }
]


@pytest.fixture
def mock_entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "entry_overlap"
    entry.title = "Test Washer"
    entry.options = {"power_sensor": "sensor.test_power", "notify_actions": ACTIONS}
    entry.data = {}
    return entry


@pytest.fixture
def manager(hass: HomeAssistant, mock_entry: Any) -> WashDataManager:
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ):
        mgr = WashDataManager(hass, mock_entry)
        mgr.profile_store.get_suggestions = MagicMock(return_value={})
        return mgr


async def test_back_to_back_notifications_are_all_delivered_in_order(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    delivered: list[str] = []

    async def _slow_notify(call: ServiceCall) -> None:
        started.set()
        await release.wait()
        delivered.append(call.data["message"])

    hass.services.async_register("notify", "slow_phone", _slow_notify)

    # The finished alert, then the live activity's clear, as the cycle end sends them.
    # (Not async_block_till_done here: it would wait on the blocked first run.)
    assert manager._run_notification_actions({"message": "finished"}) is True
    await asyncio.wait_for(started.wait(), timeout=5)
    assert manager._run_notification_actions({"message": "clear_notification"}) is True

    release.set()
    await hass.async_block_till_done()

    assert delivered == ["finished", "clear_notification"], (
        "a notification dispatched while the previous action was still running "
        "was dropped, so the Live Activity's clear never reaches the phone"
    )
