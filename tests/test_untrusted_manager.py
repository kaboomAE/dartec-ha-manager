"""The manager is not trusted: one regression test per advisory.

Each test replays the attack from its advisory against the real module and
checks it now fails, with the legitimate command beside it still working.
docs/trust-boundary.md has the rules these pin down.

Home Assistant and aiohttp are not installed in CI, so both are stubbed, as in
the rest of the suite; each test installs the stubs it needs at the time it
runs, because other files stub the same modules differently.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _stub(name, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Any:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self


def _identity(*args, **kwargs):
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]
    return lambda f: f


_stub("voluptuous", Schema=_Any, Optional=_Any, Required=_Any, All=_Any,
      Coerce=_Any, Range=_Any, Any=_Any)
_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object,
      callback=lambda f: f)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None,
      async_dispatcher_connect=lambda *a, **k: (lambda: None))
_stub("homeassistant.components")
_stub("homeassistant.components.websocket_api", websocket_command=_identity,
      require_admin=_identity, async_response=_identity,
      async_register_command=lambda *a, **k: None)

_AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(_AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg

from dartec_ha_manager import (commands, household_ws, maintenance,  # noqa: E402
                               service_policy, trust, ws_bridge)
from dartec_ha_manager import household as rules  # noqa: E402


def _load(name):
    """A private copy of a handler module. test_commissioning.py puts empty
    stand-ins for them into sys.modules, so a plain import could get one."""
    spec = importlib.util.spec_from_file_location(f"dartec_ha_manager._untrusted_{name}",
                                                  _AGENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_cmds = _load("backup_cmds")
home_cmds = _load("home_cmds")
link_cmds = _load("link_cmds")
media_cmds = _load("media_cmds")


def run(coro):
    return asyncio.run(coro)


class Unauthorized(Exception):
    def __init__(self, context=None, **kwargs):
        super().__init__("unauthorized")
        self.context = context


@pytest.fixture(autouse=True)
def ha_runtime(monkeypatch):
    """The Home Assistant modules the code under test imports while running."""
    monkeypatch.setitem(sys.modules, "homeassistant.exceptions",
                        types.SimpleNamespace(Unauthorized=Unauthorized))
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.entity_registry",
                        types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(
                            entities={})))


class FakeHass:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.fired = []
        self.bus = types.SimpleNamespace(
            async_fire=lambda event, data=None: self.fired.append((event, data)))
        self.config_entries = types.SimpleNamespace(async_entries=lambda domain=None: [])

        async def async_call(domain, service, data, blocking=False):
            self.calls.append((domain, service, data))
        self.services = types.SimpleNamespace(async_call=async_call)


@pytest.fixture
def consent(monkeypatch):
    """Set what the home has consented to: False (nothing) or True."""
    def _set(allowed: bool):
        monkeypatch.setattr(maintenance, "consent", lambda hass: (
            {"allowed": True, "source": "window"} if allowed
            else {"allowed": False, "source": None}))
    _set(False)
    return _set


def call(domain, service, **service_data):
    return {"action": "call_service", "domain": domain, "service": service,
            "service_data": service_data}


# ── GHSA-vj2g-mxcr-wx5r: comma-delimited entity targets ────────────────────


class TestCommaTargets:
    """Home Assistant splits "a,b" into two entities; the policy has to too."""

    @pytest.mark.parametrize("target", [
        "light.safe,switch.allow_dartec_support",
        "light.safe, SWITCH.Allow_Dartec_Support ",
        ["light.safe,switch.allow_dartec_support"],
        ["light.safe", " switch.allow_dartec_support"],
    ])
    def test_the_consent_switch_cannot_hide_in_a_list(self, consent, target):
        hass = FakeHass()
        result = run(commands.execute_command(
            hass, call("switch", "turn_on", entity_id=target)))
        assert result.get("refused") is True
        assert hass.calls == []

    def test_nor_inside_a_target_block(self, consent):
        hass = FakeHass()
        cmd = call("switch", "turn_on",
                   target={"entity_id": "light.safe,switch.allow_dartec_support"})
        result = run(commands.execute_command(hass, cmd))
        assert result.get("refused") is True and hass.calls == []

    def test_the_policy_sees_every_entry(self):
        own = {"switch.allow_dartec_support"}
        assert service_policy.check_own_entities(
            {"entity_id": "light.safe,switch.allow_dartec_support"}, own)

    def test_home_assistant_is_handed_the_list_that_was_checked(self, consent):
        hass = FakeHass()
        result = run(commands.execute_command(
            hass, call("light", "turn_on", entity_id="light.a, Light.B")))
        assert result["ok"] is True
        assert hass.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]

    @pytest.mark.parametrize("target", ["light.a,", "light.a,,light.b", "light", 7,
                                        ["light.a", 7], "light.a;switch.b"])
    def test_anything_but_plain_ids_is_refused(self, consent, target):
        hass = FakeHass()
        result = run(commands.execute_command(
            hass, call("light", "turn_on", entity_id=target)))
        assert result.get("refused") is True and hass.calls == []


# ── GHSA-4m4w-x5r5-hmvh / GHSA-rwhg-fxhq-m6pg: scenes and automations ──────


class TestStoredActionsNeedConsent:

    @pytest.mark.parametrize("domain,service,entity", [
        ("scene", "turn_on", "scene.night_security"),            # GHSA-4m4w
        ("automation", "turn_on", "automation.unlock_on_arrival"),  # GHSA-rwhg
        ("automation", "turn_off", "automation.alarm_when_away"),   # GHSA-rwhg
    ])
    def test_refused_without_consent(self, consent, domain, service, entity):
        hass = FakeHass()
        result = run(commands.execute_command(hass, call(domain, service, entity_id=entity)))
        assert result.get("refused") is True
        assert "maintenance window" in result["detail"]
        assert hass.calls == []

    @pytest.mark.parametrize("domain,service", [
        ("scene", "turn_on"), ("automation", "turn_on"), ("automation", "turn_off")])
    def test_allowed_with_consent(self, consent, domain, service):
        consent(True)
        hass = FakeHass()
        result = run(commands.execute_command(
            hass, call(domain, service, entity_id=f"{domain}.x")))
        assert result["ok"] is True and len(hass.calls) == 1

    @pytest.mark.parametrize("domain", ["scene", "automation"])
    def test_reloading_stays_routine(self, consent, domain):
        hass = FakeHass()
        result = run(commands.execute_command(hass, call(domain, "reload")))
        assert result["ok"] is True and hass.calls == [(domain, "reload", {})]


# ── Shared fakes for the HTTP commands ─────────────────────────────────────


class FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks
        self.read_bytes = 0

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            self.read_bytes += len(chunk)
            yield chunk

    async def read(self, n=-1):
        return b"".join(self._chunks)[:n]


class FakeResponse:
    def __init__(self, status=200, chunks=(), headers=None, content_length=None):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(list(chunks))
        self.content_length = content_length

    async def text(self):
        return b"".join(self.content._chunks).decode()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers GETs from a queue, and records every request."""

    def __init__(self, gets=(), post=None):
        self.gets = list(gets)
        self.post_response = post or FakeResponse(200, [b'{"stored_bytes": 10485760}'])
        self.requests = []
        self.posted = b""

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.gets.pop(0)

    def post(self, url, data=None, **kwargs):
        self.requests.append(("POST", url, kwargs))
        session = self

        class _Post(FakeResponse):
            async def __aenter__(inner):
                if hasattr(data, "__aiter__"):
                    async for chunk in data:
                        session.posted += chunk
                return session.post_response
        return _Post()


