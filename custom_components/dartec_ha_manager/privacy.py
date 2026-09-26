"""Household members' names never leave the home (dartec-ha-manager#20).

A person's name reached the manager in more places than anyone set out to
send it: a `person.*` entity is named after its person, a phone is "Layla's
iPhone" with a `notify.mobile_app_laylas_iphone` action and a dozen
`sensor.laylas_iphone_*` entities, Home Assistant's log lines can name a
user, a room can be "Omar's bedroom", and the `users_list` reply named every
account. The owner's rule for My Home is that staff see counts, not names,
because names are the customer's personal data.

Cutting each of those out one by one would have broken what the manager
does with them (a person tile on a family dashboard, a Live Activity pushed
to a phone) and would have missed the next one. So the rule is applied once,
at the only door the agent has to the manager (`cloud_link`):

* **Outbound**, every snapshot and every command reply is passed through
  `Pseudonymiser.scrub`. Each household name, wherever it appears in any
  string or dict key, is replaced by a stable anonymous id such as
  `hm_3f2a91c07d`. "Layla's iPhone" becomes "hm_3f2a91c07d's iPhone" and
  `person.omar` becomes `person.hm_8c1e0b44a2`.
* **Inbound**, every command is passed through `Pseudonymiser.reveal`, which
  puts the real name back where an id appears. A dashboard the manager
  builds with a `person.hm_8c1e0b44a2` tile is saved on the home with
  `person.omar`; a Live Activity sent to `notify.mobile_app_hm_3f2a91c07d_iphone`
  reaches Layla's phone.

The id is an HMAC of the name under a secret that is created on the home and
never sent, so the manager cannot recover a name by hashing a list of common
first names, and the same name gets the same id on every snapshot. Nothing
else changes: ids that are not household names pass through untouched, so
the manager's view of lights, sensors, rooms and panels is exactly what it
was.

Whose names: everyone the household panel counts (the owner and every
person account, see `household.kind`), by display name and by username, and
every `person.*` entity except those linked to Dartec's, a panel's or Home
Assistant's own accounts. Each name is matched as written, word by word
(words of three letters or more, so "Omar Khalid" also covers "Omar's
iPhone"), and as Home Assistant would slug it for an entity id, with or
without a possessive "s" (`laylas_iphone`). Arabic names are matched as
Arabic words. Words that are not names on their own ("home", "admin",
"bin") are never matched, so a person called "Home" cannot garble every
entity in the house; see GENERIC.

What this cannot do: hide a name it does not know. A phone named after a
child who has no person or account in Home Assistant still reaches the
manager under that name. That is the limit of what the home itself knows.

No Home Assistant imports at module level: the rules are unit-tested
without Home Assistant (tests/test_privacy.py). `async_filter` is the adapter.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import unicodedata
from typing import Any, Callable, Iterable

_LOGGER = logging.getLogger(__name__)

ALIAS_PREFIX = "hm_"
ALIAS_HEX = 10
# Letters and digits only on either side. An underscore counts as a
# boundary, because inside an entity id or a slug it is the separator.
_EDGE_BEFORE = r"(?<![^\W_])"
_EDGE_AFTER = r"(?![^\W_])"
ALIAS_RE = re.compile(_EDGE_BEFORE + ALIAS_PREFIX + r"[0-9a-f]{%d}" % ALIAS_HEX + _EDGE_AFTER)
# A string shaped like an entity id, a service or a slug. An id put back
# into one of these takes the slug form of the name, anywhere else the name
# as written.
_IDENTIFIER = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")

MIN_WORD = 3

# Words that are never a name on their own. A household member may be
# called "Owner" or "Home" in Home Assistant, and scrubbing those would
# replace every "home" state, every "Home Assistant" and every "admin" in
# a log line with an id. Kept lowercase; compared casefolded and slugged.
GENERIC = frozenset({
    "admin", "administrator", "owner", "user", "users", "guest", "guests",
    "family", "home", "house", "homeassistant", "assistant", "person", "people",
    "member", "dartec", "panel", "tablet", "phone", "mobile", "iphone", "ipad",
    "android", "pixel", "galaxy", "watch", "supervisor", "cloud", "local",
    "system", "service", "device", "sensor", "light", "switch", "kitchen",
    "living", "room", "bedroom", "office", "garage", "the", "and", "not", "away",
    "unknown", "unavailable", "none", "null", "true", "false", "default",
    "main", "master", "test", "demo", "maid", "driver", "nanny", "staff",
    # Parts of a name that are not a name: "Mohammed bin Rashid".
    "bin", "bint", "ibn", "abu", "umm", "van", "von", "der", "del", "des",
})

# Keys whose values are never rewritten on the way in: a credential that
# happened to contain an id would be corrupted, and none can carry a name.
_SECRET_KEYS = frozenset({"token", "password", "pairing_token", "fingerprint"})
# Digests of the entity inventory. The agent computes them over the real
# rows; folding the names in means the manager refetches its full copy when
# a new name changes which ids are aliased (see registry_paging).
_DIGEST_KEYS = frozenset({"entity_inventory_digest", "inventory_digest"})


def ascii_slug(text: str) -> str:
    """A stand-in for Home Assistant's `slugify` where it is not available
    (the unit tests). Latin letters and digits only, joined by underscores;
    apostrophes vanish, as they do in Home Assistant."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"['’]", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _generic(term: str) -> bool:
    return term.casefold() in GENERIC or term.casefold().replace(" ", "") in GENERIC


class Pseudonymiser:
    """Scrub household names out of anything bound for the manager, and put
    them back into anything coming from it.

    `names` are display names and usernames. `entity_ids` are the home's
    `person.*` entity ids, whose object id is matched whatever it is, since a
    renamed person keeps the entity id of their old name.
    """

    def __init__(self, secret: bytes, names: Iterable[str] = (),
                 entity_ids: Iterable[str] = (),
                 slugify: Callable[[str], str] = ascii_slug,
                 fold_digests: bool = True) -> None:
        self._secret = secret
        self._slugify = slugify
        self._fold_digests = fold_digests
        self._names = [n for n in names if isinstance(n, str)]
        self._entity_ids = [e for e in entity_ids if isinstance(e, str)]
        names, entity_ids = self._names, self._entity_ids
        # casefolded match -> alias; alias -> (as written, as slugged)
        self._alias_of: dict[str, str] = {}
        self._forms: dict[str, tuple[str, str]] = {}
        for name in names:
            self._add_name(name)
        for entity_id in entity_ids:
            if isinstance(entity_id, str) and entity_id.startswith("person."):
                object_id = entity_id.split(".", 1)[1]
                if object_id and not _generic(object_id):
                    self._add_term(object_id, object_id, allow_short=True)
        terms = sorted(self._alias_of, key=len, reverse=True)
        self._pattern = (re.compile(_EDGE_BEFORE + "(" + "|".join(map(re.escape, terms)) + ")"
                                    + _EDGE_AFTER, re.IGNORECASE)
                         if terms else None)
        self.fingerprint = hashlib.sha256(
            "\n".join(sorted(self._forms)).encode()).hexdigest()[:16]

    def merged(self, other: "Pseudonymiser") -> "Pseudonymiser":
        """One filter hiding both sets of names. For a command that renames
        or removes someone: its reply is scrubbed once, with the names as
        they stood before and after, so a digest in it is folded once."""
        return Pseudonymiser(self._secret, self._names + other._names,
                             self._entity_ids + other._entity_ids,
                             self._slugify, self._fold_digests)

    # ---- building the table ------------------------------------------------

    def alias(self, key: str) -> str:
        digest = hmac.new(self._secret, key.encode("utf-8"), hashlib.sha256).hexdigest()
        return ALIAS_PREFIX + digest[:ALIAS_HEX]

    def _add_term(self, written: str, slug: str, *, allow_short: bool = False) -> None:
        written = written.strip()
        if not written or _generic(written) or (slug and _generic(slug)):
            return
        if len(written) < MIN_WORD and not allow_short:
            return
        # One id per name, however it is spelled: "Layla" in a log line and
        # `layla` in an entity id are the same person to whoever reads both.
        key = slug or written.casefold()
        alias = self.alias(key)
        self._forms.setdefault(alias, (written, slug or written))
        for variant in {written.casefold(), slug}:
            if variant and (len(variant) >= MIN_WORD or allow_short):
                self._alias_of.setdefault(variant, alias)
        # The possessive a slug keeps: "Layla's iPhone" -> `laylas_iphone`.
        if slug and "_" not in slug and len(slug) >= MIN_WORD:
            possessive = slug + "s"
            self._alias_of.setdefault(possessive, self.alias(possessive))
            self._forms.setdefault(self.alias(possessive), (written + "s", possessive))

    def _add_name(self, name: Any) -> None:
        if not isinstance(name, str) or not name.strip():
            return
        name = " ".join(name.split())
        self._add_term(name, self._slugify(name))
        # Each word of a longer name, and each part of a username.
        words = re.split(r"[\s._@+-]+", name)
        if len(words) > 1:
            for word in words:
                self._add_term(word, self._slugify(word))

    # ---- applying it -------------------------------------------------------

    def _scrub_text(self, text: str) -> str:
        if self._pattern is None:
            return text
        def swap(match: re.Match) -> str:
            found = match.group(1).casefold()
            # Case folding can disagree with the pattern's own case rules
            # for a handful of letters; hide the match whatever happens.
            return self._alias_of.get(found) or self.alias(found)

        return self._pattern.sub(swap, text)

    def _reveal_text(self, text: str) -> str:
        if not self._forms or ALIAS_PREFIX not in text:
            return text
        as_id = bool(_IDENTIFIER.match(text))

        def back(match: re.Match) -> str:
            forms = self._forms.get(match.group(0))
            if forms is None:
                return match.group(0)
            return forms[1] if as_id else forms[0]

        return ALIAS_RE.sub(back, text)

    def scrub(self, node: Any) -> Any:
        """A copy of `node` with every household name replaced by its id."""
        if isinstance(node, str):
            return self._scrub_text(node)
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if (key in _DIGEST_KEYS and isinstance(value, str) and self._forms
                        and self._fold_digests):
                    out[key] = hashlib.sha256(
                        f"{value}:{self.fingerprint}".encode()).hexdigest()[:len(value) or 16]
                    continue
                out[self._scrub_text(key) if isinstance(key, str) else key] = self.scrub(value)
            return out
        if isinstance(node, (list, tuple)):
            return [self.scrub(value) for value in node]
        return node

    def reveal(self, node: Any) -> Any:
        """A copy of `node` with every id this home issued turned back into
        the name it stands for. Ids it did not issue are left alone."""
        if isinstance(node, str):
            return self._reveal_text(node)
        if isinstance(node, dict):
            return {(self._reveal_text(key) if isinstance(key, str) else key):
                    (value if key in _SECRET_KEYS else self.reveal(value))
                    for key, value in node.items()}
        if isinstance(node, (list, tuple)):
            return [self.reveal(value) for value in node]
        return node

    @property
    def size(self) -> int:
        """How many names and spellings this hides; 0 when there is nobody."""
        return len(self._forms)

    def scrub_snapshot(self, snapshot: dict) -> dict:
        """`scrub`, plus the mark that tells the manager this snapshot came
        from an agent that hides names, so it can stop hiding them itself."""
        out = self.scrub(snapshot)
        out[SNAPSHOT_KEY] = {"version": PRIVACY_VERSION}
        return out


SNAPSHOT_KEY = "privacy"
PRIVACY_VERSION = 1


def household_names(users: Iterable[dict], persons: Iterable[dict],
                    kind: Callable[[dict], str]) -> tuple[list[str], list[str]]:
    """The names to hide and the person entity ids to alias.

    `users` are shaped like `config/auth/list`, `persons` like
    `{"entity_id", "name", "user_id"}`. Dartec's own account, room panels and
    Home Assistant's accounts are not people, their names are not personal
    data, and the manager needs to read them.
    """
    names: list[str] = []
    not_people: set[str] = set()
    for user in users:
        if kind(user) in ("owner", "person"):
            names += [user.get("name") or "", user.get("username") or ""]
        else:
            not_people.add(user.get("id"))
    entity_ids: list[str] = []
    for person in persons:
        if person.get("user_id") and person.get("user_id") in not_people:
            continue
        names.append(person.get("name") or "")
        if person.get("entity_id"):
            entity_ids.append(person["entity_id"])
    return [n for n in names if n], entity_ids


# ---- Home Assistant adapter -------------------------------------------------

STORE_KEY = "dartec_ha_manager.privacy"
STORE_VERSION = 1
_DATA_SECRET = "privacy_secret"


async def _async_secret(hass) -> bytes:
    """The home's own key for the ids. Made once, kept in `.storage`, never
    sent anywhere. Losing it only means every id changes once."""
    from homeassistant.helpers.storage import Store

    from .const import DOMAIN

    bucket = hass.data.setdefault(DOMAIN, {})
    if bucket.get(_DATA_SECRET):
        return bucket[_DATA_SECRET]
    store = Store(hass, STORE_VERSION, STORE_KEY)
    data = await store.async_load() or {}
    secret_hex = data.get("secret")
    if not isinstance(secret_hex, str) or len(secret_hex) < 32:
        secret_hex = secrets.token_hex(32)
        await store.async_save({"secret": secret_hex})
    bucket[_DATA_SECRET] = bytes.fromhex(secret_hex)
    return bucket[_DATA_SECRET]


def _persons(hass) -> list[dict]:
    """Every person Home Assistant knows: the running entities, and the
    stored people behind them (a disabled person has no state)."""
    out: dict[str, dict] = {}
    for state in hass.states.async_all("person"):
        out[state.entity_id] = {"entity_id": state.entity_id,
                                "name": state.attributes.get("friendly_name") or state.name,
                                "user_id": state.attributes.get("user_id")}
    try:
        from homeassistant.helpers import entity_registry as er

        for entry in er.async_get(hass).entities.values():
            if entry.domain == "person" and entry.entity_id not in out:
                out[entry.entity_id] = {"entity_id": entry.entity_id,
                                        "name": entry.name or entry.original_name,
                                        "user_id": None}
    except Exception as err:  # noqa: BLE001 — the states are the main source
        _LOGGER.debug("person registry read failed: %s", type(err).__name__)
    data = hass.data.get("person")
    if isinstance(data, tuple) and len(data) >= 2:
        for collection in data[:2]:
            try:
                for item in collection.async_items():
                    out.setdefault(f"stored:{item.get('id')}", {
                        "entity_id": None, "name": item.get("name"),
                        "user_id": item.get("user_id")})
            except Exception:  # noqa: BLE001 — yaml or storage, best effort
                continue
    return list(out.values())


async def async_filter(hass) -> Pseudonymiser:
    """The filter for this moment: rebuilt from the current names each time,
    so a person added a minute ago is hidden in the next snapshot."""
    from homeassistant.util import slugify

    try:
        from .household import kind
    except ImportError:  # pragma: no cover — imported on its own
        from household import kind  # type: ignore[no-redef]
    from .household_ws import user_dict

    users = [user_dict(u) for u in await hass.auth.async_get_users()]
    names, entity_ids = household_names(users, _persons(hass), kind)
    return Pseudonymiser(await _async_secret(hass), names, entity_ids, slugify)
