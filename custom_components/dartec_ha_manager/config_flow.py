"""Config flow: the installer pastes the server URL + pairing token generated
in the Dartec admin panel, we validate it against the cloud, done."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PAIRING_TOKEN, CONF_SERVER_URL, DOMAIN
from .maintenance import (OPT_COMMISSIONING_UNTIL, OPT_STANDING_CONSENT,
                          commissioning_deadline)

# A bare "http://" URL is silently downgraded to plaintext ws:// by CloudLink,
# which would put the pairing token — the key to this whole home — on the wire
# in the clear. Only loopback is exempt, for developing against a local server.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def _insecure(url: str) -> bool:
    lowered = url.lower().strip()
    if lowered.startswith("https://"):
        return False
    authority = lowered.split("://", 1)[-1].split("/", 1)[0]
    # Bracketed IPv6 keeps its colons, so strip the brackets before the port.
    host = (authority.split("]", 1)[0] + "]" if authority.startswith("[")
            else authority.split(":", 1)[0])
    return host not in _LOCAL_HOSTS


STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_SERVER_URL, default="https://manager.dartec.ae"): str,
    vol.Required(CONF_PAIRING_TOKEN): str,
})


class DartecConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            server = user_input[CONF_SERVER_URL].rstrip("/")
            token = user_input[CONF_PAIRING_TOKEN].strip()
            if _insecure(server):
                return self.async_show_form(
                    step_id="user", data_schema=STEP_USER_SCHEMA,
                    errors={"base": "insecure_url"})
            try:
                session = async_get_clientsession(self.hass)
                async with session.post(f"{server}/api/agent/validate",
                                        json={"token": token}, timeout=15) as resp:
                    if resp.status == 401:
                        errors["base"] = "invalid_token"
                    elif resp.status != 200:
                        errors["base"] = "cannot_connect"
                    else:
                        info = await resp.json()
                        await self.async_set_unique_id(info["instance_id"])
                        # Re-pairing a home that is already paired stops here,
                        # before any commissioning is written, so pairing
                        # again cannot quietly restart the 30 days. The only
                        # way to a fresh period is to delete this integration
                        # on the home and pair it again with a valid token —
                        # a person on site deliberately starting over.
                        self._abort_if_unique_id_configured()
                        # Commissioning, written once, here. Reaching this
                        # line means someone held a valid pairing token and
                        # was standing in this house typing it in — which is
                        # the same evidence the maintenance switch collects,
                        # gathered a minute earlier. Asking them to then walk
                        # to a tablet mid-install was the thing stopping homes
                        # from being set up. It is a deadline, so it ends on
                        # its own at the cap; marking the install complete
                        # ends it sooner; nothing remote can extend it.
                        commissioned = commissioning_deadline()
                        return self.async_create_entry(
                            title=f"Dartec: {info.get('customer_name', '')} / {info.get('instance_name', '')}",
                            data={CONF_SERVER_URL: server, CONF_PAIRING_TOKEN: token},
                            options={OPT_COMMISSIONING_UNTIL: commissioned.isoformat()},
                        )
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DartecOptionsFlow(config_entry)


class DartecOptionsFlow(config_entries.OptionsFlow):
    """Standing consent, granted and revoked on the home.

    A site that wants Dartec to work unattended says so here, in Home
    Assistant's own settings, where the person who owns the house can see it
    and switch it off. Deliberately not a setting in the manager: the point of
    the whole consent model is that this answer is not ours to give.
    """

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            # Preserve the commissioning stamp: it is evidence of when pairing
            # happened, and rewriting options must not quietly extend it.
            options = dict(self._entry.options)
            options[OPT_STANDING_CONSENT] = bool(user_input.get(OPT_STANDING_CONSENT))
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(OPT_STANDING_CONSENT,
                             default=self._entry.options.get(OPT_STANDING_CONSENT, False)): bool,
            }),
        )
