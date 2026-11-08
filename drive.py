#!/usr/bin/env python3
"""Scripted Mobile Safari journey on a booted iOS Simulator.

Screenshots come from `xcrun simctl io` (full device, includes status bar / Dynamic Island).
Nothing sensitive is printed: every log line is passed through redact().
A missing element is logged and the journey continues.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.options import ArgOptions

OUT = os.environ.get("OUT", "out")
UDID = os.environ.get("UDID", "booted")
DRIVER = os.environ.get("DRIVER", "appium")
SERVER = os.environ.get("SERVER_URL", "http://127.0.0.1:4723")
DEVICE = os.environ.get("DEVICE_NAME", "iPhone 17 Pro")
PLATFORM_VERSION = os.environ.get("PLATFORM_VERSION", "")
APP_URL = os.environ.get("APP_URL", "").strip()
USER, PASS = os.environ.get("APP_USER", ""), os.environ.get("APP_PASS", "")
ADMIN_USER, ADMIN_PASS = os.environ.get("APP_ADMIN_USER", ""), os.environ.get("APP_ADMIN_PASS", "")

os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- redaction / log
_host = urlparse(APP_URL).netloc
SECRETS = [s for s in {APP_URL, _host, _host.split(".")[0] if _host else "", USER, PASS, ADMIN_USER, ADMIN_PASS}
           if s and len(s) >= 3]
SECRETS.sort(key=len, reverse=True)


def redact(s):
    s = str(s)
    for sec in SECRETS:
        s = s.replace(sec, "***")
    return re.sub(r"https?://[^\s'\"]+trycloudflare\.com[^\s'\"]*", "***", s)


def log(*a):
    print(redact(" ".join(str(x) for x in a)), flush=True)


class Missing(Exception):
    pass


# ---------------------------------------------------------------- results / screenshots
results = []
counter = [0]


def save_results():
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def snap(label):
    """Full-device screenshot. Returns file name or None."""
    counter[0] += 1
    name = f"{counter[0]:02d}-{slug(label)}.png"
    r = subprocess.run(["xcrun", "simctl", "io", UDID, "screenshot", os.path.join(OUT, name)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("screenshot failed:", r.stderr.strip()[:200])
        counter[0] -= 1
        return None
    return name


def step(name, fn, shot=True, settle=1.0):
    status, note = "ok", ""
    try:
        r = fn()
        if isinstance(r, str):
            note = r
    except Missing as e:
        status, note = "missing element", str(e)
    except Exception as e:  # keep going
        status, note = "error", f"{type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}"
    time.sleep(settle)
    f = snap(name) if shot else None
    results.append({"n": len(results) + 1, "name": name, "status": status, "shot": f, "note": redact(note)})
    save_results()
    log(f"[{len(results):02d}] {name}: {status} {redact(note)}")


def skip(name, why):
    results.append({"n": len(results) + 1, "name": name, "status": "skipped", "shot": None, "note": why})
    save_results()
    log(f"[{len(results):02d}] {name}: skipped ({why})")


# ---------------------------------------------------------------- driver
class Opts(ArgOptions):
    @property
    def default_capabilities(self):
        return {}

    def to_capabilities(self):
        return self._caps


def make_driver():
    o = Opts()
    o.set_capability("browserName", "Safari")
    o.set_capability("platformName", "iOS")
    if DRIVER == "appium":
        o.set_capability("appium:automationName", "XCUITest")
        o.set_capability("appium:udid", UDID)
        o.set_capability("appium:deviceName", DEVICE)
        if PLATFORM_VERSION:
            o.set_capability("appium:platformVersion", PLATFORM_VERSION)
        o.set_capability("appium:noReset", True)
        o.set_capability("appium:wdaLaunchTimeout", 300000)
        o.set_capability("appium:wdaConnectionTimeout", 300000)
        o.set_capability("appium:newCommandTimeout", 600)
        o.set_capability("appium:safariIgnoreFraudWarning", True)
        # A fresh runner launches Safari slowly; wait longer for its web view and start on a blank page.
        o.set_capability("appium:webviewConnectTimeout", 90000)
        o.set_capability("appium:webviewConnectRetries", 20)
        o.set_capability("appium:safariInitialUrl", "about:blank")
    else:
        o.set_capability("safari:useSimulator", True)
        o.set_capability("safari:deviceType", "iPhone")
        o.set_capability("safari:deviceName", DEVICE)
        if PLATFORM_VERSION:
            o.set_capability("safari:platformVersion", PLATFORM_VERSION)
    return webdriver.Remote(command_executor=SERVER, options=o)


driver = None

# ---------------------------------------------------------------- element helpers
_LOW = "translate({},'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
_KINDS = ("self::button or self::a or @role='button' or @role='tab' or @role='menuitem' "
          "or @role='option' or @role='radio' or @role='switch' or @role='link' or self::label or self::summary")


def _lit(s):
    return "'" + s.replace("'", "") + "'"


def x_aria(sub):
    return f"//*[@aria-label and contains({_LOW.format('@aria-label')}, {_lit(sub.lower())})]"


def x_text(sub):
    return f"//*[({_KINDS}) and contains({_LOW.format('normalize-space(.)')}, {_lit(sub.lower())})]"


def candidates(kind, sub):
    xp = x_aria(sub) if kind == "aria" else x_text(sub)
    els = []
    for e in driver.find_elements("xpath", xp):
        try:
            if e.is_displayed():
                els.append(e)
        except Exception:
            pass
    # drop ancestors that contain another candidate: keep the deepest
    keep = []
    for e in els:
        try:
            if not any(o is not e and driver.execute_script("return arguments[0].contains(arguments[1])", e, o)
                       for o in els):
                keep.append(e)
        except Exception:
            keep.append(e)
    return keep


def find(needles, timeout=6):
    """needles: list of (kind, substring). Returns first displayed element or None."""
    end = time.time() + timeout
    while True:
        for kind, sub in needles:
            c = candidates(kind, sub)
            if c:
                return c[0]
        if time.time() >= end:
            return None
        time.sleep(0.5)


def tap(el):
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click()", el)


def need(needles, timeout=6, label=None):
    el = find(needles, timeout)
    if el is None:
        raise Missing(label or "/".join(f"{k}:{s}" for k, s in needles))
    return el


def click(needles, timeout=6, label=None):
    tap(need(needles, timeout, label))


def AT(s):  # shorthand: both aria and text match
    return [("aria", s), ("text", s)]


def tap_xy(x, y):
    a = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "finger"))
    a.pointer_action.move_to_location(x, y)
    a.pointer_action.pointer_down()
    a.pointer_action.pause(0.08)
    a.pointer_action.pointer_up()
    a.perform()


def tap_middle():
    w, h = driver.execute_script("return [window.innerWidth, window.innerHeight]")
    tap_xy(int(w / 2), int(h / 2))
    time.sleep(1)


def find_book(pattern_js, exclude_js=None):
    """First displayed link/button/card whose text matches the JS regex."""
    js = """
    const re = new RegExp(arguments[0]); const ex = arguments[1] ? new RegExp(arguments[1]) : null;
    const nav = /log ?out|sign ?out|menu|theme|settings|search|upload|import|add|filter|sort|account|help|home/i;
    const sel = 'a,button,[role=button],[role=link],article,li,[class*=book i],[class*=card i]';
    for (const e of document.querySelectorAll(sel)) {
      const t = (e.innerText || '').trim(); const r = e.getBoundingClientRect();
      if (!t || r.width < 40 || r.height < 40 || r.bottom < 0) continue;
      if (!re.test(t) || (ex && ex.test(t))) continue;
      if (nav.test(e.getAttribute('aria-label') || '') && t.length < 12) continue;
      return e;
    }
    return null;"""
    return driver.execute_script(js, pattern_js, exclude_js)


def open_book(pattern_js, label, exclude_js=None):
    ensure_shelf()
    end = time.time() + 8
    el = None
    while time.time() < end and el is None:
        el = find_book(pattern_js, exclude_js)
        if el is None:
            time.sleep(0.5)
    if el is None:
        raise Missing(label)
    tap(el)
    time.sleep(3)


def on_login_page():
    return bool(driver.find_elements("css selector", "input[type=password]"))


def ensure_shelf():
    if on_login_page():
        return
    b = find([("aria", "back to library"), ("aria", "back to shelf"), ("aria", "library"), ("aria", "close book")], 1.5)
    if b is not None:
        tap(b)
        time.sleep(2)
        return
    if driver.execute_script("return !!document.querySelector('[class*=reader i],[data-reader]')"):
        driver.get(APP_URL)
        time.sleep(3)


def go_home():
    driver.get(APP_URL)
    time.sleep(3)


def login(user, pw):
    if not on_login_page():
        return "already signed in"
    u = None
    for css in ("input[type=email]", "input[autocomplete=username]", "input[name*=user i]",
                "input[name*=email i]", "input[type=text]"):
        for e in driver.find_elements("css selector", css):
            if e.is_displayed():
                u = e
                break
        if u:
            break
    if u is None:
        raise Missing("username field")
    u.click()
    u.clear()
    u.send_keys(user)
    p = next((e for e in driver.find_elements("css selector", "input[type=password]") if e.is_displayed()), None)
    if p is None:
        raise Missing("password field")
    p.click()
    p.clear()
    p.send_keys(pw)
    b = find([("text", "log in"), ("text", "sign in"), ("text", "login")], 2)
    if b is None:
        try:
            b = driver.find_element("css selector", "button[type=submit]")
        except Exception:
            b = None
    if b is not None:
        tap(b)
    else:
        p.send_keys(Keys.ENTER)
    time.sleep(4)
    if on_login_page():
        raise Missing("still on login page after submit (wrong credentials or button not found)")


def logout():
    for needles in (AT("log out"), AT("sign out"), AT("logout")):
        el = find(needles, 1.5)
        if el is not None:
            tap(el)
            time.sleep(3)
            if on_login_page():
                return
    for menu in (AT("menu"), AT("account"), AT("settings"), AT("profile")):
        m = find(menu, 1)
        if m is None:
            continue
        tap(m)
        time.sleep(1)
        el = find(AT("log out") + AT("sign out") + AT("logout"), 2)
        if el is not None:
            tap(el)
            time.sleep(3)
            if on_login_page():
                return
    # fallback: wipe web storage and cookies
    driver.execute_script("try{localStorage.clear();sessionStorage.clear()}catch(e){}")
    driver.delete_all_cookies()
    driver.get(APP_URL)
    time.sleep(3)
    if not on_login_page():
        raise Missing("log out control (storage wipe did not log out)")
    return "logged out via storage wipe (no logout control found)"


def close_sheet():
    b = find([("aria", "back"), ("aria", "close"), ("text", "back"), ("text", "cancel"), ("text", "close")], 2)
    if b is not None:
        tap(b)
    else:
        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    time.sleep(1)


def open_listen_sheet():
    click([("aria", "listening settings")], 6, "button aria-label~'Listening settings'")


def set_orientation(value):
    driver.command_executor._commands["setOrientationX"] = ("POST", "/session/$sessionId/orientation")
    driver.execute("setOrientationX", {"orientation": value})


# ---------------------------------------------------------------- journey
def start_screen():
    t = threading.Thread(target=lambda: driver.get(APP_URL), daemon=True)
    t.start()
    time.sleep(1.0)
    f = snap("start-first-second")
    t.join(timeout=60)
    return f"first-second shot: {f}"


def theme_to(name):
    if find([("aria", "reading theme")], 1) is None:
        open_book("[\\u0600-\\u06FF]", "Arabic book (for theme)")
        tap_middle()
    click([("aria", "reading theme")], 5, "button aria-label~'Reading theme'")
    time.sleep(1)
    opt = find([("text", name), ("aria", name)], 2)
    if opt is not None:
        tap(opt)
        time.sleep(1)
        note = ""
    else:
        note = f"no '{name}' option after tapping theme button (may cycle instead)"
    tap_middle()  # show bars
    return note


def main():
    global driver
    if not APP_URL:
        log("APP_URL secret is empty; nothing to do")
        skip("all", "APP_URL missing")
        return 1
    log("creating session via", DRIVER)
    driver = make_driver()
    try:
        driver.set_page_load_timeout(60)
    except Exception:
        pass
    try:
        step("a1 start screen first second", start_screen, shot=False)
        step("a2 login page after load", lambda: time.sleep(3), settle=0)
        step("b login user", lambda: login(USER, PASS), settle=1)

        def c():
            open_book("[\\u0600-\\u06FF]", "Arabic book")
            tap_middle()
        step("c arabic reader bars shown", c)

        step("d1 listen sheet", open_listen_sheet)
        step("d2 change voice", lambda: click(AT("change voice"), 5, "Change voice"))

        step("e1 prepare sheet", lambda: click(AT("prepare"), 5, "Prepare (first voice row)"))

        def e2():
            click(AT("whole book"), 5, "Whole book")
            click(AT("continue"), 5, "Continue")
            time.sleep(2)
        step("e2 price screen", e2)
        step("e3 back from price (no narration)", close_sheet)

        def f():
            ensure_shelf()
            go_home()
            open_book("[A-Za-z]{4}", "English book", exclude_js="[\\u0600-\\u06FF]|Trial")
            tap_middle()
            open_listen_sheet()
        step("f english book listen sheet", f)

        step("g1 theme sepia", lambda: theme_to("Sepia"))
        step("g2 theme light", lambda: theme_to("Light"))

        def h1():
            go_home()
            return logout()
        step("h1 logged out", h1)
        step("h2 login admin", lambda: login(ADMIN_USER, ADMIN_PASS))

        def h3():
            open_book("Trial", "book titled Trial")
            tap_middle()
            open_listen_sheet()
        step("h3 trial book listen sheet", h3)

        def h4():
            click([("aria", "play"), ("text", "play")], 5, "Play")
            time.sleep(6)
        step("h4 playing (pill under island)", h4, settle=0)

        def h5():
            click([("aria", "stop"), ("text", "stop")], 4, "Stop")
        step("h5 stop", h5)

        def i():
            set_orientation("LANDSCAPE")
            time.sleep(2)
        step("i landscape", i, settle=1)
        try:
            set_orientation("PORTRAIT")
        except Exception:
            pass
        skip("j add to home screen",
             "not automatable: no simctl or WebDriver API for the Safari Share sheet; see README manual path")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        save_results()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FATAL", type(e).__name__, str(e).splitlines()[0][:300] if str(e) else "")
        save_results()
        sys.exit(1)
