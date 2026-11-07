"""Capteurs : allure courante d'une pièce, date de remise en chauffe."""

from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ALLURE_NAMES
from .coordinator import EcoControlConfigEntry, EcoControlCoordinator
from .entity import EcoControlBoxEntity, EcoControlRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [VacationUntilSensor(coordinator)]
    entities.extend(AllureSensor(coordinator, room_id) for room_id in coordinator.data.rooms)
    async_add_entities(entities)


class AllureSensor(EcoControlRoomEntity, SensorEntity):
    """Allure réellement appliquée (Eco, Confort, Hors-gel...), planning compris."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ALLURE_NAMES.values())
    _attr_translation_key = "allure"

    def __init__(self, coordinator: EcoControlCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "allure")

    @property
    def native_value(self) -> str | None:
        status = self.status
        return None if status is None else ALLURE_NAMES.get(status.effective_allure)


class VacationUntilSensor(EcoControlBoxEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATE
    _attr_translation_key = "vacation_until"

    def __init__(self, coordinator: EcoControlCoordinator) -> None:
        super().__init__(coordinator, "vacation_until")

    @property
    def native_value(self) -> date | None:
        return self.coordinator.data.vacation.until
