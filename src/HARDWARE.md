# CE Dongle v3 — Hardware Reference for Firmware Development

**Board revision:** CE_Dongle_V3 rev 1 (first prototypes, received 2026-07)
**Template name:** `CE_Dongle_V3_1` (bump suffix on any pin-affecting hardware change)
**Last verified:** 2026-07-03 against `exports/schematic/netlist/netlist.xml`
(regenerate exports with `scripts/export_schematic.sh`; that netlist is the source of truth —
net *names* can mislead, always check pin directions).

## MCU / memory

| | |
|---|---|
| MCU | **ESP32-C6** (U1, bare chip, VQFN-40, RISC-V single core 160 MHz) |
| Flash | **W25Q64JVSSIQ = 8 MB** external SPI NOR (U3), QIO, 80 MHz |
| PSRAM | none (C6 has no PSRAM support) |
| Crystal | 40 MHz (Y1) — the only frequency ESP32-C6 supports |
| USB | **native USB Serial/JTAG** on GPIO12/13 → USB-C J1. **The only programming interface** (U0 UART pins unconnected, no UART header, no USB-UART bridge chip) |
| Antennas | J5 = WiFi/BT SMA (ESP32), J4 = LTE SMA (module) |

## ESP32-C6 GPIO map (authoritative)

Template: `{"NAME":"CE_Dongle_V3_1","GPIO":[1,1,1,1,1312,640,608,256,1,32,257,1,0,0,0,1,1,1,1,1376,1,9408,9952,9440,0,0,0,0,0,0,0],"FLAG":0,"BASE":1}`
(31 entries = GPIO0…GPIO30, 1:1, no remapping on C6. `1` = user-selectable None, `0` = fixed None.)

| GPIO | Net (netlist export) | Function | Tasmota (code) |
|---|---|---|---|
| 0 | — | unconnected | None (1) |
| 1 | /LTE/PWR_MON | LTE PWRMON input, 1.8 V→3.3 V via TXS0104. High = module powered (I/O init done, NOT yet AT-ready) | None (1), Berry LTE driver |
| 2 | /LTE/UART_RX ⚠ | **ESP TX** → TXS0104 → module C103/TXD (module input). Net name is module-perspective! | None (1), Berry `serial(3, 2, 115200)` |
| 3 | /LTE/UART_TX ⚠ | **ESP RX** ← TXS0104 ← module C104/RXD (module output) | None (1), Berry serial |
| 4 | /MISC/TEMP | 1-Wire data, external DS18B20 via screw terminals (J2.2 = data, J2.1 = TEMP_VCC 3V3 via 120R, J3.1 = GND). On-board 5K1 pull-up (R49) + 120R series (R44) | DS18x20 (1312) |
| 5 | /MCU/I2C_SDA | I2C SDA — PCF8574 U5 @0x20, U6 @0x21 | I2C SDA1 (640) |
| 6 | /MCU/I2C_SCL | I2C SCL | I2C SCL1 (608) |
| 7 | /MCU/RY1_CTRL | Relay K1, **HW-INVERTED: HIGH = off, LOW/floating = ON** (374K pulldown on gate ⇒ relays energized during boot/reset/flash) | Relay_i1 (256) |
| 8 | /MCU/GPIO8 | Boot strap only, 10K pullup (R31). Keep unused | None (1) |
| 9 | /MCU/BUTTON | SW2 user button to GND, 10K pullup (R22), 100n debounce. Also BOOT strap: held low at reset = USB download mode | Button1 (32) |
| 10 | /MCU/RY2_CTRL | Relay K2, same inverted driver | Relay_i2 (257) |
| 11 | /LTE/SHUTDOWN | LTE emergency kill: drive HIGH ≥18 ms → Q7 → HW_SHUTDOWN_N low. Emergency ONLY (skips flash finalization). Base pulldown = inactive at boot | None (1), Berry |
| 12/13 | USB D−/D+ | native USB Serial/JTAG — never reassign | fixed None (0) |
| 14 | — | not bonded on this package | fixed None (0) |
| 15 | — | unconnected | None (1) |
| 16/17 | — | U0TXD/U0RXD, unconnected | None (1) |
| 18 | /LTE/PWR_CYCLE | LTE ON_OFF_N via Q8 (inverter): drive HIGH = ON_OFF_N low. Pulse to power module on/off (see LTE section). Base pulldown = inactive at boot | None (1), Berry |
| 19 | /MCU/LED_DATA | WS2812 chain ×3: U17 → U18 → U20 (3V3 supply) | WS2812 bus1 (1376), `WS2812_LEDS 3` |
| 20 | — | unconnected | None (1) |
| 21 | /MCU/MODBUS_TX | SP3485 DI | ModBR TX (9408) |
| 22 | /MB_TX_EN | SP3485 DE + ~RE (also lights D2 activity LED). Driven by TasmotaModbus: LOW idle, HIGH during transmit. Also wired to J7 pin 12 | ModBR Tx En (9952) |
| 23 | /MCU/MODBUS_RX | SP3485 RO | ModBR RX (9440) |
| 24–30 | SPI flash | QSPI to W25Q64 | fixed None (0) |

