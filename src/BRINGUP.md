# CE Dongle v3 — Prototype Bring-up Log

Running log of hardware bring-up on the rev 1 prototypes. Firmware: Tasmota v15.5.0
fork (`tasmota32c6-CE_DONGLE_V3`), template `CE_Dongle_V3_1`. Flashed over native
USB Serial/JTAG (no boot button). See `HARDWARE.md` for the pin map.

## 2026-07-04 — board #1 (MAC 58:8c:81:30:af:b0)

### Confirmed WORKING
- **Flashing/console**: no-button esptool download entry over USB-C works; Tasmota
  boots on USB-CDC console @115200. OTA path untested (Mac not on device subnet).
- **Template/GPIO**: reports as `CE_Dongle_V3_1`; relays/WS2812 present as POWER1/2/3.
- **I2C + RS-485 MUX**: both PCF8574 answer at 0x20 and 0x21; MUX driver disables all
  muxes at boot with **verified readback** (`Target Exp1/2=0x00, Readback=0x00`,
  "EN pins verified Low") — proves the EN-pull-up removal fixed PCF8574 control.
- **LTE power/control**: ON_OFF_N (GPIO18/Q8), PWRMON (GPIO1 via TXS0104), and both
  UART pins (GPIO2 TX / GPIO3 RX) all verified good — module powers, PWRMON reads
  back high, AT round-trips.
- **LTE module**: LE910R1-EU, firmware **M0Q.010001**, `AT+CSQ` 23,6 (good signal),
  responds to AT, `AT&K0`+`AT&W` saved (flow control off, matches board wiring).

### Operational finding — POWER-ON PULSE MUST BE SHORT (~1 s), not ≥5 s
Holding ON_OFF_N low ~9.5 s (the "≥5 s" figure from the LE910**S1** sibling HW guide)
powered the module ON and then straight OFF (PWRMON ended at 0, no AT). A **~1 s**
pulse powers it on cleanly and it stays on (PWRMON latches high). Matches the EVB note
in `scripts/README.md §9` ("briefly tap ON/OFF"). Use a short pulse in firmware.
Power-off: `AT#SHDN` (clean, ~15–20 s to PWRMON low) — verified.

### BLOCKER — SIM not communicating (suspected contact/solder issue on board #1)
Symptom (reproducible):
- `AT#SIMDET?` → `2,1` and `AT#QSS?` → `#QSS: 0,1` → **presence detected** (SIMIN1
  strap to SIM_VCC works).
- but `AT+CIMI` → `+CME ERROR: SIM failure`; `AT+CPIN?`/`AT#CCID` → `SIM not inserted`.
- `AT#SIMDET=1` (force present) did **not** help — the problem is SIM I/O, not presence.

Interpretation: the module activates the SIM but the card never answers the ISO-7816
ATR → open/intermittent contact on one of the SIM data lines, or the card is not fully
seated. Not fixable in software. **Never got to PIN entry** (SIM PIN 1111 not yet tried;
`AT#PCT` unreadable while SIM fails, so attempt counter is safe/untouched).

Physical checklist for the next session:
1. Power down (done), **reseat the SIM** (orientation/notch, full click into J9);
   try a second known-good SIM if available.
2. Reflow/inspect **J9** SIM socket joints — active contacts VCC(J9.1), RST(J9.2),
   CLK(J9.3), I/O(J9.7); and module **U16** SIM balls SIMVCC1(A3), SIMIO1(A5),
   SIMCLK1(A6), SIMRST1(A7). Verify **R45** (SIM_DAT pull-up to SIM_VCC) populated.
3. With module powered and running `AT+CIMI`, probe test points during activation:
   **TP20**=SIM_VCC (should pulse to ~1.8/3 V), **TP22**=SIM_CLK (clock burst ~3.25 MHz),
   **TP21**=SIM_RST (pulse), **TP23**=SIM_DAT (ATR bytes from card).
   - VCC absent → open on VCC path / module not powering SIM.
   - VCC/CLK/RST present but DAT idle → card I/O contact or the card itself.

## 2026-07-04 (session 2) — debug channels + module power fault