@pytest.fixture
def http(monkeypatch):
    """Install a fake aiohttp session; returns a setter for it."""
    holder = {}

    def _use(session):
        holder["session"] = session
        return session

    monkeypatch.setitem(sys.modules, "homeassistant.helpers.aiohttp_client",
                        types.SimpleNamespace(
                            async_get_clientsession=lambda hass: holder["session"]))
    monkeypatch.setitem(sys.modules, "aiohttp", types.SimpleNamespace(
        ClientTimeout=lambda **k: k, FormData=_FakeForm))

    async def mint(hass):
        return "refresh", "access"
    monkeypatch.setattr(ws_bridge, "mint_owner_token", mint)
    monkeypatch.setattr(backup_cmds, "mint_owner_token", mint)
    monkeypatch.setattr(ws_bridge, "base_url", lambda hass, ws=False: "http://127.0.0.1:8123")
    return _use


class _FakeForm:
    def __init__(self):
        self.fields = []

    def add_field(self, *a, **k):
        self.fields.append((a, k))


class FakeAuthHass(FakeHass):
    def __init__(self):
        super().__init__()
        self.auth = types.SimpleNamespace(async_remove_refresh_token=lambda token: None)


# Where the attacks in the advisories pointed, and the usual ways around an
# allowlist.
HOSTILE_URLS = [
    "http://127.0.0.1:8123/internal/collector",
    "https://127.0.0.1/internal",
    "https://169.254.169.254/latest/meta-data",
    "https://attacker.example/collect",
    "http://manager.dartec.ae/agent/backup-upload",           # not https
    "https://manager.dartec.ae.attacker.example/x",           # suffix
    "https://attacker.example/manager.dartec.ae",
    "https://manager.dartec.ae@attacker.example/x",           # credentials
    "https://user:pw@manager.dartec.ae/x",
    "https://manager.dartec.ae:8443/x",                       # other port
    "https://manager.dartec.ae\\@attacker.example/x",         # parser confusion
    "https://manager.dartec.ae /x",
    "//attacker.example/x",
    "file:///config/secrets.yaml",
    "",
    None,
    ["https://manager.dartec.ae/x"],
]


