"""Coordinateur de données de la box Smart ECOcontrol."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BoxSnapshot,
    EcoControlClient,
    EcoControlCommandError,
    EcoControlError,
    RoomStatus,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

EcoControlConfigEntry = ConfigEntry["EcoControlCoordinator"]


class EcoControlCoordinator(DataUpdateCoordinator[BoxSnapshot]):
    """Interroge la box (pièces, plannings, vacances) à intervalle régulier."""

    config_entry: EcoControlConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EcoControlConfigEntry,
        client: EcoControlClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client

    async def _async_update_data(self) -> BoxSnapshot:
        previous = self.data.statuses if self.data else None
        try:
            return await self.client.fetch_all(previous)
        except EcoControlError as err:
            raise UpdateFailed(f"Box Smart ECOcontrol : {err}") from err

    async def async_command(
        self,
        command: Awaitable[Any],
        room_id: str | None = None,
        *,
        refresh: bool = True,
    ) -> Any:
        """Exécute une commande, traduit les erreurs, puis met à jour les données."""
        try:
            result = await command
        except EcoControlCommandError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=err.key,
                translation_placeholders=err.placeholders,
            ) from err
        except EcoControlError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err

        if not refresh:
            return result
        if isinstance(result, RoomStatus) and room_id is not None and self.data:
            self.async_set_updated_data(
                replace(self.data, statuses={**self.data.statuses, room_id: result})
            )
        else:
            await self.async_request_refresh()
        return result

    async def async_refresh_plannings(self) -> None:
        """Relit seulement la liste des plannings (après création, renommage, suppression)."""
        plannings = await self.async_command(
            self.client.list_plannings(), refresh=False
        )
        if self.data:
            self.async_set_updated_data(replace(self.data, plannings=plannings))
