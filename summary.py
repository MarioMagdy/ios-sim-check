#!/usr/bin/env python3
"""Render out/summary.md from out/results.json (written progressively by drive.py)."""
import json
import os

out = os.environ.get("OUT", "out")
os.makedirs(out, exist_ok=True)
lines = ["# iOS Simulator run", "",
         f"- Job status: {os.environ.get('JOB_STATUS', 'unknown')}",
         f"- Device: {os.environ.get('DEVICE_NAME', '?')} (iOS {os.environ.get('PLATFORM_VERSION', '?')})",
         f"- Driver: {os.environ.get('DRIVER', '?')}", ""]
try:
    results = json.load(open(os.path.join(out, "results.json"), encoding="utf-8"))
except Exception:
    results = None
if results is None:
    lines.append("No results.json: drive.py did not start or crashed before the first step. See appium.log.")
else:
    lines += ["| # | Step | Result | Screenshot | Note |", "|---|------|--------|------------|------|"]
    for r in results:
        lines.append(f"| {r['n']:02d} | {r['name']} | {r['status']} | {r.get('shot') or '-'} | {r.get('note', '')} |")
    c = {}
    for r in results:
        k = r["status"].split(":")[0]
        c[k] = c.get(k, 0) + 1
    lines += ["", "Totals: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items()))]
open(os.path.join(out, "summary.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