⚠ = LTE-sheet net names are from the **module's** perspective; MCU-sheet hierarchical labels are ESP-perspective. Trust pin directions, not names.

## Relays (K1/K2 — Omron G6K-2F-Y 5VDC)

- Coils on 5V0 rail (~21 mA), two-stage NMOS driver (SI2308A ×2 per relay), flyback SS34.
- **Inverted control** (see `src/CHANGELOG.md` for rationale — 5 V gate drive wanted, inversion accepted):
  GPIO LOW/floating = coil energized. Divider 10K→5V0 / 100K→GND holds coil-FET gate at ~4.5 V when
  the first stage is off. **Relays are ON while the ESP32 is in reset/bootloader/being flashed.**
  Firmware must drive GPIO7/GPIO10 HIGH ASAP; Tasmota `Relay_i` + `PowerOnState 0` handles it post-init.
- D5/D8 LEDs indicate coil state (hardware-wired). Contacts: only COM+NO used, 24 V AC contactor loads.
- U19 (5V0 buck) EN is clamped by a 5V1 zener (D12) and floats otherwise → **always enabled**, not firmware-controllable.

## RS-485 / Modbus + input MUX

- SP3485 (U2, 3V3) — A/B lines come from the TMUX system; 120R fixed termination (R1) across A/B.
  Also exposed directly on J10 (pin1 = A, pin2 = B).
- Tasmota **Modbus Bridge** is enabled (`USE_MODBUS_BRIDGE` + `_TCP` in `user_config_override.h`):
  commands `ModbusSend`, `ModbusBaudrate`, `ModbusSerialConfig`, `ModbusTCPStart` etc.
- **MUX purpose:** industrial Modbus RJ45 (J7) pinouts vary by vendor; any of J7 pins 1–8 can be routed
  to bus line A or B. Max 2 muxes per line (MUX0/MUX1 → OUT_A, MUX2/MUX3 → OUT_B).
- **Topology (verified 2026-07-03):** J7 pin N → TMUX channel N−1 (S1…S8 pads = channels 0…7) on ALL
  four TMUX1208 (5 V supply). U7 = MUX0 and U8 = MUX1 output to OUT_A → SP3485 A;
  U9 = MUX2 and U10 = MUX3 → OUT_B → SP3485 B.
- **PCF8574 mapping (both expanders identical layout):** U5 @ **0x20**: P0=MUX0_EN, P1/P2/P3=MUX0 S0/S1/S2,
  P4=MUX1_EN, P5/P6/P7=MUX1 S0/S1/S2. U6 @ **0x21**: P0=MUX3_EN, P1–P3=MUX3 S0–S2, P4=MUX2_EN, P5–P7=MUX2 S0–S2.
- **No external pull resistors on any PCF8574 output or TMUX EN/select line** (verified against the
  production netlist 2026-07-04). The PCF8574 quasi-bidirectional output sources only **~50 µA** when
  high and sinks strongly when low, so it cannot overcome any meaningful external resistor:
  a **pulldown** on EN/select prevents the pin from reaching a valid high (why pulldowns were never an
  option), and the **10K pull-ups R37–R40** that old proto boards (`e6876d4`/`22bfcc2`) placed on the
  four EN nets were too low for the PCF8574 to pull down cleanly. Those pull-ups were removed from
  commit `6797124` on; the production board has none, so the PCF8574 has sole authority over
  EN/select — correct.