### Debug channels (both had issues; WiFi is now the reliable one)
- **WiFi HTTP `/cm` is the reliable channel.** It returned empty replies until we
  discovered Tasmota's Referer CSRF check (`HttpCheckPriviledgedAccess`, SetOption128):
  requests must carry a `Referer: http://<ip>/` header. Helper: scratchpad `hcmd.py`.
  Berry globals persist across HTTP calls (no reset) — much better than USB serial.
  (To drop the header requirement: `SetOption128 1`.)
- **USB-CDC console:** added `-DARDUINO_USB_CDC_ON_BOOT=1` (board json) so it enumerates
  on a cold plug. BUT with CDC_ON_BOOT the Tasmota console RX became unreliable over our
  pyserial scripts (device TX/boot-log flows, host→device commands ignored). Use WiFi.
- **ESP32-C6 download-mode trap:** once in ROM download mode, NO software reset exits it
  (esptool hard_reset re-enters; watchdog_reset "not supported on ESP32-C6"). Only a full
  power-cycle exits — and if GPIO9/BOOT is held/stuck low it re-enters every boot. Flash
  with `--after no_reset` then power-cycle.

### MODULE POWER-ON — needs a LONG ON_OFF_N assertion to LATCH (resolved)
**Root cause: the ON_OFF_N pulse must be held for SEVERAL SECONDS to latch power-on on this
unit — a short pulse does NOT latch.** Symptom that misled us: a ~0.7–1.3 s assertion brings
VAUX/PWRMON up (rises ~50 ms after assert) but the module falls back off ~1 s AFTER the
release. Fine-grained 50 ms PWRMON sampling proved it: with ON_OFF_N *held* continuously,
PWRMON stays high indefinitely and the module STAYS ON after release; with a 700 ms
assertion, PWRMON rises then drops ~1.85 s in (~1.1 s after release). So the drop tracks the
*release of a too-short pulse*, not any power/VBATT problem.

Correct power-on: assert GPIO18 HIGH (ON_OFF_N low) and hold ~3 s, then release. Verified:
after a multi-second hold the module latched and answered AT, ATE0, CMEE, CSQ 17,5. The
Telit "520 ms–3520 ms" figure did not hold for this unit — 700 ms didn't latch; ~3 s+ does.

**MISDIAGNOSIS to unwind:** the earlier "powers on then self-shuts / brownout / FB1 open"
theory was WRONG — it was entirely the too-short pulse. FB1 is fine; the jumper across FB1
and the added 220 µF VBATT cap were unnecessary (harmless — can be reverted). No VBATT/U14
fault. (Credit: the fix came from questioning the pulse timing and then holding ON_OFF_N
while sampling PWRMON.)

### SIM INTERFACE — confirmed BOARD-side fault (not the card)
Known-good SIM (verified working on the Telit EVB) in this board, module latched and stable
(CSQ 17,5), `AT#SIMDET=1` forced: still `#QSS: 0,1` (presence detected) but `AT+CIMI` →
`SIM failure`, `AT+CPIN?`/`AT#CCID` → `SIM not inserted`. Same as the original card. So the
module powers the SIM and starts ISO-7816 activation but the card never answers → a
**contact/solder fault on the SIM data lines of THIS board**, not the card, not software.
Check/reflow: socket **J9** contacts VCC(1)/RST(2)/CLK(3)/I-O(7); module **U16** SIM balls
SIMVCC1(A3)/SIMIO1(A5)/SIMCLK1(A6)/SIMRST1(A7); and confirm **R45** (SIM_DAT pull-up to
SIM_VCC) is populated with good joints. Probe TP20=SIMVCC / TP22=SIMCLK / TP21=SIMRST /
TP23=SIMDAT during an `AT+CIMI` attempt to localize which line is dead.

**LOCALIZED via scope (2026-07-04): SIM_CLK is the dead line.** During activation VCC
pulses (module cycles 3 V then 1.8 V classes, retrying) and I/O (SIMDAT) toggles, but
**CLK (TP22) is silent** → card never clocked → never answers → `SIM failure`. SIM_CLK net =
**U16.A6 (SIMCLK1) — TP22 — J9.3 — C67 (33 pF to GND)**.
Diagnose with a DMM, TP22→GND:
  - **~0 Ω (shorted)** → CLK clamped to GND: **C67 shorted**, or a **solder bridge from A6 to
    an adjacent GND ball** (A2 is GND). Remove/replace C67 or clear the bridge.
  - **open / high** → CLK not driven: **U16 A6 ball open** or a broken SIM_CLK trace. Reflow
    U16 (esp. A6); verify continuity A6↔TP22↔J9.3.