# ── GHSA-v9pc-vw88-p798: backup upload URL ─────────────────────────────────


class TestBackupUpload:

    def _cmd(self, **over):
        return {"action": "backup_upload", "backup_id": "a1b2c3d4",
                "upload_url": "https://manager.dartec.ae/agent/backup-upload",
                "upload_token": "tok", **over}

    @pytest.fixture
    def home(self, monkeypatch, http):
        seen = []

        async def own_ws(hass, payload, timeout=60):
            seen.append(payload)
            return {"success": True, "result": {"backup": {
                "name": "nightly", "agents": {"backup.local": {}}}}}
        monkeypatch.setattr(backup_cmds, "call_own_ws", own_ws)
        return seen

    @pytest.mark.parametrize("url", HOSTILE_URLS)
    def test_a_hostile_url_is_refused_before_anything_is_read(self, home, http, url):
        session = http(FakeSession())
        result = run(backup_cmds.backup_upload(FakeAuthHass(), self._cmd(upload_url=url)))
        assert result["ok"] is False
        assert home == [] and session.requests == []

    @pytest.mark.parametrize("backup_id", ["../../config/core/check_config",
                                           "a1b2/../../states", "..%2F..%2Fstates",
                                           "a1b2c3d4?x=1", 12])
    def test_a_backup_id_cannot_leave_the_download_path(self, home, http, backup_id):
        session = http(FakeSession())
        result = run(backup_cmds.backup_upload(FakeAuthHass(), self._cmd(backup_id=backup_id)))
        assert result.get("refused") is True
        assert home == [] and session.requests == []

    def test_an_agent_id_cannot_add_to_the_query(self, home, http):
        session = http(FakeSession())
        result = run(backup_cmds.backup_upload(
            FakeAuthHass(), self._cmd(agent_id="backup.local&x=../../")))
        assert result.get("refused") is True and session.requests == []

    @pytest.mark.parametrize("max_mb", [4097, 10**9, 0, -1, "2048", True, 1.5])
    def test_the_size_limit_is_bounded(self, home, http, max_mb):
        http(FakeSession())
        result = run(backup_cmds.backup_upload(FakeAuthHass(), self._cmd(max_mb=max_mb)))
        assert result.get("refused") is True and home == []

    def test_the_manager_still_gets_its_copy_and_no_redirect_is_followed(self, home, http):
        session = http(FakeSession(gets=[FakeResponse(200, [b"x" * 1024] * 3)]))
        result = run(backup_cmds.backup_upload(FakeAuthHass(), self._cmd()))
        assert result["ok"] is True, result
        method, url, kwargs = session.requests[-1]
        assert (method, url) == ("POST", "https://manager.dartec.ae/agent/backup-upload")
        assert kwargs["allow_redirects"] is False
        assert session.posted == b"x" * 3072
        # The id went into the path only after it was checked; the agent as
        # an encoded query parameter.
        _, download, get_kwargs = session.requests[0]
        assert download.endswith("/api/backup/download/a1b2c3d4")
        assert get_kwargs["params"] == {"agent_id": "backup.local"}

    def test_the_limit_holds_on_what_is_actually_sent(self, home, http):
        chunk = b"x" * (512 * 1024)
        session = http(FakeSession(gets=[FakeResponse(200, [chunk] * 4)]))
        result = run(backup_cmds.backup_upload(FakeAuthHass(), self._cmd(max_mb=1)))
        assert result["ok"] is False and "limit" in result["detail"]
        assert len(session.posted) <= 1024 * 1024


