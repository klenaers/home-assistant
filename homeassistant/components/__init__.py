"""
This package contains components that can be plugged into Home Assistant.

Component design guidelines:
- Each component defines a constant DOMAIN that is equal to its filename.
- Each component that tracks states should create state entity names in the
  format "<DOMAIN>.<OBJECT_ID>".
- Each component should publish services only under its own domain.
"""
import asyncio
import itertools as it
import logging
from typing import Awaitable

import voluptuous as vol

import homeassistant.core as ha
import homeassistant.config as conf_util
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.restore_state
from homeassistant.helpers.service import extract_entity_ids
from homeassistant.helpers import intent
from homeassistant.const import (
    ATTR_ENTITY_ID, SERVICE_TURN_ON, SERVICE_TURN_OFF, SERVICE_TOGGLE,
    SERVICE_HOMEASSISTANT_STOP, SERVICE_HOMEASSISTANT_RESTART,
    RESTART_EXIT_CODE)
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

SERVICE_RELOAD_CORE_CONFIG = 'reload_core_config'
SERVICE_CHECK_CONFIG = 'check_config'
SERVICE_UPDATE_ENTITY = 'update_entity'
SCHEMA_UPDATE_ENTITY = vol.Schema({
    ATTR_ENTITY_ID: cv.entity_id
})


def is_on(hass, entity_id=None):
    """Load up the module to call the is_on method.

    If there is no entity id given we will check all.
    """
    if entity_id:
        entity_ids = hass.components.group.expand_entity_ids([entity_id])
    else:
        entity_ids = hass.states.entity_ids()

    for ent_id in entity_ids:
        domain = ha.split_entity_id(ent_id)[0]

        try:
            component = getattr(hass.components, domain)

        except ImportError:
            _LOGGER.error('Failed to call %s.is_on: component not found',
                          domain)
            continue

        if not hasattr(component, 'is_on'):
            _LOGGER.warning("Component %s has no is_on method.", domain)
            continue

        if component.is_on(ent_id):
            return True

    return False


async def async_setup(hass: ha.HomeAssistant, config: dict) -> Awaitable[bool]:
    """Set up general services related to Home Assistant."""
    async def async_handle_turn_service(service):
        """Handle calls to homeassistant.turn_on/off."""
        entity_ids = extract_entity_ids(hass, service)

        # Generic turn on/off method requires entity id
        if not entity_ids:
            _LOGGER.error(
                "homeassistant/%s cannot be called without entity_id",
                service.service)
            return

        # Group entity_ids by domain. groupby requires sorted data.
        by_domain = it.groupby(sorted(entity_ids),
                               lambda item: ha.split_entity_id(item)[0])

        tasks = []

        for domain, ent_ids in by_domain:
            # We want to block for all calls and only return when all calls
            # have been processed. If a service does not exist it causes a 10
            # second delay while we're blocking waiting for a response.
            # But services can be registered on other HA instances that are
            # listening to the bus too. So as an in between solution, we'll
            # block only if the service is defined in the current HA instance.
            blocking = hass.services.has_service(domain, service.service)

            # Create a new dict for this call
            data = dict(service.data)

            # ent_ids is a generator, convert it to a list.
            data[ATTR_ENTITY_ID] = list(ent_ids)

            tasks.append(hass.services.async_call(
                domain, service.service, data, blocking))

        await asyncio.wait(tasks, loop=hass.loop)

    hass.services.async_register(
        ha.DOMAIN, SERVICE_TURN_OFF, async_handle_turn_service)
    hass.services.async_register(
        ha.DOMAIN, SERVICE_TURN_ON, async_handle_turn_service)
    hass.services.async_register(
        ha.DOMAIN, SERVICE_TOGGLE, async_handle_turn_service)
    hass.helpers.intent.async_register(intent.ServiceIntentHandler(
        intent.INTENT_TURN_ON, ha.DOMAIN, SERVICE_TURN_ON, "Turned {} on"))
    hass.helpers.intent.async_register(intent.ServiceIntentHandler(
        intent.INTENT_TURN_OFF, ha.DOMAIN, SERVICE_TURN_OFF,
        "Turned {} off"))
    hass.helpers.intent.async_register(intent.ServiceIntentHandler(
        intent.INTENT_TOGGLE, ha.DOMAIN, SERVICE_TOGGLE, "Toggled {}"))

    async def async_handle_core_service(call):
        """Service handler for handling core services."""
        if call.service == SERVICE_HOMEASSISTANT_STOP:
            hass.async_create_task(hass.async_stop())
            return

        try:
            errors = await conf_util.async_check_ha_config_file(hass)
        except HomeAssistantError:
            return

        if errors:
            _LOGGER.error(errors)
            hass.components.persistent_notification.async_create(
                "Config error. See dev-info panel for details.",
                "Config validating", "{0}.check_config".format(ha.DOMAIN))
            return

        if call.service == SERVICE_HOMEASSISTANT_RESTART:
            hass.async_create_task(hass.async_stop(RESTART_EXIT_CODE))

    async def async_handle_update_service(call):
        """Service handler for updating an entity."""
        await hass.helpers.entity_component.async_update_entity(
            call.data[ATTR_ENTITY_ID])

    hass.services.async_register(
        ha.DOMAIN, SERVICE_HOMEASSISTANT_STOP, async_handle_core_service)
    hass.services.async_register(
        ha.DOMAIN, SERVICE_HOMEASSISTANT_RESTART, async_handle_core_service)
    hass.services.async_register(
        ha.DOMAIN, SERVICE_CHECK_CONFIG, async_handle_core_service)
    hass.services.async_register(
        ha.DOMAIN, SERVICE_UPDATE_ENTITY, async_handle_update_service,
        schema=SCHEMA_UPDATE_ENTITY)

    async def async_handle_reload_config(call):
        """Service handler for reloading core config."""
        try:
            conf = await conf_util.async_hass_config_yaml(hass)
        except HomeAssistantError as err:
            _LOGGER.error(err)
            return

        await conf_util.async_process_ha_core_config(
            hass, conf.get(ha.DOMAIN) or {})

    hass.services.async_register(
        ha.DOMAIN, SERVICE_RELOAD_CORE_CONFIG, async_handle_reload_config)

    homeassistant.helpers.restore_state.async_setup(hass)

    return True
