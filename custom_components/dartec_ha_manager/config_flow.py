"""Config flow: the installer pastes the server URL and either an enrolment
code or a pairing token from the Dartec admin panel.

An enrolment code is traded at the manager for this home's pairing token,
which is stored exactly as a pasted token would be — so nothing after this
flow knows or cares which one was typed. See enrolment.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import enrolment
from .const import CONF_PAIRING_TOKEN, CONF_SERVER_URL, DOMAIN
from .maintenance import (COMMISSIONING_MINUTES, OPT_COMMISSIONING_UNTIL,
                          OPT_STANDING_CONSENT)
from .service_policy import OPT_OFFSITE_BACKUPS

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
            typed = user_input[CONF_PAIRING_TOKEN].strip()
            if _insecure(server):
                return self.async_show_form(
                    step_id="user", data_schema=STEP_USER_SCHEMA,
                    errors={"base": "insecure_url"})
            code = enrolment.normalize(typed)
            try:
                session = async_get_clientsession(self.hass)
                if code:
                    info, error = await self._redeem(session, server, code)
                    token = info["pairing_token"] if info else None
                else:
                    info, error = await self._validate(session, server, typed)
                    token = typed
                if error:
                    errors["base"] = error
                else:
                    existing = await self.async_set_unique_id(info["instance_id"])
                    if existing is not None:
                        if not code:
                            return self.async_abort(reason="already_configured")
                        # Re-pairing a home that is already set up. Redeeming
                        # the code has just replaced this home's pairing token
                        # at the manager, so the entry must take the new one
                        # or it would never reconnect.
                        return self.async_update_reload_and_abort(
                            existing, data={**existing.data, CONF_SERVER_URL: server,
                                            CONF_PAIRING_TOKEN: token},
                            reason="repaired")
                    # The commissioning allowance, written once, here.
                    # Reaching this line means someone held a valid
                    # pairing token or enrolment code and was standing in
                    # this house typing it in — which is the same evidence
                    # the maintenance switch collects, gathered a minute
                    # earlier. Asking them to then walk to a tablet
                    # mid-install was the thing stopping homes from being set
                    # up. It is a timestamp, so it expires on its own, and
                    # nothing remote can extend it.
                    commissioned = (datetime.now(timezone.utc)
                                    + timedelta(minutes=COMMISSIONING_MINUTES))
                    return self.async_create_entry(
                        title=f"Dartec: {info.get('customer_name', '')} / {info.get('instance_name', '')}",
                        data={CONF_SERVER_URL: server, CONF_PAIRING_TOKEN: token},
                        options={OPT_COMMISSIONING_UNTIL: commissioned.isoformat()},
                    )
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def _validate(self, session, server: str, token: str):
        """(info, None) for a pairing token the manager knows, else (None, error)."""
        async with session.post(f"{server}/api/agent/validate",
                                json={"token": token}, timeout=15) as resp:
            if resp.status == 401:
                return None, "invalid_token"
            if resp.status != 200:
                return None, "cannot_connect"
            return await resp.json(), None

    async def _redeem(self, session, server: str, code: str):
        """(info including the new pairing_token, None), else (None, error).

        When this Home Assistant is already paired, the home it is paired to
        goes along as `instance_id`, and the manager refuses a code for any
        other home without using it up — rather than quietly giving this box a
        second identity and taking the credential from whichever home the code
        was really for."""
        body: dict[str, Any] = {"code": code}
        paired = [e.unique_id for e in self._async_current_entries(include_ignore=False)
                  if e.unique_id]
        if paired:
            body["instance_id"] = paired[0]
        async with session.post(f"{server}/api/agent/enrol", json=body, timeout=15) as resp:
            try:
                payload = await resp.json(content_type=None)
            except ValueError:
                payload = None
            if resp.status == 200 and isinstance(payload, dict) and payload.get("pairing_token"):
                return payload, None
            return None, enrolment.error_key(resp.status, payload)

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

    Offsite backup copies are a separate switch, because they are a different
    question. Unattended support is about what Dartec may *do* in the house;
    offsite copies are about the house's data leaving it. A customer can
    reasonably want either without the other, and both default to off.
    """

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            # Preserve the commissioning stamp: it is evidence of when pairing
            # happened, and rewriting options must not quietly extend it.
            options = dict(self._entry.options)
            options[OPT_STANDING_CONSENT] = bool(user_input.get(OPT_STANDING_CONSENT))
            options[OPT_OFFSITE_BACKUPS] = bool(user_input.get(OPT_OFFSITE_BACKUPS))
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(OPT_STANDING_CONSENT,
                             default=self._entry.options.get(OPT_STANDING_CONSENT, False)): bool,
                vol.Optional(OPT_OFFSITE_BACKUPS,
                             default=self._entry.options.get(OPT_OFFSITE_BACKUPS, False)): bool,
            }),
        )
