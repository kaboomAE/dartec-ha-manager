"""Constants for the Dartec HA Manager agent."""

DOMAIN = "dartec_ha_manager"

CONF_SERVER_URL = "server_url"
CONF_PAIRING_TOKEN = "pairing_token"

SNAPSHOT_INTERVAL_S = 60
RECONNECT_MIN_S = 5
RECONNECT_MAX_S = 300

# Dispatcher signal: send a snapshot now, not at the next interval. Sent by a
# guarded update at every step, so the manager sees each one as it happens.
SIGNAL_SNAPSHOT_NOW = f"{DOMAIN}_snapshot_now"
