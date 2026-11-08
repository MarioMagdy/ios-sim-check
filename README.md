# iOS Simulator screenshots of Pijom (Mobile Safari, iPhone with Dynamic Island)

A manual GitHub Actions workflow boots an iOS Simulator on a macOS runner, drives Mobile Safari through a scripted
journey, and uploads full-device screenshots (status bar and Dynamic Island area included) as a 1-day artifact.
This repo holds no app code. Nothing here has been run yet.

Files: `.github/workflows/ios-sim.yml`, `setup_sim.py` (pick/create/boot device), `drive.py` (journey),
`summary.py` (writes `summary.md`), this README.

## 1. Create the public repo with only these files

Run from this folder. Replace `YOU/ios-sim`. Public repos get free macOS minutes; private repos bill macOS at 10x.

```sh
git init -b main
git add .github setup_sim.py drive.py summary.py README.md
git commit -m "ios-sim workflow"
gh repo create YOU/ios-sim --public --source . --push
```

## 2. Secrets (values are never printed; the workflow also `::add-mask::`s them and drive.py redacts its logs)

Start the laptop tunnel first, then set `APP_URL` fresh every time because the trycloudflare URL changes.

```sh
gh secret set APP_URL --repo YOU/ios-sim --body "https://<random>.trycloudflare.com"
gh secret set APP_USER --repo YOU/ios-sim
gh secret set APP_PASS --repo YOU/ios-sim
gh secret set APP_ADMIN_USER --repo YOU/ios-sim
gh secret set APP_ADMIN_PASS --repo YOU/ios-sim
```

(`gh secret set NAME` without `--body` prompts, so the value stays out of shell history.)

## 3. Run, watch, download, clean up

```sh
gh workflow run ios-sim.yml --repo YOU/ios-sim -f device="iPhone 17 Pro" -f driver=appium
gh run watch --repo YOU/ios-sim            # pick the run
gh run download --repo YOU/ios-sim -n ios-sim-screenshots -D shots
# read shots/summary.md first, then the PNGs
gh run delete <run-id> --repo YOU/ios-sim  # deletes logs and the artifact with it
```

Optional inputs: `-f runtime=26.4` (default newest installed iOS), `-f driver=safaridriver`.
Then stop the tunnel and delete the secrets: `gh secret delete APP_URL --repo YOU/ios-sim` and the others.

## How it drives the Simulator

- Runner `macos-26` (Apple Silicon). Fallback: edit `runs-on` to `macos-15` and pass `device="iPhone 16 Pro"`
  (the iPhone 17 types need the iOS 26 SDK runtimes).
- Preinstalled per the runner-images macos-26 arm64 README
  (github.com/actions/runner-images, `images/macos/macos-26-arm64-Readme.md`, read 2026-11-08):
  Xcode 26.0.1 to 26.6 (default 26.6); iOS simulator runtimes 26.2, 26.4, 26.5; devices include iPhone 17,
  iPhone 17 Pro, iPhone 17 Pro Max, iPhone Air; Node 24, Python 3.15 (default) with 3.11 to 3.14 cached. Appium is
  not preinstalled, so the workflow runs `npm install -g appium` + `appium driver install xcuitest`.
  Python 3.12 is set up explicitly with `actions/setup-python` so `selenium` installs from wheels without
  PEP 668 trouble.
- `setup_sim.py` finds the newest iOS runtime (or yours), finds the named device, creates it with `simctl create`
  if missing, boots it and blocks on `simctl bootstatus -b`.
- Status bar: `xcrun simctl status_bar <udid> override --time 9:41 --batteryState charged --batteryLevel 100 ...`.
  Dark mode: `xcrun simctl ui <udid> appearance dark`. Set before the browser session starts.
- Screenshots: `xcrun simctl io <udid> screenshot NN-step.png`, not WebDriver screenshots.
- Driver choice:
  - `appium` (default): Appium 2/3 + XCUITest with `browserName=Safari`, `platformName=iOS`,
    `appium:automationName=XCUITest`, `appium:udid`, `appium:deviceName`, `appium:platformVersion`. These are the
    long-standing documented capabilities from my knowledge of the Appium docs; I could not re-fetch the capability
    page this session (the URL I tried returned 404).
  - `safaridriver` (opt-in): capabilities `platformName=iOS`, `browserName=Safari`, `safari:useSimulator=true`,
    `safari:deviceType=iPhone`, `safari:deviceName`, `safari:platformVersion`. NOT VERIFIED: Apple's docs pages
    (developer.apple.com/documentation/webkit/about-webdriver-for-safari) are JavaScript-rendered and my fetch
    returned only the title. I believe these names exist but do not know that `safaridriver` on macOS really
    attaches to Simulator Safari. Treat as an experiment; if it fails, use `appium`.
- Orientation: Appium's `POST /session/:id/orientation` (LANDSCAPE/PORTRAIT). Failure is logged, not fatal.
  `simctl` itself cannot rotate.

## Journey and summary

`drive.py` runs steps a1..j (see `summary.md` in the artifact: one row per step with ok / missing element / error /
skipped, the screenshot name, and a note). Selectors prefer `aria-label` and visible text (case-insensitive
substring). A missing element is recorded and the journey continues, so later steps may also read "missing".
Narration is never started on the Arabic book (Prepare sheet, price screen, back). Narration is started only for
the "Trial" book on the admin account (step h), then stopped.

## Limits and risks

- Simulator is not a device. Audio output, lock screen, Now Playing / Control Center media controls, haptics,
  real safe-area quirks while a call/hotspot bar is active, and real Safari memory/perf are unreliable or absent.
  The narration pill is a DOM element, so it should render, but audio itself may be silent or fail.
- Standalone home-screen (PWA) mode: "Add to Home Screen" is a Safari Share-sheet action. There is no `simctl` or
  WebDriver call for it, so step j is skipped. The standalone display mode (and its status-bar/safe-area
  behavior) is therefore NOT captured; everything above is the in-Safari view. Manual path: boot the same device
  locally on a Mac, or use a cloud device service (BrowserStack/LambdaTest real or virtual iOS with Share-sheet
  access), open the URL, Share, Add to Home Screen.
- Dynamic Island in screenshots: `simctl io screenshot` on a headless device should include the island and status
  bar, but I have not confirmed the island is drawn in headless captures. If the top of the shot is missing it,
  that is a limit of the headless render (open `Simulator.app` would need a GUI session, not available on runners).
- Screenshots show Safari's address bar, which contains the trycloudflare host, and the artifact of a public
  repo is downloadable by any signed-in GitHub user who has the run URL. The URL is short-lived; delete the run
  afterwards (`gh run delete`). Workflow logs of a public repo are public: drive.py logs no URLs or page text, and
  errors are truncated and redacted, but review the first log if you change the scripts.
- Typed credentials appear as dots in screenshots only.
- First Appium session builds WebDriverAgent for the simulator; budget 3 to 8 minutes. Timeout is 45 min total.
- `safaridriver --enable` (safaridriver mode) needs sudo; runners allow it. In Appium mode it is not needed.
- Fragile by design: selectors are guesses at Pijom's markup (aria-labels given in the brief). The "first book"
  finder uses text regexes (Arabic letters `؀-ۿ`, "Trial", else first Latin-letter card). Expect to
  tune after the first run by reading `summary.md`.
- No video, no HAR. Add `xcrun simctl io <udid> recordVideo` in the workflow if needed.
- Cost: public repos: free macOS minutes; keep runs rare.
