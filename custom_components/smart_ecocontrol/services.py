"""Services de l'intégration qui concernent la box entière : plannings de chauffe.

Ils servent à la carte « éditeur de planning » mais s'utilisent aussi seuls (scripts,
automatisations). Les heures s'écrivent « HH:MM » par quarts d'heure ; une plage de
confort est une période où la pièce chauffe à la température de confort, le reste de
la journée est en Éco.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import (
    DAYS_PER_WEEK,
    NO_PLANNING_ID,
    QUARTERS_PER_DAY,
    Planning,
    Schedule,
    quarter_to_time,
)
from .const import DOMAIN
from .coordinator import EcoControlCoordinator

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # lundi = 0, comme la box
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")

ATTR_ENTRY = "config_entry_id"
RANGE_SCHEMA = vol.Schema(
    {vol.Required("start"): cv.string, vol.Required("end"): cv.string}
)
DAYS_SCHEMA = vol.All(cv.ensure_list, [vol.In(DAY_KEYS)])

SCHEMAS: dict[str, vol.Schema] = {
    "get_planning": vol.Schema(
        {vol.Optional(ATTR_ENTRY): cv.string, vol.Optional("planning"): cv.string}
    ),
    "set_planning_day": vol.Schema(
        {
            vol.Optional(ATTR_ENTRY): cv.string,
            vol.Required("planning"): cv.string,
            vol.Required("days"): DAYS_SCHEMA,
            vol.Required("ranges"): vol.All(cv.ensure_list, [RANGE_SCHEMA]),
        }
    ),
    "copy_planning_day": vol.Schema(
        {
            vol.Optional(ATTR_ENTRY): cv.string,
            vol.Required("planning"): cv.string,
            vol.Required("source_day"): vol.In(DAY_KEYS),
            vol.Required("target_days"): DAYS_SCHEMA,
        }
    ),
    "create_planning": vol.Schema(
        {vol.Optional(ATTR_ENTRY): cv.string, vol.Required("name"): cv.string}
    ),
    "rename_planning": vol.Schema(
        {
            vol.Optional(ATTR_ENTRY): cv.string,
            vol.Required("planning"): cv.string,
            vol.Required("name"): cv.string,
        }
    ),
    "delete_planning": vol.Schema(
        {vol.Optional(ATTR_ENTRY): cv.string, vol.Required("planning"): cv.string}
    ),
}


# ------------------------------------------------------------------ outils


def parse_time(value: str) -> int:
    """« HH:MM » (par quarts d'heure, 24:00 admis) -> quart d'heure 0-96."""
    match = _TIME.match(value.strip())
    if match:
        hours, minutes = int(match.group(1)), int(match.group(2))
        quarter = hours * 4 + minutes // 15
        if minutes % 15 == 0 and 0 <= minutes < 60 and 0 <= quarter <= QUARTERS_PER_DAY:
            return quarter
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="time_invalid",
        translation_placeholders={"value": value},
    )


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> EcoControlCoordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted:
        entries = [entry for entry in entries if entry.entry_id == wanted]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="config_entry_missing"
        )
    if len(entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="config_entry_ambiguous"
        )
    return entries[0].runtime_data


def _resolve(coordinator: EcoControlCoordinator, reference: str) -> Planning:
    """Un planning se désigne par son numéro ou par son nom (sans tenir compte de la casse)."""
    plannings = coordinator.data.plannings if coordinator.data else {}
    wanted = reference.strip()
    if wanted in plannings:
        return plannings[wanted]
    for planning in plannings.values():
        if planning.name.casefold() == wanted.casefold():
            return planning
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="planning_unknown",
        translation_placeholders={"planning": reference},
    )


def _planning_sort_key(planning: Planning) -> int:
    return int(planning.id) if planning.id.isdigit() else 99


def _listing(coordinator: EcoControlCoordinator) -> list[dict[str, Any]]:
    plannings = coordinator.data.plannings if coordinator.data else {}
    return [
        {
            "id": planning.id,
            "name": planning.name,
            "rooms": planning.rooms,
            "editable": planning.id != NO_PLANNING_ID,
        }
        for planning in sorted(plannings.values(), key=_planning_sort_key)
    ]


def _describe(planning: Planning, schedule: Schedule) -> dict[str, Any]:
    return {
        "id": planning.id,
        "name": planning.name,
        "rooms": planning.rooms,
        "days": [
            {
                "day": DAY_KEYS[index],
                "ranges": [
                    {"start": quarter_to_time(start), "end": quarter_to_time(end)}
                    for start, end in schedule.days[index]
                ],
            }
            for index in range(DAYS_PER_WEEK)
        ],
    }


def _response(
    coordinator: EcoControlCoordinator,
    planning: Planning | None = None,
    schedule: Schedule | None = None,
) -> dict[str, Any]:
    return {
        "plannings": _listing(coordinator),
        "planning": _describe(planning, schedule) if planning and schedule else None,
    }


# ------------------------------------------------------------------ services


async def async_register_services(hass: HomeAssistant) -> None:
    async def get_planning(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        reference = call.data.get("planning")
        if not reference:
            return _response(coordinator)
        planning = _resolve(coordinator, reference)
        schedule = await coordinator.async_command(
            coordinator.client.get_schedule(planning.id), refresh=False
        )
        return _response(coordinator, planning, schedule)

    async def set_planning_day(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        planning = _resolve(coordinator, call.data["planning"])
        ranges = [
            (parse_time(item["start"]), parse_time(item["end"]))
            for item in call.data["ranges"]
        ]
        changes = {DAY_KEYS.index(day): ranges for day in call.data["days"]}
        schedule = await coordinator.async_command(
            coordinator.client.set_schedule_days(planning.id, changes), refresh=False
        )
        return _response(coordinator, planning, schedule)

    async def copy_planning_day(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        planning = _resolve(coordinator, call.data["planning"])
        source = DAY_KEYS.index(call.data["source_day"])
        current = await coordinator.async_command(
            coordinator.client.get_schedule(planning.id), refresh=False
        )
        changes = {
            DAY_KEYS.index(day): list(current.days[source])
            for day in call.data["target_days"]
            if DAY_KEYS.index(day) != source
        }
        schedule = await coordinator.async_command(
            coordinator.client.set_schedule_days(planning.id, changes), refresh=False
        )
        return _response(coordinator, planning, schedule)

    async def create_planning(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        created = await coordinator.async_command(
            coordinator.client.create_planning(call.data["name"]), refresh=False
        )
        await coordinator.async_refresh_plannings()
        return _response(coordinator, created, Schedule(created.id, ((),) * DAYS_PER_WEEK))

    async def rename_planning(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        planning = _resolve(coordinator, call.data["planning"])
        await coordinator.async_command(
            coordinator.client.rename_planning(planning.id, call.data["name"]),
            refresh=False,
        )
        await coordinator.async_refresh_plannings()
        return _response(coordinator)

    async def delete_planning(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        planning = _resolve(coordinator, call.data["planning"])
        await coordinator.async_command(
            coordinator.client.delete_planning(planning.id), refresh=False
        )
        await coordinator.async_refresh_plannings()
        return _response(coordinator)

    handlers = {
        "get_planning": (get_planning, SupportsResponse.ONLY),
        "set_planning_day": (set_planning_day, SupportsResponse.OPTIONAL),
        "copy_planning_day": (copy_planning_day, SupportsResponse.OPTIONAL),
        "create_planning": (create_planning, SupportsResponse.OPTIONAL),
        "rename_planning": (rename_planning, SupportsResponse.OPTIONAL),
        "delete_planning": (delete_planning, SupportsResponse.OPTIONAL),
    }
    for name, (handler, supports_response) in handlers.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=SCHEMAS[name],
            supports_response=supports_response,
        )
