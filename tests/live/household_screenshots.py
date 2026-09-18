"""Screenshots of "My Home" for the homeowner guide, in English and Arabic,
on a phone and on a desktop.

Point it at a Home Assistant left running by `run_live_household.py --keep`
(its owner is the live test's `livetest` user). Signs in through Home
Assistant's login flow in code and hands the page the resulting tokens, the
way the frontend stores them, so nothing is typed into a sign-in form.

    python tests/live/household_screenshots.py http://127.0.0.1:PORT docs/my-home/img

Needs Playwright with Chromium (`pip install playwright && playwright install
chromium`); nothing else in the repository does.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

USER, PASSWORD = "livetest", "live-test-password"
VIEWPORTS = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1280, "height": 800}}


def post(url: str, *, json_body=None, form=None, token=None) -> dict:
    data = json.dumps(json_body).encode() if json_body is not None else \
        urllib.parse.urlencode(form).encode()
    kind = "application/json" if json_body is not None else "application/x-www-form-urlencoded"
    headers = {"Content-Type": kind}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}


def finish_onboarding(base: str, token: str) -> None:
    """The live test only creates the owner; the frontend sends anyone to
    onboarding until the other steps are done too."""
    for step, body in (("core_config", {}), ("analytics", {}),
                       ("integration", {"client_id": f"{base}/", "redirect_uri": f"{base}/"})):
        try:
            post(f"{base}/api/onboarding/{step}", json_body=body, token=token)
        except urllib.error.HTTPError as err:
            if err.code != 403:  # that step is already done
                raise


def tokens(base: str) -> dict:
    client_id = f"{base}/"
    flow = post(f"{base}/auth/login_flow", json_body={
        "client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id})
    step = post(f"{base}/auth/login_flow/{flow['flow_id']}", json_body={
        "username": USER, "password": PASSWORD, "client_id": client_id})
    got = post(f"{base}/auth/token", form={"grant_type": "authorization_code",
                                            "code": step["result"], "client_id": client_id})
    return {**got, "hassUrl": base, "clientId": client_id,
            "expires": int(time.time() * 1000) + got["expires_in"] * 1000}


def panel(page):
    return page.locator("dartec-household-panel")


def shot(page, out: Path, name: str) -> None:
    page.wait_for_timeout(600)
    page.screenshot(path=str(out / f"{name}.png"))
    print(f"wrote {name}.png")


def main(base: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stored = tokens(base)
    finish_onboarding(base, stored["access_token"])
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for lang in ("en", "ar"):
            for form, viewport in VIEWPORTS.items():
                context = browser.new_context(viewport=viewport, device_scale_factor=1.5,
                                              is_mobile=form == "phone", has_touch=form == "phone")
                context.add_init_script(
                    f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(stored))});"
                    f"localStorage.setItem('selectedLanguage', {json.dumps(json.dumps(lang))});")
                page = context.new_page()
                page.goto(f"{base}/dartec-household")
                panel(page).locator(".card").first.wait_for(timeout=30000)
                tag = f"{lang}-{form}"
                shot(page, out, f"{tag}-list")

                panel(page).locator("[data-act=add]").click()
                sheet = panel(page).locator(".sheet")
                sheet.locator("input[name=name]").fill("Sara" if lang == "en" else "سارة")
                sheet.locator("input[name=username]").fill("sara")
                sheet.locator("[data-sact=generate]").click()
                sheet.locator("input[value=guest]").check()
                shot(page, out, f"{tag}-add")
                sheet.locator("[data-sact=close]").click()

                panel(page).locator(".card[data-act=open]").nth(1).click()
                shot(page, out, f"{tag}-person")
                panel(page).locator(".sheet [data-sact=dashboard]").click()
                shot(page, out, f"{tag}-dashboard")
                panel(page).locator(".sheet [data-sact=close]").click()

                panel(page).locator(".card[data-act=open]").nth(1).click()
                panel(page).locator(".sheet [data-sact=remove]").click()
                shot(page, out, f"{tag}-remove")
                context.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1].rstrip("/"), Path(sys.argv[2]))
