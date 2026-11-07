"""Mode vacances : tous les radiateurs en hors-gel, jusqu'à une date ou en permanence."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EcoControlConfigEntry, EcoControlCoordinator
from .entity import EcoControlBoxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([VacationSwitch(entry.runtime_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_vacation", {vol.Required("until"): cv.date}, "async_set_vacation"
    )


class VacationSwitch(EcoControlBoxEntity, SwitchEntity):
    """Activé : vacances jusqu'à une date OU hors-gel permanent.

    * turn_on  -> hors-gel permanent ;
    * turn_off -> annule vacances et hors-gel permanent ;
    * service set_vacation -> hors-gel jusqu'à une date (remise en chauffe ensuite).
    """

    _attr_translation_key = "vacation"

    def __init__(self, coordinator: EcoControlCoordinator) -> None:
        super().__init__(coordinator, "vacation")

    @property
    def is_on(self) -> bool:
        vacation = self.coordinator.data.vacation
        return vacation.permanent_frost or vacation.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        vacation = self.coordinator.data.vacation
        return {
            "permanent_frost": vacation.permanent_frost,
            "until": vacation.until.isoformat() if vacation.until else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_vacation(None, permanent_frost=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.set_vacation(None))

    async def async_set_vacation(self, until: date) -> None:
        await self.coordinator.async_command(self.coordinator.client.set_vacation(until))
