"""Capteurs binaires : fenêtre ouverte, problème de communication, mise à jour."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EcoControlConfigEntry, EcoControlCoordinator
from .entity import EcoControlBoxEntity, EcoControlRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [UpdateAvailableBinarySensor(coordinator)]
    for room_id in coordinator.data.rooms:
        entities.append(WindowOpenBinarySensor(coordinator, room_id))
        entities.append(CommunicationProblemBinarySensor(coordinator, room_id))
    async_add_entities(entities)


class WindowOpenBinarySensor(EcoControlRoomEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_translation_key = "window_open"

    def __init__(self, coordinator: EcoControlCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "window_open")

    @property
    def is_on(self) -> bool | None:
        status = self.status
        return None if status is None else status.window_open


class CommunicationProblemBinarySensor(EcoControlRoomEntity, BinarySensorEntity):
    """Un thermostat de la pièce ne répond plus (th offline="1")."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "communication_problem"

    def __init__(self, coordinator: EcoControlCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "communication_problem")

    @property
    def is_on(self) -> bool | None:
        room = self.coordinator.data.rooms.get(self._room_id)
        return None if room is None else room.offline


class UpdateAvailableBinarySensor(EcoControlBoxEntity, BinarySensorEntity):
    """Information seulement : l'intégration ne déclenche JAMAIS la mise à jour."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "update_available"

    def __init__(self, coordinator: EcoControlCoordinator) -> None:
        super().__init__(coordinator, "update_available")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.general.update_available