Then re-run `AT+CIMI` — CLK should show a ~3–4 MHz burst and the card's ATR should appear on I/O.

**UPDATE (2026-07-04): NOT a solder open, and it fails on BOTH soldered boards → systematic.**
User diode-tested the SIM pins: normal module ESD diode present (pins bonded, no open joint).
Same module firmware (M0Q.010001) reads a SIM fine on the Telit EVB. Scope: VCC cycles 3 V→1.8 V
(voltage-class retry), SIM_DAT toggles, **SIM_CLK stays silent**.
**Leading hypothesis: C67 (SIM_CLK filter cap, spec 33 pF) populated with the WRONG value
(e.g. 33 nF).** At ~3.25 MHz, 33 nF ≈ 1.5 Ω → AC-shorts the clock to GND (silent); 33 pF ≈ 1.5 kΩ
(fine). Slow lines (DAT/RST/VCC) survive a too-large cap → matches "only CLK dead." A cap is not
a DC short → passes the diode test. Same BOM/reel error on both boards → identical failure; EVB has
correct parts. TEST: measure C67's capacitance (nF-range = confirmed) or lift C67 and re-scope CLK
(it's only an EMI cap, SIM works without it); if fixed, check C65 (DAT)/C68 (RST) — same 33 pF spec.
Full-line series-R absence and Telit's max SIMCLK load cap being verified via research.

### ROOT CAUSE (2026-07-04, **CONFIRMED** — see confirmation section below): SIMIN1 tied to SIM_VCC instead of GND
**Schematic error: R51 connects SIMIN1 (U16.A4) to SIM_VCC.** A SIM presence line must be
static; this one tracks SIM_VCC and flips during activation:
- Module idle (SIM_VCC=0) → SIMIN LOW → "inserted" → module starts activation.
- Module powers SIM (SIM_VCC→1.8/3V) → SIMIN pulled HIGH → module reads "card REMOVED" →
  **aborts activation before applying SIMCLK/SIMRST** → retries forever. Exactly the scope trace:
  VCC + I/O(DAT) active, SIM_CLK and RST flat at 0V, VCC cycling 3V/1.8V; AT+CIMI="SIM failure".
Evidence: (1) scope shows abort right after VCC; (2) #QSS status flips on its own
(0,1 at power-on → 0,0 later) proving SIMIN is unstable/tracking SIM_VCC; (3) neither
SIMIN polarity (AT#SIMINCFG mode 0 vs 1) fixes a *transitioning* line — mode 0 aborts on
VCC-rise, mode 1 won't start (idle reads removed); (4) AT#SIMDET=1 starts activation but does
NOT suppress the hardware removal-detect on that edge; (5) Telit HW guide says tie SIMIN to GND.
Ruled out before this: card, solder, PCB/passives, caps (33pF ok), 11µF (normal - fresh module
reads same in-circuit), module symbol+footprint (A6=SIMCLK1 correct), socket J9 footprint
(pad N = C_N verified correct vs LCSC C7529385 / GCT SIM8050 / ISO-7816-2), CFUN (set =1, no change).
FIX: tie SIMIN1 to GND — move R51's far end from SIM_VCC to GND (or drop R51, tie SIMIN1 straight
to GND). Bench test: jumper R51's SIMIN1 pad to GND, keep SIMDET=2/SIMINCFG=0,0, retry AT+CIMI.
Confirm mechanism: scope R51's SIMIN1 pad during activation — pulses HIGH when VCC rises.
NEXT-REV SCHEMATIC CHANGE: R51 to GND (not SIM_VCC).

### ✅ CONFIRMED (2026-07-04): SIMIN1→GND fixes SIM; full LTE registration achieved (board 3)
Verification was messy — logged for honesty:
1. **First jumper test (board 1, .189) grounded the WRONG net** — SIMIN2 (R52), the unused
   SIM-2 presence pin. Result (still failing) was meaningless.
2. **Second test (board 1, R51 pin 1 to GND) still failed** — looked like a refutation, but the
   user then measured **~2 Ω SIM_VCC→GND on that board** = hard short that kills activation by
   itself → test confounded, board 1 set aside. (Short origin unresolved: possibly the jumper
   bridging R51 pin 1→pin 2 (0402, pin 2 = SIM_VCC), or a real defect (C64/C66/BGA). TO DO:
   remove jumper, re-measure.)
3. **Board 3 (fresh, MAC 58:8C:81:2E:9C:AC, .248): stock circuit failed identically**
   (#QSS 0,1 idle; CIMI "SIM failure") — exactly what the SIMIN theory predicts, and proof the
   failure is systematic, not per-board soldering.
4. **Board 3 + R51 pin 1 grounded (verified TP20→GND ≈ 10 k through R51, no bridge):**
   after AT+CFUN=4→1 re-init: **+CPIN: READY, AT+CIMI → 260021821600413,
   AT#CCID → 8948022225086004131**. First try.
5. **Registration follows immediately: +CEREG: 0,1 (home), +COPS: 0,2,"26002",7 (LTE),
   +CSQ: 15,4 (≈ −83 dBm).** Full chain works: power latch → UART → SIM → LTE network.
Notes: the SIM's **PIN lock is disabled** — +CPIN: READY without PIN entry (PIN 1111 unused,
attempt counter untouched). Module config on board 3 is factory (SIMDET 2 default).
Bench fix per board: ground R51's SIMIN1-side pad (pin 1 — the pad NOT shared with
TP20/C64/C66/R45). NEXT-REV: R51 far end to GND, or drop R51 and tie SIMIN1 to GND per Telit HDG.