# ── GHSA-mqpf-x42g-g28w / GHSA-88r5-5vxg-53px: media download ──────────────


BODY = b"athan-audio"


class TestMediaUpload:

    def _cmd(self, **over):
        return {"action": "media_upload", "filename": "athan.mp3",
                "download_url": "https://manager.dartec.ae/agent/media/athan",
                "download_token": "tok", "sha256": hashlib.sha256(BODY).hexdigest(),
                **over}

    @pytest.fixture
    def home(self, monkeypatch, http):
        async def own_ws(hass, payload, timeout=60):
            return {"success": False}
        monkeypatch.setattr(ws_bridge, "call_own_ws", own_ws)

    @pytest.mark.parametrize("url", HOSTILE_URLS)
    def test_a_hostile_url_is_never_fetched(self, home, http, url):
        session = http(FakeSession())
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd(download_url=url)))
        assert result["ok"] is False
        assert session.requests == []

    @pytest.mark.parametrize("location", [
        "http://169.254.169.254/latest/meta-data",
        "https://attacker.example/x",
        "//attacker.example/x",
        "http://manager.dartec.ae/agent/media/athan",
    ])
    def test_a_redirect_away_from_dartec_is_refused(self, home, http, location):
        session = http(FakeSession(gets=[FakeResponse(302, headers={"Location": location})]))
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd()))
        assert result.get("refused") is True
        assert [r[0] for r in session.requests] == ["GET"]
        assert session.requests[0][2]["allow_redirects"] is False

    def test_a_redirect_within_dartec_is_followed(self, home, http):
        session = http(FakeSession(gets=[
            FakeResponse(302, headers={"Location": "/agent/media/athan-v2"}),
            FakeResponse(200, [BODY])]))
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd()))
        assert result["ok"] is True, result
        assert session.requests[1][1] == "https://manager.dartec.ae/agent/media/athan-v2"

    def test_endless_redirects_stop(self, home, http):
        loop = FakeResponse(302, headers={"Location": "/agent/media/athan"})
        session = http(FakeSession(gets=[loop] * 10))
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd()))
        assert result["ok"] is False and "redirects" in result["detail"]
        assert len(session.requests) == trust.MAX_REDIRECTS + 1

    @pytest.mark.parametrize("max_mb", [21, 100, 1000, 10**9, 0, -5, "100", True, 2.5])
    def test_the_manager_cannot_raise_the_size_cap(self, home, http, max_mb):
        session = http(FakeSession())
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd(max_mb=max_mb)))
        assert result.get("refused") is True and session.requests == []

    def test_an_oversized_stream_stops_at_the_cap(self, home, http):
        chunk = b"x" * media_cmds.CHUNK
        response = FakeResponse(200, [chunk] * 200)                 # 50 MiB
        http(FakeSession(gets=[response]))
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd()))
        assert result["ok"] is False and "larger than 20 MB" in result["detail"]
        assert response.content.read_bytes <= 20 * 1024 * 1024 + media_cmds.CHUNK

    def test_a_declared_oversize_is_refused_before_reading(self, home, http):
        response = FakeResponse(200, [BODY], content_length=100 * 1024 * 1024)
        http(FakeSession(gets=[response]))
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd()))
        assert result["ok"] is False and response.content.read_bytes == 0

    def test_a_smaller_cap_is_honoured(self, home, http):
        http(FakeSession(gets=[FakeResponse(200, [b"x" * 2 * 1024 * 1024])]))
        result = run(media_cmds.media_upload(FakeAuthHass(), self._cmd(max_mb=1)))
        assert result["ok"] is False and "larger than 1 MB" in result["detail"]


