# CE Dongle v3 — Project Changelog

Design decisions and milestones that are not obvious from the schematic, code, or git history.
Hardware-specific entries live in [src/CHANGELOG.md](src/CHANGELOG.md); firmware-specific entries
in [firmware/CHANGELOG.md](firmware/CHANGELOG.md) (submodule).

## 2026-07-03
- First prototypes manufactured and received.
- Firmware submodule bumped: Tasmota fork merged with upstream **v15.5.0** (was v15.0.0-based).
- Tasmota device template naming convention adopted: templates are versioned per hardware
  revision as `CE_Dongle_V3_1`, `CE_Dongle_V3_2`, … — bump the suffix whenever a schematic
  change alters pin usage or peripheral behavior.
- Product architecture (confirmed): WiFi is the primary MQTT transport; the LE910R1 is a
  transparent fallback driven by a **Berry AT-command driver** (no PPP). When WiFi is
  unavailable, MQTT traffic is rerouted over LTE automatically, invisible to other Tasmota
  services.

## 2026-06-03 (retrospective)
- LE910R1-EU validated standalone on Telit EVB 2.0: MQTT (plaintext + TLS) and SMS work
  concurrently; module firmware M0Q.010001 quirks documented in `scripts/README.md`.