### (superseded) CAP THEORY REFUTED (2026-07-04): the caps are NOT the cause. User confirmed C65/C67/C68 are
correct 33 pF (match other boards, ceramic color); on a BARE board (no module) the SIM nets read
the correct ~33 pF. The **11 µF appears only once the module (LE910R1-EU) is soldered** → it comes
from the module's SIM pins, not the caps or the PCB. Footprint also checked OK (U16 pad grid
self-consistent: A6 at row-A/col-6, A5/A6 not swapped; module works for UART/RF/power → placement
aligned). So NOT caps, NOT PCB/passives, NOT footprint/pin-swap, NOT the SIM card.
OPEN QUESTION: the LE910R1 datasheet spec's SIM pad Ci = ~5 pF, so 11 µF in-circuit is either
(a) a normal artifact of measuring an IC pin in-circuit (meter drives current through the module's
internal SIM/ESD network — verify by measuring a KNOWN-GOOD module the same way), or (b) both
modules' SIM pins are abnormal/damaged. Still to do: confirm SIM_CLK is truly absent (trigger scope
on VCC-rise; also watch RST) vs a brief ~10 ms burst; try module-side SIM config via AT; check
module provenance (same batch? verified before solder? ESD during SIM insertion?).
--- superseded text below kept for history ---
User measured **11 µF** on both SIM_CLK and SIM_DAT with an RLC meter (spec is 33 pF).
Module SIM ball assignments verified CORRECT against the LE910R1 HW Design Guide r9 (§ pin table:
A6=SIMCLK1, A5=SIMIO1, A3=SIMVCC1, A7=SIMRST1, A4=SIMIN1) → NOT a pin/symbol mismatch. So
**C65 (SIM_DAT), C67 (SIM_CLK), C68 (SIM_RST) were populated with ~10 µF instead of 33 pF** — a
reel/BOM/pick-and-place error, systematic across both boards (EVB has correct parts → works).
At 3.25 MHz, 10 µF ≈ 0.005 Ω = dead short → **CLK silent**; DAT is slow so it survives enough to
show activity; a cap is not a DC short so the pin diode-tests normal. FIX: remove C65/C67/C68
(pure EMI caps, SIM works without them) or replace with correct **33 pF** (Telit ref = 33 pF–100 Ω–
33 pF Pi filter per HDG §6.7/§ "background noise"); correct the BOM/assembly for future builds.
Verify by lifting C67 and measuring out-of-circuit (~10 µF confirms).

