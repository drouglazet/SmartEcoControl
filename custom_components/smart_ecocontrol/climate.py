"""Un thermostat (climate) par pièce.

Ce que la box permet à distance (d'après son interface web) :
* modifier la consigne, uniquement en mode Auto sur l'allure Confort ;
* forcer temporairement Eco / Confort / Hors-gel (dérogation) ;
* le mode lui-même (Auto, Arrêt...) ne se change que sur l'appareil (touche Smart),
  donc hvac_modes ne propose que le mode courant.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    ALLURE_TO_DEROG,
    MODE_AUTO,
    MODE_OFF,
    MODE_SHEDDING,
    TEMP_MAX,
    TEMP_MIN,
    quarter_to_time,
)
from .const import (
    CONF_DEROGATION_HOURS,
    DEFAULT_DEROGATION_HOURS,
    DOMAIN,
    PRESET_FROST,
)
from .coordinator import EcoControlConfigEntry, EcoControlCoordinator
from .entity import EcoControlRoomEntity

PRESET_TO_KIND = {PRESET_ECO: "eco", PRESET_COMFORT: "comfort", PRESET_FROST: "frost"}
KIND_TO_PRESET = {kind: preset for preset, kind in PRESET_TO_KIND.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EcoControlClimate(coordinator, room_id) for room_id in coordinator.data.rooms
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_derogation",
        {
            vol.Required("allure"): vol.In(list(PRESET_TO_KIND.values())),
            vol.Optional("duration"): vol.All(
                vol.Coerce(float), vol.Range(min=0.25, max=24)
            ),
        },
        "async_set_derogation",
    )
    platform.async_register_entity_service(
        "cancel_derogation", None, "async_cancel_derogation"
    )


class EcoControlClimate(EcoControlRoomEntity, ClimateEntity):
    _attr_name = None
    _attr_translation_key = "radiator"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.1
    _attr_preset_modes = [PRESET_NONE, PRESET_ECO, PRESET_COMFORT, PRESET_FROST]

    def __init__(self, coordinator: EcoControlCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "climate")

    # ------------------------------------------------------------ état

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.PRESET_MODE
        status = self.status
        # Comme les boutons +/- de la box : mode Auto, allure Confort, sans fil pilote
        if status is not None and status.can_set_setpoint:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def hvac_mode(self) -> HVACMode | None:
        status = self.status
        if status is None:
            return None
        mode = status.effective_mode
        if mode in (MODE_OFF, MODE_SHEDDING):
            return HVACMode.OFF
        if mode == MODE_AUTO:
            return HVACMode.AUTO
        return HVACMode.HEAT

    @property
    def hvac_modes(self) -> list[HVACMode]:
        mode = self.hvac_mode
        return [mode] if mode is not None else []

    @property
    def target_temperature(self) -> float | None:
        status = self.status
        # La box n'affiche pas de température en Eco, Hors-gel, Confort -1/-2...
        if status is None or not status.shows_setpoint:
            return None
        return status.setpoint

    @property
    def min_temp(self) -> float:
        status = self.status
        return status.setpoint_bounds[0] if status else TEMP_MIN

    @property
    def max_temp(self) -> float:
        status = self.status
        return status.setpoint_bounds[1] if status else TEMP_MAX

    @property
    def preset_mode(self) -> str | None:
        status = self.status
        if status is None:
            return None
        if status.derog_active:
            kind = ALLURE_TO_DEROG.get(status.derog_allure)
            return KIND_TO_PRESET.get(kind, PRESET_NONE)
        return PRESET_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self.status
        if status is None:
            return None
        planning = self.coordinator.data.plannings.get(status.prog_id)
        attributes: dict[str, Any] = {
            "box_mode": status.mode,
            "allure": status.effective_allure,
            "allure_name": status.allure_name,
            "setpoint_visible": status.shows_setpoint,
            "available_derogations": list(status.available_derogations),
            "planning": planning.name if planning else status.prog_id,
            "setpoint": status.setpoint,
            "preferred_temperature": status.preferred,
            "window_open": status.window_open,
            "absence_detection": status.presence_detection,
            "keyboard_lock": status.keyboard_lock,
            "fil_pilote_6_ordres": status.fp6,
        }
        if status.derog_active and status.derog_end is not None:
            attributes["derogation_until"] = quarter_to_time(status.derog_end)
        return attributes

    # --------------------------------------------------------- commandes

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.coordinator.async_command(
            self.coordinator.client.set_setpoint(self._room_id, float(temperature)),
            self._room_id,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == self.hvac_mode:
            return
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="hvac_mode_physical"
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_NONE:
            await self.async_cancel_derogation()
        else:
            await self.async_set_derogation(PRESET_TO_KIND[preset_mode])

    async def async_set_derogation(
        self, allure: str, duration: float | None = None
    ) -> None:
        if duration is None:
            duration = self.coordinator.config_entry.options.get(
                CONF_DEROGATION_HOURS, DEFAULT_DEROGATION_HOURS
            )
        await self.coordinator.async_command(
            self.coordinator.client.set_derogation(self._room_id, allure, duration),
            self._room_id,
        )

    async def async_cancel_derogation(self) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.clear_derogation(self._room_id), self._room_id
        )
