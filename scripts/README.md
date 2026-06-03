# LE910R1 module test & bring-up notes

Tooling and findings for driving the **Telit LE910R1-EU** cellular module on the
**Telit EVB 2.0** (with the **LE910S1/R1 interface board / TLB**) from **macOS**,
exercising **MQTT** and **SMS** — the two functions this device will use in production.

- Module: **LE910R1-EU**, firmware **M0Q.010001**, IMEI `358890826920243`
- Network during tests: **Orange Poland (26003)**, **LTE** (AcT 7), signal `CSQ ~25`
- Host: macOS (Apple Silicon), Python 3.14, libusb 1.0.29

---

## 1. Why libusb instead of a serial port

On macOS the module enumerates as a composite device (**VID `0x1bc7`**, PID `0x701a`,
product string "Mobile Composite Device Bus") whose AT/serial ports are **vendor-specific
interfaces (`bInterfaceClass 0xFF`)**. macOS has **no kernel driver** for those, so
**no `/dev/cu.usbmodem*` is ever created** and `pyserial`/`screen` cannot reach the module.
All scripts here therefore talk straight to the interface's **bulk IN/OUT endpoints via
pyusb/libusb**.

USB composition observed (PID `0x701a`):

| Interface | Class | Role |
|-----------|-------|------|
| if#0 + if#1 | `0xE0` / `0x0A` | RNDIS network (data) |
| if#2 | `0xFF` | DIAG/QMI (no AT) |
| **if#3** | `0xFF` | **AT / modem port** (scripts auto-select this) |
| if#4 | `0xFF` | AT / AUX port |

> To get a normal `/dev/cu.usbmodem*` on macOS you'd have to switch the module to a
> CDC-ACM composition with `AT#USBCFG=<n>` (persists) — but you need an AT channel to
> send that, so libusb is the prerequisite anyway.

---

## 2. Setup

```bash
brew install libusb                      # 1.0.29 used here
python3 -m venv scripts/.venv
scripts/.venv/bin/pip install pyusb pypdf
```

Prerequisites for any script: module **powered on** (PWRMON steady) and the **DEVICE
(USB PWR)** USB-C cable connected to the Mac (NOT the EVB/FTDI port — see §8).

---

## 3. Scripts

| File | Purpose |
|------|---------|
| **`telit_at.py`** | Reusable libusb AT transport (`AtPort`), reset-resilient. Run directly = USB/identity/SIM smoke test. |
| **`read_sms.py`** | Read & decode all SMS (PDU mode; GSM-7/UCS2/8-bit; reassembles concatenated). |
| **`mqtt_test.py`** | Full MQTT command coverage, plaintext + TLS, broker via flags or `.env`. |
| **`mqtt_sms_test.py`** | MQTT + SMS concurrency/endurance test with MQTT auto-reconnect. |

```bash
# smoke test — is the module alive over USB?
scripts/.venv/bin/python scripts/telit_at.py

# MQTT functional test
scripts/.venv/bin/python scripts/mqtt_test.py --broker mosquitto       # 1883 plaintext
scripts/.venv/bin/python scripts/mqtt_test.py --broker mosquitto-ssl   # 8883 TLS, anon
scripts/.venv/bin/python scripts/mqtt_test.py --broker env             # broker from ../.env

# SMS
scripts/.venv/bin/python scripts/read_sms.py                # SIM must be READY
scripts/.venv/bin/python scripts/read_sms.py --pin 1111     # enter PIN first

# MQTT + SMS together (the real use case)
scripts/.venv/bin/python scripts/mqtt_sms_test.py --minutes 10
scripts/.venv/bin/python scripts/mqtt_sms_test.py --minutes 30 \
    --sms-interval 300 --dest +48792846824 --delete-rx
```

`.env` (repo root, git-ignored) holds broker creds:
`MQTT_ADDRESS / MQTT_PORT / MQTT_USERNAME / MQTT_PASSWORD`.

---

## 4. MQTT — results

| Target | Mode | Result |
|--------|------|--------|
| `test.mosquitto.org:1883` | plaintext | ✅ **19/19** — connect, sub, pub, `#MQRING`, read (payload matched), `MQPUBSEXT`, unsub, disconnect |
| `test.mosquitto.org:8883` | **TLS 1.2** (Verify-None) | ✅ **23/23** — full encrypted round-trip |
| `don.columbusenergy.cloud:8883` | TLS | ❌ TLS handshake rejected — see §6 |