### How to resume (module drive helper)
Driven live over USB serial with Berry `Br` commands (no file upload needed). Recreate
per session (ESP32 resets on serial open; module persists once powered):
```
Br import gpio
Br gpio.pin_mode(1,gpio.INPUT_PULLDOWN)      # PWRMON read
Br s=serial(3,2,115200)                       # GPIO3<-module TXD, GPIO2->module RXD
Br def q(c) s.read(); s.write(bytes().fromstring(c+'\r\n')); tasmota.delay(700); return s.read().asstring() end
# power on (only if PWRMON==0): pulse GPIO18 high ~1s then low, wait ~5s, poll gpio.digital_read(1)
Br q('AT')
```
(A reusable `fs/lte_bringup.be` helper also exists — `load('lte_bringup.be')` then use
the global `lte`.)

## 2026-07-04 (session 3) — LTE data stack fully up (board #3, .248)

With the SIM fixed (R51 pin-1 grounded), the full cellular data chain came up on board #3.

### Registration → data → sockets (all verified on hardware)
- **SIM/registration**: `+CPIN: READY` (no PIN — this SIM's PIN lock is disabled),
  IMSI `260021821600413`, ICCID `8948022225086004131`, `+CEREG: 0,1` (home),
  `+COPS: …"26002"…,7` (Orange PL, **AcT 7 = LTE**), `+CSQ: 15,4` (≈ −83 dBm).
- **Data context**: `AT+CGDCONT=1,"IPV4V6",""` — **APN left empty; the network
  auto-provisions** (Orange PL). `AT#SGACT=1,1` → context up, `AT+CGPADDR`/`AT#SGACT?`
  returned a live IP (e.g. `10.227.230.138`). `AT#PING 8.8.8.8` = **4/4 replies**.
- **Raw sockets present**: `AT#SD` (TCP, 6 sockets) and `AT#SSLD` (TLS) both work.
- SMS text mode works (`CMGF=1`); 25 SMS slots.

### ⚠ Module firmware is a REDUCED variant — no built-in MQTT AT client
`AT#MQEN`/`#MQCFG`/`#MQCONN`/… all return a **bare `ERROR`** on the production
module (verified not a `+CME` error via `CMEE=2`) — the built-in MQTT client is
**absent**, even though the firmware string (`M0Q.010001`, model `LE910R1-EU`) is
identical to the Telit EVB where `#MQ` worked. `#OTAUPD`/`#FOTACFG`/`#FWSWITCH` are
also absent, while sockets (`#SD`/`#SSLD`/`#SGACT`/`#SSLEN`) work → this is a
**per-unit reduced firmware image**, not a runtime toggle. Consequence: MQTT-over-LTE
cannot use `#MQ`; it must ride raw TCP/TLS sockets **or** PPP (see below).
Module fingerprint for a Telit ticket (board #3): model `LE910R1-EU`,
SW `44.00.014-P0Q.010004`, modem `M0Q.010001`, IMEI `359356503873484`.

### Two MQTT-over-LTE paths were prototyped, then PPP was chosen
- **Socket path** (`fs/lte.be` + `fs/mqtt.be`, Berry): a minimal MQTT 3.1.1 codec over
  an `#SSLD` TLS socket. Full round-trip proven against `test.mosquitto.org:8883`
  (CONNACK/SUBACK/publish echo). Works on stock firmware and coexists with AT/SMS, but
  reimplements MQTT and carries only MQTT.
- **PPP path** (chosen): LE910R1 as a normal lwIP network interface → Tasmota's own
  MQTT/TLS/NTP/reconnect all ride LTE transparently. This is the path taken forward.

## 2026-07-04 — PPP: LE910R1 as a transparent lwIP netif (egress PROVEN)

### esp_modem / Arduino `PPP` class CANNOT be used — they crash
An `xdrv` built on the Arduino `PPP` class compiled/linked/flashed fine but **`abort()`ed
at begin** (addr2line → `esp_modem::throw_if_error()`). Root cause: the framework
sdkconfig has `# CONFIG_COMPILER_CXX_EXCEPTIONS is not set`, so `esp_modem`'s `throw`
becomes `abort()` under `-fno-exceptions`. Unfixable without rebuilding the whole
IDF/Arduino framework. **Correct path: raw lwIP PPPoS (pure C, no exceptions)** —
`CONFIG_LWIP_PPP_SUPPORT=y`, `pppos.h`/`pppapi.h` present for esp32c6.

### Driver + the route fix
`xdrv_128` installs a dedicated UART, dials `ATD*99***1#`, and runs raw PPPoS
(`pppapi_pppos_create`/`_connect`, a FreeRTOS RX task feeding `pppos_input_tcpip`).
UART assignment: **LTE gets UART0** (console is USB-CDC, so UART0 is free); **Modbus
keeps UART1** — the two run concurrently. Self-recovery: a half-dial leaves the module
stuck in PPP data mode ignoring AT → `+++` 1 s-guarded escape, else GPIO11 HW_SHUTDOWN
power-cycle + re-wait `AT`/`CREG`.

Routing was the hard part (verified against ESP-IDF lwIP/esp_netif source via a 4-agent
review):
- This build has **`CONFIG_LWIP_TCPIP_CORE_LOCKING=0`** → `LOCK_TCPIP_CORE()` is a
  **no-op**. All route/DNS mutations from the main thread **must** be marshaled onto the
  tcpip thread via `tcpip_callback`. The PPP status callback already runs on the tcpip
  thread, so it may call `netif_set_default`/`dns_setserver` directly.
- Switch to PPP as default **in the status cb on `PPPERR_NONE`**, not at create — a
  *down* netif set as default black-holes traffic.
- `pppapi_close` does **not** free the pcb/netif — must `pppapi_free` after the DEAD
  phase, else every failover cycle leaks a pcb.

**Egress conclusively proven** (`scratchpad/ppp_egress_test.py`): board `webclient` GET
`http://api.ipify.org/` returned the home WAN `46.205.197.70` (WiFi, ERANET AS12912)
→ bring LTE up → same GET returned `37.47.104.243` (**Orange Mobile PL, different ASN,
verified via whois**) → LTE down → back to `46.205.197.70`. The `/cm` LAN command channel
stayed up on WiFi throughout (the board's own subnet is directly connected, never uses
the default route). Handy gotchas: the Tasmota Berry console command is **`Br`** (not
`Berry`); a `webclient` URL **must include a path** (`/`) or servers 400.

## 2026-07-05 — WiFi↔LTE WAN failover

### v1 — on-demand failover (WiFi down → bring LTE up)
FSM in `FUNC_EVERY_SECOND` + a worker task running the blocking modem bring-up (main
loop never stalls — HTTP answered every 3 s during a 28 s bring-up). Debounced
WiFi-down → LTE, WiFi-up → revert. **Across-flap route hold proven**: when WiFi
reassociated, esp_netif re-asserted the WiFi default route on `GOT_IP`; the FSM's
per-second re-assert (fire-and-forget `tcpip_callback`) took it back to LTE until the
up-debounce reverted. A 3-agent review before flashing caught 3 real bugs (restore
target snapshotted *during* the outage → black-hole on revert; double-teardown; a
1500 ms main-loop stall) — all fixed. Then hardened with a **WiFi-WAN reachability
probe** (bound `SO_BINDTODEVICE` TCP-connect out the WiFi netif while LTE owns the
default route) so failover also triggers on "WiFi associated but WAN dead".

### v2 — hot-standby dual-link, MQTT-health driven (current design)
User redesign: keep **both links up simultaneously** so MQTT is *always* active; probe
the **MQTT broker** on both links; WiFi primary, LTE fallback.
- **Health**: the ACTIVE link uses Tasmota's live `MqttIsConnected()` (authoritative);
  the STANDBY link uses a bound TCP-connect probe (`SO_BINDTODEVICE`) to `MqttHost:port`,
  which reaches the broker over that link even though it isn't the default route.
- **Per-link DNS is required** and is the subtle part: the home router's DNS
  (`192.168.200.1`) is a **private address only reachable over WiFi**, so when routing
  over LTE the device must use the **carrier DNS** (learned via `ppp_set_usepeerdns`).
  WiFi's DHCP DNS is captured from the lwIP global table while it's WiFi's; the active
  link's DNS is re-asserted on every switch + periodically (defeats DHCP re-clobber).
  (`esp_netif_get_dns_info` is useless here — `CONFIG_ESP_NETIF_SET_DNS_PER_DEFAULT_NETIF`
  is off, so it just reads the global table.) *Note: an earlier "the network blocks
  external DNS" claim was **wrong** — `dig @1.1.1.1/@8.8.8.8/@9.9.9.9` all resolve from
  this network; per-link DNS is needed because the router DNS is private, not blocked.*
- **Policy**: hard failure (link can't TCP-reach broker for N probes) = immediate switch;
  soft failure (TCP ok but MQTT down ≥8 s) = **one-shot** switch per MQTT-down episode
  (no broker-outage ping-pong); failback to WiFi only after it's healthy for the hold
  (default 5 min); **both hard-dead = HOLD + alarm** (never flap into a dead link).
- **Validated against the real TLS broker** `don.columbusenergy.cloud:8883`. That broker
  has an outdated/self-signed cert → accept it with Tasmota MQTT **fingerprint** mode:
  `SetOption103 1` (TLS) + `SetOption132 1` (fingerprint, skips CA/expiry) +
  `MqttFingerprint1` = 40×`FF` (accept any cert). All 4 scenarios pass
  (`scratchpad/ppp_v2_test.py`): hot-standby, hard failover→failback, soft failover +
  no-ping-pong, both-down hold.

## 2026-07-05 — Driver split (mechanism / policy)

The monolithic driver was split so the modem is reusable independent of the failover
policy (old `xdrv_128_lte_ppp.ino` deleted):
- **`xdrv_128_lte_modem.ino` (mechanism)** — "LE910R1 as a lwIP netif." Owns power/UART/
  AT/dial/PPPoS + a keeper task that keeps the link up (auto re-dial). **Never touches
  routing/DNS.** C API `LteModemBegin/End/IsUp/Netif/GetIfname/GetDns/OnLinkChange`;
  commands `LteBegin`/`LteEnd`/`LteState`.
- **`xdrv_126_wan_failover.ino` (policy)** — owns `netif_default` + per-link DNS + the
  health FSM; keeps LTE hot via the modem API, probes the broker per-link, and decides
  switches. On LTE up/down the modem fires a callback and the *manager* does the instant
  route restore. Commands `WanFailover` (1/0 + test hooks), `WanProbe`/`WanHold`/`WanConfirm`.

Build model: Tasmota concatenates all `tasmota_*/ *.ino` into ONE translation unit, so
cross-driver calls just work; the cross-driver API uses **only basic types**
(`uint32_t`/`char*`/`struct netif*`/inline fn-ptr) so the auto-generated prototype at the
top of the TU never references a not-yet-defined lwIP typedef. A 2-agent review caught one
split-introduced bug — a **keeper resurrection race** (disarm then re-arm during the
keeper's ~2.3 s teardown left `want_up=true` with no keeper → LTE silently never came up);
fixed by wrapping the keeper in `for(;;)` that re-checks `want_up` after teardown.
HW-verified (disarm+re-arm → LTE back up).

## Current production state (board #3, .248)

- `WanFailover`: Armed, Active WiFi, both links up, `MqttUp:1`, `BrokerOk:1`, `DnsOk:1`.
- `LteState`: Up, carrier IP, ifname `pp3`.
- Runtime config: `WanProbe 30` (30 s broker probe), `WanHold 300` (5 min failback),
  `WanConfirm 2`.
- Boot arm rule: `Rule1 ON System#Boot DO Backlog Delay 300; WanFailover 1 ENDON` +
  `Rule1 1` (arms ~30 s after boot so WiFi connects first).

**⚠ Each full-image flash (`esptool … write_flash 0x0 …factory.bin`) WIPES device
settings** (MQTT host/user/pass, TLS options, rules, hostname — and the LittleFS config).
WiFi STA creds survive only because they're compiled into `user_config_override.h`. After
any flash, re-provision MQTT + TLS fingerprint + the boot rule.

Build/flash (must run from `firmware/`):
```
cd firmware && ~/.platformio/penv/bin/pio run -e tasmota32c6-CE_DONGLE_V3
/opt/homebrew/bin/esptool.py --port /dev/cu.usbmodem1101 --chip esp32c6 \
  write_flash 0x0 .pio/build/tasmota32c6-CE_DONGLE_V3/firmware.factory.bin
```

## 2026-07-05 — USB-CDC console RX fixed (typed commands now work)

The USB-CDC console was read-only: logs streamed out, but typed commands were ignored. It turned
out **not** to be a HWCDC/driver bug — bytes arrived fine. Because this board sets
`-DARDUINO_USB_CDC_ON_BOOT=1` (the only Tasmota board that does — added for cold-plug enumeration),
the Arduino core aliases `Serial` == `HWCDCSerial` == `TasConsole` (one object). Tasmota's
`SerialInput()` runs first each loop, drains the bytes, but its execute-on-newline block is gated
`if (tasconsole_serial)` (false on USB-CDC), so it **silently dropped** the command before
`TasConsoleInput()` could run it. Fix = one line in `tasmota.ino` (`is_connected_to_USB` branch):
`TasmotaGlobal.serial_local = true;` → `SerialInput()` is skipped, `TasConsoleInput()` executes the
command. HW-verified (`TelePeriod 88` typed over USB took effect). Full write-up + "why upstream
never hit it" in `firmware/CHANGELOG_CE_DONGLE.md` (2026-07-05). *Recovery note learned: a
`factory.bin` flash at `0x0` does NOT wipe settings/LittleFS here — use `esptool erase_flash` for a
clean slate; the LTE feature set + template `CE_Dongle_V3_2` are also in that changelog.*

## 2026-07-05 — Full logging / auditability on both LTE drivers

Both `xdrv_128` (LTE modem) and `xdrv_126` (WAN failover) now emit a complete, level-graded log so a
field incident can be reconstructed from the log alone. No behaviour changed — only `AddLog` coverage.

**What's logged, by level:**
- `ERROR` — real failures: power-on / UART-install / dial / `pppos_create` / registration timeout,
  SIM PIN rejected or PUK-locked, `pppapi_free` leak, `+++`/ATO escape failure, tcpip-callback
  timeout, **both links unable to reach the broker**, config-save failure.
- `INFO` — the default decision trail: begin/end, keeper/probe/rx task start+exit, **bring-up attempt
  #N** with outcome + elapsed, LINK UP (ip/gw/DNS) / DOWN (decoded PPP error), arm / disarm /
  **boot-arm**, every **SWITCH** with a state snapshot (`wifiPath ltePath wifiPfail ltePfail mqtt
  lteLink`), WiFi/LTE/MQTT health + broker-reachability **edges** (logged once per change), both-down
  enter + **clear**, active-WAN role change, and per-field config changes.
- `DEBUG` — the full AT transcript, APN/operator apply, per-poll registration, route/DNS apply,
  cfg load & persist.
- `DEBUG_MORE` — per-probe raw reachability + per-tick default-route re-assert.

**Reading it:** each bring-up stamps a monotonic attempt # into all its lines, so a failed attempt's
power→UART→SIM→registration→dial→PPP sequence correlates cleanly. Transitions are edge-tracked
(logged once, not every 1 s tick), so raising the level doesn't flood.

**Secrets:** the SIM PIN and APN password are never written to the log (`AT+CPIN="****"` /
`AT#PDPAUTH=…,"****"`; config lines say `set`/`cleared`). Command *responses* still echo values to
whoever issued the command — unchanged — but those don't enter the log stream.

**Capturing the trail** (firmware emits it; where it lands is an operator setting — all default to
INFO, i.e. production stays quiet):
- `SysLog 3` + `LogHost <collector-ip>` + `LogPort 514` → remote syslog, **survives reboots** (best
  for field forensics).
- `MqttLog 3` → publishes each line to `tele/<topic>/LOGGING` for broker-side collection.
- `WebLog 3` (Console page) or `SerialLog 3` (USB console, now working) → live DEBUG at the bench.

## Open hardware items

- **Board #1 (.189)**: remove the R51 jumper and **re-measure the 2 Ω SIM_VCC→GND short**
  (origin unresolved — jumper bridge pin1→pin2, or a real C64/C66/BGA defect). Board set
  aside until this is cleared.
- **Next-rev schematic**: move **R51's far end from SIM_VCC to GND** (or drop R51 and tie
  SIMIN1 straight to GND per the Telit HDG). This is the systematic SIM fix.
