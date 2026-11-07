"""Intégration Home Assistant pour la box locale Smart ECOcontrol."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.start import async_at_started
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
        # ?v=... : le navigateur recharge la carte après une mise à jour
        versioned = [f"{url}?v={integration.version}" for url in urls]
        for url in versioned:
            add_extra_js_url(hass, url)

        async def _register(_hass: HomeAssistant) -> None:
            await _async_register_lovelace_resources(hass, versioned)

        async_at_started(hass, _register)
    except Exception:  # noqa: BLE001 - les cartes sont un confort, jamais bloquantes
        _LOGGER.warning("Cartes Smart ECOcontrol non chargées dans l'interface", exc_info=True)
    return True


async def _async_register_lovelace_resources(hass: HomeAssistant, urls: list[str]) -> None:
    """Inscrit les cartes dans les ressources des tableaux de bord.

    C'est ce que fait HACS pour les cartes : l'interface charge alors les cartes
    même si la page a gardé une ancienne version de son index en cache. Sans effet si
    les ressources sont gérées en YAML (dans ce cas, ajoutez-les dans configuration.yaml).
    """
    try:
        data = hass.data.get("lovelace")
        resources = getattr(data, "resources", None)
        if resources is None or getattr(data, "resource_mode", "storage") != "storage":
            return
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        for url in urls:
            path = url.split("?", 1)[0]
            known = [item for item in resources.async_items() if item["url"].split("?", 1)[0] == path]
            if not known:
                await resources.async_create_item({"res_type": "module", "url": url})
                _LOGGER.info("Carte ajoutée aux ressources des tableaux de bord : %s", url)
            elif known[0]["url"] != url or known[0].get("type") != "module":
                await resources.async_update_item(known[0]["id"], {"res_type": "module", "url": url})
                _LOGGER.info("Ressource de carte mise à jour : %s", url)
    except Exception:  # noqa: BLE001 - confort : jamais bloquant
        _LOGGER.warning(
            "Impossible d'ajouter les cartes aux ressources : ajoutez-les à la main "
            "(Paramètres > Tableaux de bord > Ressources)",
            exc_info=True,
        )


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
