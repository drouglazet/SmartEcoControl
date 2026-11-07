"""Classes de base des entités."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RoomStatus
from .const import DOMAIN
from .coordinator import EcoControlCoordinator


class EcoControlBoxEntity(CoordinatorEntity[EcoControlCoordinator]):
    """Entité rattachée à la box elle-même."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EcoControlCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})


class EcoControlRoomEntity(CoordinatorEntity[EcoControlCoordinator]):
    """Entité rattachée à une pièce (un appareil par pièce)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EcoControlCoordinator, room_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._room_id = room_id
        self._attr_unique_id = f"{entry_id}_{room_id}_{key}"
        room = coordinator.data.rooms.get(room_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{room_id}")},
            name=room.name if room else f"Pièce {room_id}",
            model="Pièce",
            via_device=(DOMAIN, entry_id),
        )

    @property
    def status(self) -> RoomStatus | None:
        return self.coordinator.data.statuses.get(self._room_id)

    @property
    def available(self) -> bool:
        return super().available and self.status is not None
