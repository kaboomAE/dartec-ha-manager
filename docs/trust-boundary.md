# The trust boundary

The agent runs inside a customer's home and takes commands from Dartec's
manager. **It treats every command as if the manager had been taken over**,
because the one thing the agent exists to guarantee is that a compromised
Dartec cloud is not a compromised home. A rule that holds only while the
manager behaves protects nothing.

So a command can *ask*; the home decides. Every new command, and every new
field on an old one, follows the rules below. Code review checks them.

## The rules

1. **Places are fixed in the agent, never taken from a command.** A URL the
   agent is told to upload to, download from or log in to is checked against
   a fixed allowlist in [`trust.py`](../custom_components/dartec_ha_manager/trust.py)
   before any connection is made: `https` only, the exact host
   (`manager.dartec.ae` for backups and media, `headscale.dartec.ae` for Dartec
   Link), the default port, no credentials in the URL. A redirect is followed
   only by hand, each hop checked the same way, at most three times; a
   streamed upload follows none. What reaches an add-on is rebuilt from the
   allowlist, not copied from the command. Adding a host is a release of the
   agent, never a setting the manager can send.
2. **Every size is bounded by the agent.** A command may ask for a lower limit
   than the agent's own ceiling, never a higher one, and the limit is enforced
   on the bytes actually read or sent, before they are kept, not on what the
   other side declares. `trust.bounded_mb` refuses anything but a whole number
   in range.
3. **Every id is checked against a strict pattern before it touches a path.**
   An id that ends up in a URL or a file path (an automation id, an add-on
   slug, a backup id) must match its own pattern in `trust.py`, none of which
   allow `/`, `\`, `.` or `%`. Query values go in `params=`, encoded, never
   into the string by hand.
4. **Entity targets are parsed as a list and authorised item by item.** An
   `entity_id` is read the way Home Assistant reads it (a string is a
   comma-separated list), each entry must be a plain `domain.object_id`, and
   the call that runs is built from that checked list, so Home Assistant acts
   on exactly what was authorised (`service_policy.normalise_service_data`).
   Targeting by area, device, floor or label is refused.
5. **Anything that runs stored actions needs the same consent as the actions.**
   A scene applies stored states and an automation runs stored service calls,
   so applying a scene or switching an automation on or off is *sensitive*,
   like the lock or alarm call it could stand in for. Reloading them is
   routine.
6. **Consent is checked at the moment of the action.** It is read from the home
   when the command arrives, never from the command. A command that spends
   minutes installing before it changes anything (Dartec Link) asks again
   right before the change (`maintenance.consent_ended`).
7. **Only a Home Assistant administrator can grant consent.** The
   `allow_maintenance` service, the *Allow Dartec support* switch and turning
   *approved updates* back on all call `maintenance.require_admin`. Calls Home
   Assistant makes itself (an automation an administrator wrote) carry no user
   and are allowed. Taking access away is open to anyone signed in. The
   options flow, where standing consent and offsite backups are set, is
   administrator-only in Home Assistant already.
8. **Invariants over the household are enforced atomically.** "Someone must
   still be able to manage the home" is checked and applied under one lock
   (`household_ws._mutation_lock`), against the users as they are at that
   moment, with the person making the change re-read inside the lock.
9. **The default is to refuse, and to say so in the home's own logbook.** An
   unknown action, service or field is refused, and so is anything malformed,
   rather than repaired.
10. **A capability that cannot be made safe is retired, not gated.** The
    Cloudflare tunnel took a token that decided whose account the home was
    published through, and nothing on the home could check it; Dartec Link
    replaced it, so `tunnel_setup` is in `service_policy.RETIRED_ACTIONS` and
    refused before consent is read.

## Adding a command

Before merging, answer each of these in the handler's docstring or in
`service_policy.py`:

- Does any field become a URL, a host or a login server? Use `trust.check_url`
  with a fixed host set, and handle redirects by hand.
- Does any field become part of a path or a query? Give it a pattern in
  `trust.py`, or pass it as an encoded parameter.
- Does any field set a size, a count or a duration? Bound it in the agent.
- Can it change what happens in the house, now or later (stored states,
  stored calls, code, accounts, network reachability)? Then it is sensitive,
  and if it runs for a while before making its change, it asks for consent
  again at that point.
- Does it change who may do what in the home? Only an administrator may grant,
  and the check runs where the change is made.

Each rule has a regression test in
[`tests/test_untrusted_manager.py`](../tests/test_untrusted_manager.py) that
plays the attack it prevents.
