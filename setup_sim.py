#!/usr/bin/env python3
"""Pick (or create) and boot the requested simulator; export UDID/PLATFORM_VERSION to $GITHUB_ENV."""
import json
import os
import re
import subprocess
import sys


def sh(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        print("FAILED:", " ".join(args), r.stderr.strip()[:500])
        sys.exit(1)
    return r.stdout


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


device = os.environ.get("IN_DEVICE", "").strip() or "iPhone 17 Pro"
want = os.environ.get("IN_RUNTIME", "").strip()

runtimes = [r for r in json.loads(sh("xcrun", "simctl", "list", "runtimes", "-j"))["runtimes"]
            if r.get("isAvailable") and r.get("platform", "iOS") == "iOS"]
if not runtimes:
    print("No available iOS runtime")
    sys.exit(1)
if want:
    norm = want.replace("iOS-", "").replace("iOS ", "").replace("-", ".")
    cands = [r for r in runtimes if r["version"] == norm or r["identifier"].endswith(want)
             or r["version"].startswith(norm)]
    if not cands:
        print("Requested runtime not found. Available:", [r["version"] for r in runtimes])
        sys.exit(1)
else:
    cands = runtimes
runtime = sorted(cands, key=lambda r: vkey(r["version"]))[-1]
print("Runtime:", runtime["name"], runtime["identifier"])

devs = json.loads(sh("xcrun", "simctl", "list", "devices", "-j"))["devices"].get(runtime["identifier"], [])
match = [d for d in devs if d["name"] == device and d.get("isAvailable", True)]
if match:
    udid = match[0]["udid"]
    print("Found existing device", udid)
else:
    types = json.loads(sh("xcrun", "simctl", "list", "devicetypes", "-j"))["devicetypes"]
    t = [x for x in types if x["name"] == device]
    if not t:
        print("Unknown device type. Available iPhones:", [x["name"] for x in types if "iPhone" in x["name"]])
        sys.exit(1)
    udid = sh("xcrun", "simctl", "create", device, t[0]["identifier"], runtime["identifier"]).strip()
    print("Created device", udid)

sh("xcrun", "simctl", "boot", udid, check=False)  # already-booted is fine
sh("xcrun", "simctl", "bootstatus", udid, "-b")    # blocks until fully booted
print("Booted.")

with open(os.environ["GITHUB_ENV"], "a") as f:
    f.write(f"UDID={udid}\nDEVICE_NAME={device}\nPLATFORM_VERSION={runtime['version']}\n")