# ── GHSA-qj62-wg85-79cv: automation id path traversal ─────────────────────


class TestAutomationId:

    @pytest.fixture
    def rest(self, monkeypatch):
        seen = []

        async def call_own_rest(hass, method, path, json_body=None, timeout=60):
            seen.append((method, path))
            return {"ok": True, "status": 200, "body": {"result": "ok"}}
        monkeypatch.setattr(ws_bridge, "call_own_rest", call_own_rest)
        return seen

    def _config(self, automation_id):
        return {"id": automation_id, "alias": "Porch light",
                "triggers": [{"trigger": "sun", "event": "sunset"}],
                "actions": [{"action": "light.turn_on",
                             "target": {"entity_id": "light.porch"}}]}

    @pytest.mark.parametrize("automation_id", [
        "../../../services/homeassistant/stop",
        "..%2F..%2F..%2Fservices%2Fhomeassistant%2Fstop",
        "x/../../../../core/check_config",
        "..", ".", "a.b", "a b", "a\\b", "x" * 65, ["x"], True,
    ])
    def test_a_traversing_id_never_reaches_a_path(self, rest, automation_id):
        result = run(home_cmds.automation_create(
            FakeHass(), {"config": self._config(automation_id)}))
        assert result.get("refused") is True
        assert rest == []

    @pytest.mark.parametrize("automation_id,expected", [
        ("1726312345678", "1726312345678"), (1726312345678, "1726312345678"),
        ("dartec_athan_fajr", "dartec_athan_fajr"), ("porch-light", "porch-light")])
    def test_home_assistant_style_ids_still_work(self, rest, automation_id, expected):
        result = run(home_cmds.automation_create(
            FakeHass(), {"config": self._config(automation_id)}))
        assert result["ok"] is True
        assert rest == [("POST", f"/api/config/automation/config/{expected}")]

    def test_no_id_still_gets_one_of_ours(self, rest):
        config = self._config(None)
        del config["id"]
        result = run(home_cmds.automation_create(FakeHass(), {"config": config}))
        assert result["ok"] is True and rest[0][1].startswith(
            "/api/config/automation/config/dartec_")


class TestAddonSlug:
    """The same class of bug in the add-on commands: the slug is a path."""

    @pytest.mark.parametrize("slug", ["../core", "../host", "x/../../core",
                                      "..%2Fcore", "core_ssh/../../supervisor"])
    def test_a_traversing_slug_never_reaches_the_supervisor(self, consent, monkeypatch,
                                                            http, slug):
        consent(True)
        monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
        session = http(FakeSession())
        result = run(commands.execute_command(
            FakeHass(), {"action": "addon_stop", "addon_slug": slug}))
        assert result.get("refused") is True and session.requests == []