- TMUX1208 **EN is active-HIGH**. PCF8574 pins power up weakly HIGH ⇒ **all muxes enabled (all select
  bits =1 ⇒ channel 8 / J7 pin 8 selected) from power-on until the Berry driver writes 0x00**. Since
  MUX0/1 → OUT_A and MUX2/3 → OUT_B, that briefly ties J7 pin 8 to both A and B. Inherent to
  PCF8574 float-high + active-high EN (not related to the EN-pull-up removal); the ESP32's own SP3485
  driver is disabled at boot (GPIO22/DE low), so the board doesn't drive the bus during that window.
- Berry driver: `firmware/fs/pcf8574_mux_driver.be` (MUX0/MUX3 bit order fixed 2026-07-03; commands
  `MuxRoute`/`MuxDisable`/`MuxStatus`/`MuxDebug`). `firmware/MUX_REFERENCE.md` is STALE — see its warning banner.

## LTE modem — Telit LE910R1-EU (U16)

Power/control (all module logic is **1.8 V, abs-max 2.16 V** — everything goes through TXS0104 U15 or NPN inverters):

| Signal | ESP32 | Polarity / timing |
|---|---|---|
| PWR_CYCLE → ON_OFF_N | GPIO18 | drive HIGH = ON_OFF_N low. **ON: hold ≥5 s** then release; **OFF: hold ≥3 s**. Module AT-ready up to **~25–30 s** after ON assertion. (Timings from LE910S1 sibling HW guide — R1's own PDF is login-gated; EVB testing showed a short tap also powers on. Verify against Telit doc `1VV0301715` R1 edition when available.) |
| PWR_MON ← VAUX/PWRMON | GPIO1 | HIGH (1.8 V, shifted to 3.3 V) = module powered. Goes low only after full shutdown (>15 s). **Never cut VBATT while high.** |
| SHUTDOWN → HW_SHUTDOWN_N | GPIO11 | drive HIGH ≥18 ms = unconditional kill. **Emergency only** (loses NV data). |
| UART | GPIO2 = ESP TX, GPIO3 = ESP RX | **115200 8N1 fixed** (no autobaud). Factory default is HW flow control: board has RTS grounded / CTS unconnected (Telit's documented minimum wiring) → issue `AT&K0` + `AT&W` early, else large responses stall. |

- VBATT = 3V8 rail (U14 buck, dedicated). SIM slot 1 only (J9); SIMIN1 strapped to SIM_VCC = "always present"
  (`AT#SIMDET=1` is the fallback if SIM reads absent). **Test SIM PIN = 1111** — always check `AT+CPIN?` +
  `AT#PCT` (remaining attempts) before sending a PIN; 3 wrong = PUK.
- Module status LED D3 driven by module GPIO_1 (LED_STAT) via Q18 — no ESP involvement.
- DTR is not wired → module may enter UART sleep after idle; if AT stops answering after idle periods,
  suspect this first (disable module power saving).
- TXS0104's A-side rail/OE = module VDD_IO ⇒ level shifter is Hi-Z while the module is off (no back-powering).
- GNSS not supported on -EU variant; bands B1/B3/B7/B8/B20/B28 + 2G fallback.
- **Validated AT knowledge base** (EVB tests, fw M0Q.010001, MQTT+SMS): `scripts/README.md`. Highlights:
  `AT#MQWCFG` **resets the module** — never use; `AT#SGACT=1,1` must precede `AT#MQCFG`; `AT#MQSUB` takes no
  qos arg; no MQTT auto-reconnect (poll `AT#MQCONN?`); delete received SMS (`AT+CMGD`) or the 25-slot SIM store fills.
- Product architecture: **Berry AT-command driver** (no PPP). WiFi is primary MQTT transport; Berry reroutes
  MQTT over LTE transparently when WiFi is down.

## Power

- Inputs: VEXT 10–28 V (screw terminals) or USB-C 5 V; PMOS OR-ing (Q1/Q2) → VSUPPLY.
- Bucks (TPS54302): U4 → 3V3 (always on), U14 → 3V8 (LTE VBATT, always on), U19 → 5V0 (relays; always on,
  EN not controllable). D11 ORs USB VBUS into 5V0 (USB-only ⇒ 5V0 ≈ 4.3–4.5 V — relay hold margin unverified).
- USB-only operation: LTE TX bursts on 3V8 may exceed USB budget — power via VEXT for LTE testing.

## Buttons / straps / boot behavior

- SW1 = reset (CHIP_PU to GND). SW2 = GPIO9 user button = BOOT strap.
- Straps: GPIO8 pulled up (R31 10K), GPIO9 pulled up (R22 10K) → normal SPI flash boot. GPIO9 low at
  reset = USB download mode ROM.
- During reset/boot: relays ON (inverted drivers), all RS-485 muxes enabled (PCF8574 float-high), LTE
  control lines inactive (base pulldowns) — module stays off until firmware pulses PWR_CYCLE.

## Firmware / BSP (Tasmota fork, submodule `firmware/`)

- Fork branch `ce-dongle-v3`, upstream merged: **v15.5.0**. Custom bits:
  - `boards/esp32c6-ce_dongle_v3.json` — 8 MB QIO 80 MHz, `-DARDUINO_USB_MODE=1 -DUSE_USB_CDC_CONSOLE`,
    safeboot extra image @0x10000.
  - `partitions/esp32c6_partition_8MB_app3904k_fs3392k.csv` — nvs 0x9000, otadata 0xE000,
    safeboot 0x10000 (0xD0000), app0 0xE0000 (3904K), LittleFS 0x4B0000 (3392K, ends exactly at 8 MB).
  - `[env:tasmota32c6-CE_DONGLE_V3]` in `platformio_tasmota_env32.ini`.
  - `data_dir = fs` in `platformio.ini` → git-tracked LittleFS payload (`fs/autoexec.be`, MUX driver);
    flash it with `pio run -e tasmota32c6-CE_DONGLE_V3 -t uploadfs`.
  - Template + feature defines in `tasmota/user_config_override.h` (template `CE_Dongle_V3_1`; also in
    `src/tasmota_template_CE_Dongle_V3_1.json`). ⚠ WiFi credentials are currently hardcoded there.
- Console = native USB Serial/JTAG (HWCDC), fixed 115200. Appears as `/dev/cu.usbmodem*` (VID:PID 303a:1001).

## Flashing & debugging (macOS, no boot button needed)

`pio` lives at `~/.platformio/penv/bin/pio` (add to PATH). esptool: `/opt/homebrew/bin/esptool.py` (4.8.1).

- **No-button flashing works by design:** the C6's USB Serial/JTAG is fixed-function hardware; esptool
  detects PID 0x1001 and issues the DTR/RTS sequence that latches the download-mode flag and resets the
  chip (verified against C6 TRM ch. 32 + esptool source). Works while Tasmota runs. Fails only if firmware
  reconfigures GPIO12/13, sleeps deeply, or crash-loops — then hold SW2 (GPIO9) while plugging USB.
- Build: `pio run -e tasmota32c6-CE_DONGLE_V3`
- First/full flash (merged factory image at offset **0x0**):
  `esptool.py --chip esp32c6 --port /dev/cu.usbmodem* write_flash 0x0 build_output/firmware/tasmota32c6-CE_DONGLE_V3.factory.bin`
  (or `pio run -e tasmota32c6-CE_DONGLE_V3 -t upload`)
- FS image (Berry MUX driver + autoexec): `pio run -e tasmota32c6-CE_DONGLE_V3 -t uploadfs`
- Monitor without resetting the chip: `pio device monitor -p /dev/cu.usbmodem* -b 115200 --rts 0 --dtr 0`
  (a port open/close that leaves RTS=1/DTR=0 resets the chip — that's the C6 hardware, not a bug).
- OTA dev loop once on WiFi (app bin only, never factory.bin):
  `curl -f -F "file=@build_output/firmware/tasmota32c6-CE_DONGLE_V3.bin" http://<ip>/u2`
  (safeboot partition handles the swap; bricked app ⇒ safeboot web UI still serves /u2).
- On-chip debug (same USB cable, works alongside the CDC console): add `debug_tool = esp-builtin` to the env
  in `platformio_override.ini`, then `pio debug -e tasmota32c6-CE_DONGLE_V3` (auto-installs
  openocd-esp32 ≥0.12 + riscv32-esp-elf-gdb; the pre-installed tool-openocd-esp32 v0.11 is too old for C6).
  Manual: `openocd -f board/esp32c6-builtin.cfg` + `riscv32-esp-elf-gdb firmware.elf -ex 'target extended-remote :3333'`.
  4 HW breakpoints; OpenOCD suspends the watchdogs at halt; expect WiFi to drop while halted.
- **While flashing, remember: relays are energized and RS-485 muxes are enabled** (see boot behavior).
  Don't flash with mains-connected contactors wired.
