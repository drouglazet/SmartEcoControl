"""Intégration Home Assistant pour la box locale Smart ECOcontrol."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import EcoControlClient
from .const import DOMAIN
from .coordinator import EcoControlConfigEntry, EcoControlCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
# cartes Lovelace fournies : thermostat coloré et éditeur de planning
CARD_FILES = ("smart-ecocontrol-card.js", "smart-ecocontrol-planning-card.js")

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services de plannings, et chargement des cartes dans l'interface."""
    await async_register_services(hass)
    try:
        integration = await async_get_integration(hass, DOMAIN)
        urls = [f"/{DOMAIN}/{name}" for name in CARD_FILES]
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(url, str(FRONTEND_DIR / name), cache_headers=False)
                for url, name in zip(urls, CARD_FILES, strict=True)
            ]
        )
        for url in urls:
            # ?v=... : le navigateur recharge la carte après une mise à jour
            add_extra_js_url(hass, f"{url}?v={integration.version}")
    except Exception:  # noqa: BLE001 - les cartes sont un confort, jamais bloquantes
        _LOGGER.warning("Cartes Smart ECOcontrol non chargées dans l'interface", exc_info=True)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EcoControlConfigEntry) -> bool:
    client = EcoControlClient(async_get_clientsession(hass), entry.data[CONF_HOST])
    coordinator = EcoControlCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Appareil « box » : les pièces y sont rattachées (via_device)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Smart ECOcontrol",
        model="Smart ECOcontrol",
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def _async_reload_on_options(
    hass: HomeAssistant, entry: EcoControlConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EcoControlConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
