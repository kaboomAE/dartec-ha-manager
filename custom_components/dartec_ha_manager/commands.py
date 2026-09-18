"""Command executor — runs server-issued commands against this HA instance.

Security model: this agent is the last line of defence, because the threat it
guards against is *our own cloud being compromised*. A guardrail that trusts
the server for anything is therefore worth nothing.

Three gates, all enforced here:

* Every ``call_service`` is checked as a ``domain.service`` pair against
  ``service_policy.py`` — default deny, with a permanently blocked tier that
  no consent flow can unlock.
* Sensitive operations additionally need a maintenance window the homeowner
  opened locally — ``service_policy.is_sensitive`` decides, which for almost
  every action means membership of ``SENSITIVE_ACTIONS``. The exception is
  ``blueprint_install``, where staging a new file is inert and overriding one
  reloads live automations; see that function. ``maintenance.py`` holds the
  window itself.
* A few actions need a standing opt-in on the home instead of a window —
  ``service_policy.OPT_IN_ACTIONS``. Offsite backup copies are the one today:
  they must run unattended, so they cannot wait for a window, but the data is
  leaving the house, so they cannot run without the home having agreed.

Everything that runs, and everything refused, is written to this instance's
own logbook, so the house keeps its own record independent of ours.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

from . import maintenance
from .const import DOMAIN
from .service_policy import (GUARDED_ACTIONS, GUARDED_WITHOUT_CONSENT,
                             check_call_service, check_guarded,
                             check_opt_in, check_own_entities, is_sensitive)

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"

_ADDON_ACTIONS = {
    "addon_restart": "restart",
    "addon_start": "start",
    "addon_stop": "stop",
}


def _refuse(hass: HomeAssistant, what: str, why: str, code: str = "") -> dict:
    """Deny a command, and make sure the house knows it was attempted."""
    _LOGGER.warning("Refused %s: %s", what, why)
    maintenance.logbook(hass, f"Refused remote command '{what}': {why}")
    result = {"ok": False, "refused": True, "detail": why}
    if code:
        result["code"] = code
    return result


def _describe(cmd: dict[str, Any]) -> str:
    action = cmd.get("action")
    if action == "call_service":
        target = cmd.get("service_data", {}) or {}
        entity = target.get("entity_id") if isinstance(target, dict) else None
        suffix = f" on {entity}" if entity else ""
        return f"{cmd.get('domain')}.{cmd.get('service')}{suffix}"
    return str(action)


async def execute_command(hass: HomeAssistant, cmd: dict[str, Any]) -> dict[str, Any]:
    from .backup_cmds import HANDLERS as BACKUP_HANDLERS
    from .blueprint_cmds import HANDLERS as BLUEPRINT_HANDLERS
    from .ha_update import HANDLERS as HA_UPDATE_HANDLERS
    from .hacs_cmds import HANDLERS as HACS_HANDLERS
    from .helper_cmds import HANDLERS as HELPER_HANDLERS
    from .home_cmds import HANDLERS as HOME_HANDLERS
    from .integration_cmds import HANDLERS as INTEGRATION_HANDLERS
    from .media_cmds import HANDLERS as MEDIA_HANDLERS
    from .lovelace_cmds import HANDLERS as LOVELACE_HANDLERS
    from .link_cmds import HANDLERS as LINK_HANDLERS
    from .registry_cmds import HANDLERS as REGISTRY_HANDLERS
    from .tunnel_cmds import HANDLERS as TUNNEL_HANDLERS
    from .user_cmds import HANDLERS as USER_HANDLERS

    action = cmd.get("action")
    try:
        # Window management is always reachable — the cloud may ask, and may
        # read the state, but neither grants it anything.
        if action == "maintenance_status":
            return {"ok": True, **maintenance.status(hass)}
        if action == "maintenance_request":
            return maintenance.request_window(hass, str(cmd.get("reason") or ""))
        # Ending commissioning only ever takes permission away, so the manager
        # may send it without consent. Nothing in the command is read: there
        # is no deadline or duration it could carry that would be honoured,
        # which is what keeps "close" from becoming "extend".
        if action == "commissioning_complete":
            return maintenance.complete_commissioning(hass, "manager")

        # Consent, not merely the switch: an interactive window, the
        # commissioning period that pairing opened, or a standing
        # opt-in set on this home. All three are decided here; none can be
        # asserted by the caller. See maintenance.consent.
        granted = maintenance.consent(hass)
        window_open = granted["allowed"]

        if action == "call_service":
            refusal = (check_call_service(cmd, maintenance_open=window_open)
                       or check_own_entities(cmd.get("service_data") or {},
                                             _own_entity_ids(hass)))
            if refusal:
                return _refuse(hass, _describe(cmd), refusal)
            result = await _call_service(hass, cmd)
            maintenance.logbook(hass, f"Dartec called {_describe(cmd)}")
            return result

        # Guarded updates need no window: the owner's decision, with its
        # limits, is in service_policy.GUARDED_ACTIONS. They answer to the
        # home's opt-out and to a strict shape instead, and are logged whether
        # they run or not, with the version they were asked for.
        if action in GUARDED_ACTIONS and action not in GUARDED_WITHOUT_CONSENT:
            what = f"{action} {cmd.get('version', '')}".strip()
            refused = check_guarded(cmd, maintenance.entry_options(hass))
            if refused:
                return _refuse(hass, what, refused[1], code=refused[0])
            result = await HA_UPDATE_HANDLERS[action](hass, cmd)
            if not result.get("ok"):
                maintenance.logbook(hass, f"Dartec asked for '{what}'; not started: "
                                          f"{result.get('detail')}")
            return result

        sensitive = is_sensitive(cmd)
        guarded_run = False
        if sensitive and not window_open and action in GUARDED_WITHOUT_CONSENT:
            # No consent, but this action may still run in its guarded shape
            # (service_policy.GUARDED_ACTIONS): nothing but the listed keys,
            # and never if the homeowner has turned guarded updates off.
            refused = check_guarded(cmd, maintenance.entry_options(hass))
            if refused:
                return _refuse(hass, str(action), refused[1], code=refused[0])
            guarded_run = True
        elif sensitive and not window_open:
            # `force` in the command is read only to answer it. A cloud that
            # asks to skip consent is told no in the same words as one that
            # did not ask, because the answer does not depend on the asking.
            return _refuse(hass, str(action),
                           f"'{action}' needs consent from this home. Ask the "
                           "homeowner to switch 'Allow Dartec support' on, or "
                           "request a window from the manager. (Pairing opens "
                           "a commissioning period that lasts until the "
                           "install is marked complete, "
                           f"{maintenance.COMMISSIONING_DAYS} days by default; for a site "
                           "that wants unattended support, turn it on in this "
                           "integration's options.)", code="consent")

        refusal = check_opt_in(cmd, maintenance.entry_options(hass))
        if refusal:
            return _refuse(hass, str(action), refusal)

        if action in _ADDON_ACTIONS:
            result = await _addon_action(hass, cmd.get("addon_slug", ""),
                                         _ADDON_ACTIONS[action])
        elif action in BLUEPRINT_HANDLERS:
            result = await BLUEPRINT_HANDLERS[action](hass, cmd)
        elif action in HELPER_HANDLERS:
            result = await HELPER_HANDLERS[action](hass, cmd)
        elif action in INTEGRATION_HANDLERS:
            result = await INTEGRATION_HANDLERS[action](hass, cmd)
        elif action in MEDIA_HANDLERS:
            result = await MEDIA_HANDLERS[action](hass, cmd)
        elif action in LOVELACE_HANDLERS:
            result = await LOVELACE_HANDLERS[action](hass, cmd)
        elif action in HACS_HANDLERS:
            result = await HACS_HANDLERS[action](hass, cmd)
        elif action in HOME_HANDLERS:
            result = await HOME_HANDLERS[action](hass, cmd)
        elif action in REGISTRY_HANDLERS:
            result = await REGISTRY_HANDLERS[action](hass, cmd)
        elif action in USER_HANDLERS:
            result = await USER_HANDLERS[action](hass, cmd)
        elif action in TUNNEL_HANDLERS:
            result = await TUNNEL_HANDLERS[action](hass, cmd)
        elif action in LINK_HANDLERS:
            result = await LINK_HANDLERS[action](hass, cmd)
        elif action in BACKUP_HANDLERS:
            result = await BACKUP_HANDLERS[action](hass, cmd)
        else:
            return {"ok": False, "detail": f"unsupported action '{action}'"}

        if guarded_run:
            maintenance.logbook(hass, f"Dartec ran '{action}' as an approved update "
                                      "(latest release, upgrade only), without the "
                                      "maintenance switch: "
                                      f"{result.get('detail') or result.get('ok')}")
        elif sensitive:
            # Which consent was relied on, not just that there was some. If a
            # homeowner ever asks why something ran while they were out, the
            # answer is in their own logbook, in their own words.
            how = {"window": "under an open maintenance window",
                   "commissioning": "during the commissioning period that "
                                    "started when this home was paired",
                   "standing": "under the standing 'unattended support' "
                               "setting on this integration"}.get(
                granted.get("source"), "under consent from this home")
            maintenance.logbook(hass, f"Dartec ran '{action}' {how}")
        elif action == "media_upload" and result.get("uploaded"):
            maintenance.logbook(hass, f"Dartec added media file "
                                      f"'{result.get('media_content_id')}'")
        elif action == "blueprint_install" and result.get("ok"):
            # Inert, so it needed no window — but a file did appear on their
            # disk, and the house keeping its own record of that is the whole
            # point of the logbook.
            maintenance.logbook(hass, f"Dartec staged automation blueprint "
                                      f"'{result.get('path')}'; nothing uses it yet")
        elif action == "lovelace_update" and result.get("ok"):
            # Not consequential enough to need consent, but it is a name the
            # household reads every day changing under them.
            maintenance.logbook(hass, f"Dartec {result.get('detail')}")
        elif action == "hacs_token_set":
            # Routine, so no consent line above — but a credential in their
            # house changed, or was changed and put back, and they should be
            # able to see that. Fingerprints only: the token is never written
            # anywhere. Refusals before the entry was touched are not logged.
            from .hacs_token import logbook_line

            line = logbook_line(result)
            if line:
                maintenance.logbook(hass, line)
        return result
    except Exception as err:  # noqa: BLE001 — always answer the cloud, never raise
        _LOGGER.warning("Command %s failed: %s", action, err)
        return {"ok": False, "detail": str(err)}


async def _addon_action(hass: HomeAssistant, slug: str, verb: str) -> dict:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"ok": False, "detail": "No Supervisor on this install (Container/Core)"}
    if not slug:
        return {"ok": False, "detail": "addon_slug missing"}
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    async with session.post(f"{SUPERVISOR_URL}/addons/{slug}/{verb}",
                            headers={"Authorization": f"Bearer {token}"}, timeout=90) as resp:
        if resp.status == 200:
            return {"ok": True, "detail": f"{verb} {slug} succeeded"}
        body = await resp.text()
        return {"ok": False, "detail": f"Supervisor returned {resp.status}: {body[:200]}"}


# Always treated as ours, even if the registry cannot be read.
_KNOWN_OWN_ENTITIES = ("switch.allow_dartec_support",)


def _own_entity_ids(hass: HomeAssistant) -> set[str]:
    """Every entity this integration created, by its current entity id."""
    own = set(_KNOWN_OWN_ENTITIES)
    try:
        from homeassistant.helpers import entity_registry as er

        own.update(e.entity_id for e in er.async_get(hass).entities.values()
                   if e.platform == DOMAIN)
    except Exception as err:  # noqa: BLE001 — the fixed list above still applies
        _LOGGER.debug("entity registry unreadable for own-entity check: %s", err)
    return own


async def _call_service(hass: HomeAssistant, cmd: dict) -> dict:
    """Execute a call already cleared by ``service_policy.check_call_service``.

    Never call this directly — the policy check is the only thing standing
    between the cloud and every service in the house.
    """
    await hass.services.async_call(
        cmd["domain"], cmd["service"], cmd.get("service_data") or {}, blocking=True
    )
    return {"ok": True, "detail": f"called {cmd['domain']}.{cmd['service']}"}