# ── GHSA-cvj4-75mr-7858: the last administrator ────────────────────────────


class FakeUser:
    def __init__(self, user_id, admin=True):
        self.id = user_id
        self.name = user_id.title()
        self.is_owner = False
        self.is_active = True
        self.local_only = False
        self.system_generated = False
        self.credentials = [types.SimpleNamespace(auth_provider_type="homeassistant",
                                                  data={"username": user_id})]
        self.groups = [types.SimpleNamespace(
            id=rules.GROUP_ADMIN if admin else rules.GROUP_USER)]


class SlowAuth:
    """Home Assistant's auth, answering after a pause, which is when two
    requests can overlap."""

    def __init__(self, users):
        self.users = {u.id: u for u in users}

    async def async_get_users(self):
        await asyncio.sleep(0)
        return list(self.users.values())

    async def async_get_user(self, user_id):
        await asyncio.sleep(0)
        return self.users.get(user_id)

    async def async_remove_user(self, user):
        await asyncio.sleep(0)
        self.users.pop(user.id, None)

    async def async_update_user(self, user, group_ids=None, **kwargs):
        await asyncio.sleep(0)
        if group_ids is not None:
            user.groups = [types.SimpleNamespace(id=g) for g in group_ids]

    def async_get_refresh_token(self, token_id):
        return None


class FakeConnection:
    def __init__(self, user):
        self.user = user
        self.refresh_token_id = None
        self.errors = []
        self.results = []

    def send_error(self, msg_id, code, message):
        self.errors.append(code)

    def send_result(self, msg_id, result):
        self.results.append(result)


class TestLastAdministrator:

    @pytest.fixture
    def home(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "homeassistant.config_entries",
                            types.SimpleNamespace(ConfigEntryState=types.SimpleNamespace(
                                LOADED="loaded")))

        async def quiet(*a, **k):
            return None
        monkeypatch.setattr(household_ws, "_record", quiet)
        monkeypatch.setattr(household_ws, "_answer", quiet)

        a, b = FakeUser("alia"), FakeUser("badr")
        hass = FakeHass()
        hass.auth = SlowAuth([a, b])
        hass.config_entries = types.SimpleNamespace(
            async_entries=lambda domain=None: [types.SimpleNamespace(state="loaded")])
        store = types.SimpleNamespace(guests=set(), activity=[])
        hass.data = {household_ws.DOMAIN: {household_ws._DATA: store}}
        return hass, a, b

    @staticmethod
    def _admins(hass):
        return [u for u in hass.auth.users.values()
                if u.is_active and any(g.id == rules.GROUP_ADMIN for g in u.groups)]

    def test_two_admins_removing_each_other_leave_one(self, home):
        hass, a, b = home
        ca, cb = FakeConnection(a), FakeConnection(b)

        async def race():
            await asyncio.gather(
                household_ws.ws_remove(hass, ca, {"id": 1, "user_id": b.id}),
                household_ws.ws_remove(hass, cb, {"id": 1, "user_id": a.id}))
        run(race())
        assert len(self._admins(hass)) == 1
        assert sorted(ca.errors + cb.errors) in (["actor_not_admin"], ["last_admin"])

    def test_two_admins_demoting_each_other_leave_one(self, home):
        hass, a, b = home
        ca, cb = FakeConnection(a), FakeConnection(b)

        async def race():
            await asyncio.gather(
                household_ws.ws_update(hass, ca, {"id": 1, "user_id": b.id,
                                                  "role": rules.ROLE_FAMILY}),
                household_ws.ws_update(hass, cb, {"id": 1, "user_id": a.id,
                                                  "role": rules.ROLE_FAMILY}))
        run(race())
        assert len(self._admins(hass)) == 1
        assert len(ca.errors + cb.errors) == 1

    def test_one_removal_on_its_own_still_works(self, home):
        hass, a, b = home
        conn = FakeConnection(a)
        run(household_ws.ws_remove(hass, conn, {"id": 1, "user_id": b.id}))
        assert conn.errors == [] and list(hass.auth.users) == [a.id]


