"""Choix du planning de chauffe d'une pièce."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EcoControlConfigEntry, EcoControlCoordinator
from .entity import EcoControlRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        PlanningSelect(coordinator, room_id) for room_id in coordinator.data.rooms
    )


class PlanningSelect(EcoControlRoomEntity, SelectEntity):
    _attr_translation_key = "planning"

    def __init__(self, coordinator: EcoControlCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "planning")

    def _names(self) -> dict[str, str]:
        """id -> libellé (suffixé de l'id si deux plannings portent le même nom)."""
        plannings = self.coordinator.data.plannings
        counts: dict[str, int] = {}
        for planning in plannings.values():
            counts[planning.name] = counts.get(planning.name, 0) + 1
        return {
            planning.id: (
                planning.name
                if counts[planning.name] == 1
                else f"{planning.name} ({planning.id})"
            )
            for planning in plannings.values()
        }

    @property
    def options(self) -> list[str]:
        return list(self._names().values())

    @property
    def current_option(self) -> str | None:
        status = self.status
        return None if status is None else self._names().get(status.prog_id)

    async def async_select_option(self, option: str) -> None:
        for planning_id, label in self._names().items():
            if label == option:
                await self.coordinator.async_command(
                    self.coordinator.client.set_planning(self._room_id, planning_id),
                    self._room_id,
                )
                return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="planning_unknown",
            translation_placeholders={"planning": option},
        )
