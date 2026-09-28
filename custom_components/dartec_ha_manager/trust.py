"""What the agent accepts from the manager, whoever the manager turns out to be.

The agent is the last line of defence against *our own cloud being
compromised* (commands.py). So anything a command carries that decides where
this home connects, how much it reads, or which path it touches is checked
here against values fixed in this code, never against values the command
supplies. docs/trust-boundary.md says why, and lists the rules a new command
has to follow.

No Home Assistant imports, so the suite can test it directly.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

# ── Where the home may connect ──────────────────────────────────────────────
#
# Fixed here, not taken from the pairing: the manager's own address is chosen
# by whoever pairs the home, but a URL inside a command is chosen by whoever
# is sending commands, and that is exactly who this module does not trust.
# Adding a host is a deliberate release of this integration.

# Offsite backup copies go to, and blueprint media come from, the manager.
MANAGER_HOSTS = frozenset({"manager.dartec.ae"})
# Dartec Link enrols the home with Dartec's Headscale and nothing else.
LINK_HOSTS = frozenset({"headscale.dartec.ae"})

# A redirect is followed only to another allowed https URL, and only this many
# times. The manager's own routes do not redirect today.
MAX_REDIRECTS = 3
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")


def check_url(url: object, hosts: frozenset[str]) -> str | None:
    """Refusal if `url` is not an https URL on one of `hosts`, else None.

    Exact host match, default port only, no credentials in the URL. Whitespace,
    control characters and backslashes are refused outright rather than
    normalised, because browsers and HTTP clients disagree about them and a
    parser disagreement is how an allowlist is walked around.
    """
    if not isinstance(url, str) or not url:
        return "a URL is required"
    if _CONTROL.search(url):
        return "the URL contains whitespace, control characters or a backslash"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "the URL could not be parsed"
    if parts.scheme.lower() != "https":
        return "only https URLs are accepted"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "a URL with credentials in it is not accepted"
    host = (parts.hostname or "").rstrip(".").lower()
    if host not in hosts:
        return f"'{host or url}' is not a Dartec address this home connects to"
    if port not in (None, 443):
        return "only the default https port is accepted"
    if parts.fragment:
        return "a URL with a fragment is not accepted"
    return None


def redirect_target(current: str, location: object,
                    hosts: frozenset[str]) -> tuple[str | None, str | None]:
    """(next URL, None) for a redirect that stays on `hosts`, or
    (None, refusal). The same check as the first URL, on the resolved target."""
    if not isinstance(location, str) or not location:
        return None, "a redirect without a Location"
    target = urljoin(current, location)
    refusal = check_url(target, hosts)
    if refusal:
        return None, f"a redirect away from Dartec was refused ({refusal})"
    return target, None


def canonical_login_server(value: object) -> str | None:
    """The Headscale origin to hand the Dartec Link add-on, or None.

    What the add-on receives is rebuilt from the allowlist, not copied from
    the command, so nothing the manager adds (a path, a port, a query) can
    reach it.
    """
    if isinstance(value, str):
        value = value.strip().rstrip("/")
    if check_url(value, LINK_HOSTS):
        return None
    parts = urlsplit(value)
    if parts.path or parts.query:
        return None
    return f"https://{(parts.hostname or '').rstrip('.').lower()}"


# ── Ids that become part of a path ──────────────────────────────────────────
#
# An id from a command that is interpolated into a URL or a file path is
# checked against its own narrow pattern first. None of these allow "/", "\",
# "%" or ".", so no id can climb out of the path it is put in, encoded or not.

AUTOMATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Supervisor add-on slugs: a repository hash, an underscore and a name.
ADDON_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Home Assistant backup agents: "<domain>.<name>", e.g. backup.local.
BACKUP_AGENT_RE = re.compile(r"^[a-z0-9_]{1,64}\.[A-Za-z0-9_-]{1,64}$")


def valid_id(value: object, pattern: re.Pattern[str]) -> str | None:
    """`value` if it is a string matching `pattern` exactly, else None."""
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return None


# ── Sizes ───────────────────────────────────────────────────────────────────


def bounded_mb(value: object, default: int, ceiling: int) -> int | None:
    """A size limit in MB from a command: `default` when absent, the value
    when it is a whole number from 1 to `ceiling`, and None (refuse) for
    anything else. The command can ask for less than the ceiling, never more."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > ceiling:
        return None
    return value