# ── GHSA-qx58-m2pf-8388: who can grant consent ─────────────────────────────


class ConsentHass(FakeHass):
    def __init__(self, users):
        super().__init__()
        self.registered = {}
        self.data = {maintenance.DOMAIN: {maintenance._STATE_KEY: None}}
        users = {u.id: u for u in users}

        async def get_user(user_id):
            return users.get(user_id)
        self.auth = types.SimpleNamespace(async_get_user=get_user)

        def register(domain, service, handler, schema=None):
            self.registered[service] = handler
        self.services = types.SimpleNamespace(async_register=register)


def _person(user_id, admin):
    return types.SimpleNamespace(id=user_id, is_admin=admin, is_active=True)


def _ctx(user_id):
    return types.SimpleNamespace(user_id=user_id)


class TestOnlyAnAdministratorGrantsConsent:

    @pytest.fixture
    def hass(self, monkeypatch):
        opened = []
        monkeypatch.setattr(maintenance, "open_window",
                            lambda hass, minutes=60: opened.append(minutes))
        monkeypatch.setattr(maintenance, "close_window", lambda hass: opened.append("closed"))
        monkeypatch.setattr(maintenance, "complete_commissioning",
                            lambda hass, by: opened.append("completed"))
        monkeypatch.setattr(maintenance, "logbook", lambda hass, message: None)
        hass = ConsentHass([_person("kid", False), _person("parent", True),
                            types.SimpleNamespace(id="gone", is_admin=True,
                                                  is_active=False)])
        run(maintenance.async_register_services(hass))
        hass.opened = opened
        return hass

    def _service(self, hass, name, user_id, **data):
        handler = hass.registered[name]
        return run(handler(types.SimpleNamespace(context=_ctx(user_id), data=data)))

    @pytest.mark.parametrize("user_id", ["kid", "gone", "unknown"])
    def test_a_regular_user_cannot_allow_maintenance(self, hass, user_id):
        with pytest.raises(Unauthorized):
            self._service(hass, maintenance.SERVICE_ALLOW, user_id, minutes=480)
        assert hass.opened == []

    def test_an_administrator_can(self, hass):
        self._service(hass, maintenance.SERVICE_ALLOW, "parent", minutes=30)
        assert hass.opened == [30]

    def test_home_assistant_itself_can(self, hass):
        """An automation's call carries no user; an administrator wrote it."""
        self._service(hass, maintenance.SERVICE_ALLOW, None, minutes=30)
        assert hass.opened == [30]

    def test_anyone_may_take_access_away(self, hass):
        self._service(hass, maintenance.SERVICE_END, "kid")
        self._service(hass, maintenance.SERVICE_COMPLETE, "kid")
        assert hass.opened == ["closed", "completed"]

    def test_the_switch_asks_the_same_question(self, hass, monkeypatch):
        """switch.allow_dartec_support and the approved-updates switch call
        require_admin with the entity's call context before granting."""
        source = (_AGENT / "switch.py").read_text(encoding="utf-8")
        for method in source.split("async def async_turn_on(self, **kwargs) -> None:")[1:]:
            body = method.split("async def ")[0]
            assert "await maintenance.require_admin(self.hass, self._context)" in body
        with pytest.raises(Unauthorized):
            run(maintenance.require_admin(hass, _ctx("kid")))
        run(maintenance.require_admin(hass, _ctx("parent")))


# ── GHSA-hrm5-cvxj-cc7w: the Link login server ─────────────────────────────


