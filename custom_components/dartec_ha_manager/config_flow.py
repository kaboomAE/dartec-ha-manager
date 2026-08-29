"""Config flow: the installer pastes the server URL + pairing token generated
in the Dartec admin panel, we validate it against the cloud, done."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PAIRING_TOKEN, CONF_SERVER_URL, DOMAIN

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
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"Dartec: {info.get('customer_name', '')} / {info.get('instance_name', '')}",
                            data={CONF_SERVER_URL: server, CONF_PAIRING_TOKEN: token},
                        )
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)