**Working plaintext sequence** (order matters — see quirks):
```
AT#SGACT=1,1                      # activate PDP context (cid 1) FIRST  -> returns IP
AT#MQEN=1,1                       # enable MQTT client instance 1
AT#MQCFG=1,"host",1883,1          # host, port, cid
AT#MQCFG2=1,60,1                  # keepalive, clean-session (optional)
AT#MQCONN=1,"clientid","user","pass"
AT#MQSUB=1,"topic"               # NOTE: no qos arg (see quirks)
AT#MQPUBS=1,"topic",0,1,"msg"
# incoming -> URC: #MQRING: <inst>,<mId>,<topic>,<len>  ->  AT#MQREAD=1,<mId>
AT#MQDISC=1
```

**Working TLS sequence** (broker with a normal cert/endpoint):
```
AT#SSLEN=1,1                      # enable SSL socket SSId 1
AT#SSLSECCFG2=1,2                 # TLS 1.2 (module max)
AT#SSLSECCFG=1,0,0               # cipher auto, auth_mode 0 = Verify None
#   (or auth_mode 1 + store CA via AT#SSLSECDATA=1,1,1,<size> for full validation)
AT#MQCFG=1,"host",8883,1,1       # trailing ,1 = sslEn  (NO sslInstance field, see quirks)
AT#MQCONN=1,"clientid","user","pass"
```

---

## 5. Firmware quirks — LE910R1 **M0Q.010001** (differ from AT manual rev 7)