class TestLinkLoginServer:

    @pytest.fixture
    def supervisor(self, monkeypatch, consent):
        consent(True)
        monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
        calls = []

        async def fake(hass, method, path, json_body=None, timeout=180):
            calls.append((method, path, json_body))
            return {"status": 200, "body": {"data": {"state": "started", "version": "1"}}}

        async def addon(hass, **kwargs):
            return {"slug": "abcd1234_dartec_link", "installed": True}

        async def no_sleep(seconds):
            return None
        monkeypatch.setattr(link_cmds, "_supervisor", fake)
        monkeypatch.setattr(link_cmds, "_find_addon", addon)
        monkeypatch.setattr(link_cmds.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(maintenance, "logbook", lambda hass, message: None)
        return calls

    def _cmd(self, login_server):
        return {"action": "link_setup", "auth_key": "key", "node_name": "Villa",
                "login_server": login_server}

    @pytest.mark.parametrize("login_server", [
        "https://attacker.invalid", "http://headscale.dartec.ae",
        "https://headscale.dartec.ae.attacker.invalid",
        "https://headscale.dartec.ae@attacker.invalid",
        "https://headscale.dartec.ae:8443", "https://headscale.dartec.ae/extra",
        "https://headscale.dartec.ae/?next=x", "http://169.254.169.254:8080",
        "file:///etc/passwd", "//attacker.invalid/path", "https://manager.dartec.ae",
        ["https://headscale.dartec.ae"],
    ])
    def test_only_dartec_headscale_is_joined(self, supervisor, login_server):
        result = run(link_cmds.link_setup(FakeHass(), self._cmd(login_server)))
        assert result.get("refused") is True
        assert supervisor == []

    @pytest.mark.parametrize("login_server", [
        "https://headscale.dartec.ae", "https://headscale.dartec.ae/",
        "https://Headscale.Dartec.AE:443"])
    def test_the_add_on_is_given_the_pinned_origin(self, supervisor, login_server):
        result = run(link_cmds.link_setup(FakeHass(), self._cmd(login_server)))
        assert result["ok"] is True, result
        options = next(body["options"] for method, path, body in supervisor
                       if path.endswith("/options"))
        assert options["login_server"] == "https://headscale.dartec.ae"

    def test_consent_is_asked_again_before_enrolling(self, supervisor, monkeypatch):
        """The install can take minutes; a home that says no meanwhile is obeyed."""
        calls = 0

        def consent_that_ends(hass):
            nonlocal calls
            calls += 1
            return {"allowed": False, "source": None}
        monkeypatch.setattr(maintenance, "consent", consent_that_ends)
        result = run(link_cmds.link_setup(FakeHass(), self._cmd("https://headscale.dartec.ae")))
        assert result.get("code") == "consent" and calls == 1
        assert not any(path.endswith(("/options", "/start", "/restart"))
                       for _, path, _ in supervisor)


# ── The shared checks ───────────────────────────────────────────────────────


class TestTrust:

    @pytest.mark.parametrize("url", HOSTILE_URLS)
    def test_hostile_urls(self, url):
        assert trust.check_url(url, trust.MANAGER_HOSTS)

    @pytest.mark.parametrize("url", [
        "https://manager.dartec.ae/agent/backup-upload",
        "https://MANAGER.dartec.ae./agent/media/x?token=1",
        "https://manager.dartec.ae:443/agent/media/x"])
    def test_the_manager_itself(self, url):
        assert trust.check_url(url, trust.MANAGER_HOSTS) is None

    def test_the_allowlists_are_what_production_uses(self):
        assert trust.MANAGER_HOSTS == {"manager.dartec.ae"}
        assert trust.LINK_HOSTS == {"headscale.dartec.ae"}

    @pytest.mark.parametrize("value,expected", [
        (None, 20), (5, 5), (20, 20), (21, None), (0, None), (True, None), ("5", None)])
    def test_bounded_mb(self, value, expected):
        assert trust.bounded_mb(value, 20, 20) == expected

    def test_trust_imports_nothing_from_home_assistant(self):
        source = (_AGENT / "trust.py").read_text(encoding="utf-8")
        assert "import homeassistant" not in source
        assert "from homeassistant" not in source
