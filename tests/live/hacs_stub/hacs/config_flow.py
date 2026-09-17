"""One step, no questions: the live driver passes the token as the only field."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from . import DOMAIN


class StubHacsFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user",
                                        data_schema=vol.Schema({vol.Required("token"): str}))
        return self.async_create_entry(title="", data={"token": user_input["token"],
                                                       "experimental": True})