- **`AT#SGACT=<cid>,1` must be active before `AT#MQCFG`** (manual implies optional; it isn't).
  A repeat returns `+CME ERROR: context already activated` — harmless.
- **`AT#MQEN=1,1` must precede `AT#MQCFG`.**
- **`AT#MQWCFG` (Last-Will) RESETS the module** and drops USB — even the `,0` form. **Avoid it.**
- **`AT#MQSUB` rejects the optional `<qos>`** parameter. `AT#MQSUB=?` → `(1-2),256` (no qos field).
  Use `AT#MQSUB=<inst>,<topic>` (defaults to qos 1).
- **`AT#MQCFG` has NO `<sslInstance>` field.** `AT#MQCFG=?` → 5 params `(1-2),512,(1-65535),(1-6),(0-1)`.
  For TLS use `...,<cid>,1` (sslEn only); TLS settings come from SSL socket SSId 1.
- **No MQTT auto-reconnect.** The `#MQ*` client does not reconnect on drop — the app must
  poll `AT#MQCONN?` (state `1` = connected) and reconnect. `mqtt_sms_test.py` shows the pattern.
- **`AT#SSLSECCFG=1,<cipher!=0>,...` was rejected** in testing; cipher `0` (server-chosen) works.
- **Verify-None still rejects an expired server cert** (see §6).

---

## 6. `don.columbusenergy.cloud:8883` — cannot connect (full investigation)

**The module cannot complete the TLS handshake to this broker, and it is NOT just the
expired certificate.** Proven by elimination:

| Tried | Result |
|-------|--------|
| Verify-None, real clock | ❌ |
| Verify-None, module clock rolled into cert validity window (Sep 2024) | ❌ |
| Verify-Server + ISRG Root X1 CA + clock in-window | ❌ |
| Verify-Server + R10+ISRG chain CA + clock in-window | ❌ |
| Same module → `test.mosquitto.org:8883` | ✅ works |

Facts gathered:
- TCP to broker **reachable** from the module (`AT#SD ... OK`).
- Broker cert **expired 2024-10-22** (Let's Encrypt R10, `CN=don.columbusenergy.cloud`);
  module clock is correct so it sees the expiry.
- Broker is on **AWS (`13.38.160.6`)**, sends a **2-cert chain** (leaf + R10); mosquitto sends 1.
- Broker offers **TLS 1.2 / `ECDHE-RSA-AES128-GCM-SHA256`** — a cipher the module supports.
- **No SNI required, no client cert requested.**
- **No plaintext port** (1883/8083/8884 closed).
- **NITZ** keeps resyncing the clock to real time, so a clock-rollback workaround isn't durable.

**Conclusion:** a **TLS-stack incompatibility** between firmware M0Q.010001 and the broker's
strict AWS-ALB TLS policy (independent of expiry). **Fixes (server/firmware side):**
1. Renew the broker cert **and** relax/adjust the ALB TLS security policy (broader TLS 1.2
   cipher/curve set), or terminate TLS in the broker itself (mosquitto/EMQX/NanoMQ) rather
   than an ALB.
2. Or update the module firmware (newer Telit TLS stack).
3. Re-test with `mqtt_test.py --broker env` after any change.

If you stand up **your own** TLS broker (recommended): terminate TLS directly in the broker,
`tls_version tlsv1.2`, a simple cert chain (or store the CA on the module via `#SSLSECDATA` +
`auth_mode 1`). That path is proven to work (mosquitto:8883 = 23/23).

To store a CA for `auth_mode 1`: download the chain (e.g. Let's Encrypt R10 + ISRG Root X1),
`AT#SSLSECCFG=1,0,1,1` (PEM), then `AT#SSLSECDATA=1,1,1,<bytes>` → send PEM + `Ctrl-Z`.

### MQTT over WebSocket (WSS)?
**Not supported.** No WebSocket/WSS references exist in the AT manual or product brief, and
`AT#MQCFG` only does native MQTT over TCP/TLS. WSS is also TLS, so it wouldn't bypass the
issue above. Use a plain MQTT-over-TLS broker instead.

---

## 7. SIM & SMS

### EVB SIM-detect quirk → **`AT#SIMDET=1` required**
Symptom: card detected but unreadable (`#QSS: …,1` but `AT+CPIN?` = "SIM not inserted",
`AT+CIMI` = "SIM failure"); the **SIM1 LED turns on then off after a few seconds**.
Cause: the EVB's **SIMIN presence-detect line glitches** (EVB User Guide §8.3) → the module
thinks the card was removed and **cuts SIM power**. The data contacts are fine.

**Fix (applied + persisted):**
```
AT#SIMDET=1     # ignore SIMIN pin, assume SIM inserted (forces a real SIM query)
AT&W            # save to profile so it survives reboot
```
After this the SIM was rock-stable (READY 10/10 polls). `AT#SIMDET=2` is the default
(auto via pin) and is what causes the dropouts on this board.

### SMS facts for this SIM / network
- **Service center set**: `AT+CSCA?` → `"+48501200777"` → sending works.
- **CS-registered** (`+CREG: 0,1`) → SMS-over-LTE via SGs available alongside data.
- **`AT+CNUM` is empty** — SIM doesn't expose its own MSISDN; learn the number by sending
  out and reading the sender on the receiving phone.
- **SIM SMS storage ≈ 25 slots** (`AT+CPMS?` → `SM,…,25`). **A device receiving config SMS
  MUST delete after processing** (`AT+CMGD=<idx>`, or `mqtt_sms_test.py --delete-rx`) or
  storage fills and new SMS get rejected. ME storage = 180 slots.
- New-message URC: `AT+CNMI=2,1` → `+CMTI: "SM",<index>` on arrival (or poll
  `AT+CMGL="REC UNREAD"`).

### Reading SMS
`read_sms.py` reads in PDU mode and decodes GSM-7 / UCS2 / 8-bit, reassembling multipart
messages (text mode shows UDH messages as hex). The 14 messages on the test SIM were all
Orange onboarding texts; concatenated ones were reassembled correctly.

---

## 8. MQTT + SMS concurrency (the production use case)

**Result of a 4-minute concurrent run (Orange PL / LTE):**

```
MQTT uptime:     24/24 cycles (100%)
MQTT publishes:   8/8 OK
SMS sent (while MQTT live):  +CMGS OK  -> +48792846824
SMS received (while MQTT live): 2/2  ("REPLY", "CONFIG TEST 123") decoded OK
Signal CSQ:      19-25 throughout
```

**Verdict: no firmware misbehavior for MQTT+SMS concurrency.** Send and receive both work
while an MQTT session is active. Just implement the app-level reconnect (§5) and SMS storage
cleanup (§7) for "works every time" robustness. Validate longer with
`mqtt_sms_test.py --minutes 60 ...`, and optionally a network-drop resilience run
(`AT+CFUN=4` → `AT+CFUN=1`).

---

## 9. EVB bring-up notes (hardware gotchas hit during setup)

- **Two USB-C ports:** upper = **DEVICE (USB PWR)** = the module's own USB (used by all
  scripts); lower = **EVB (FTDI)** = an FT4232 USB↔UART converter.
- **The EVB/FTDI port never enumerated** on this board — no FTDI device appears on the host
  (LED stays off) even with a known-good data cable, correct power, and the cable proven on
  the DEVICE port. The module/USB are healthy (DEVICE port works), so the **FTDI section of
  this EVB is faulty** — irrelevant for us since the native DEVICE-USB path is what we use.
- **Power-on:** apply power, then **briefly tap ON/OFF and release** (watch PWRMON come on
  and stay). Do **not** use the **AUTO_ON** switch — it holds ON/OFF permanently, which this
  module reads as a long-press = power-off, so it powers on then off after a few seconds.
  Power off cleanly with `AT#SHDN`.
- **Power supply:** 12 V / ≥2 A wall adapter via DCDC (source selector right), NO_LEAK jumper
  fitted. (≥1.25 A is the documented minimum.)
- **Voltage:** the LE910S1/R1 TLB is a **SMART TLB with AVS** — the 3.3/3.8 V selector is
  irrelevant (voltage auto-set).
