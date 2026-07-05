# CE Dongle v3 — Hardware Design Changelog

Rationale and quirks of the PCB design that the schematic alone does not explain.
See also `HARDWARE.md` (board reference for firmware development) in this directory.

## Board revision: CE_Dongle_V3 rev 1 (first prototypes, received 2026-07)

### Relay driver inversion (deliberate trade-off, revisit next revision)
The relay coil FETs (Q14/Q15, SI2308A) are driven through a second NMOS stage
(Q16/Q17) with a 10K pull-up to 5V0 + 100K to GND on the coil-FET gate.
**Reason:** SI2308A drain current at Vgs = 3.3 V was judged too low, so the gate is
driven from 5 V via the extra stage. **Side effect (accepted, not desired):** control
is inverted — GPIO7/GPIO10 HIGH = relay OFF, LOW **or floating** = relay ON, so both
relays are energized during boot, reset, and flashing.
**Firmware consequence:** Tasmota template must use inverted relays (`Relay1_i`/`Relay2_i`).
**Next revision:** consider going back to direct 3.3 V single-FET drive (verify SI2308A or
substitute FET Rds(on)/Id at Vgs = 3.3 V against the ~21 mA G6K coil current) and remove
the inversion.

### 2026-06 schematic cleanup (commit 8d9b8dd)
- GPIO9 (BOOT/user button) strap changed from pulldown to **pullup** (R22 10K → 3V3):
  GPIO9 must be HIGH at reset for normal SPI boot; button SW2 pulls it LOW (also enters
  USB download mode if held during reset).

### Rev 1 boot-window issues (review for next revision)
- Relays energized during boot/reset/flash (inverted drivers, see above).
- All four RS-485 TMUX1208 are **enabled** from power-on until Berry runs: PCF8574 pins float
  weakly HIGH and TMUX EN is active-HIGH, so multiple J7 inputs are briefly connected to the
  bus lines. Pulldowns on EN can't fix it — the PCF8574 sources only **~50 µA** when high and
  couldn't overcome a pulldown to reach a valid logic 1 (this is also why the old 10K EN
  pull-ups R37–R40 were removed: too low for the PCF8574 to pull down cleanly). Eliminating the
  boot window would need a drive-scheme change (e.g. an actively-driven buffer/latch on EN).

### EN pull-ups removed (history, verified 2026-07-04)
Earliest proto boards in this repo (`e6876d4`, `22bfcc2`) had 10K pull-ups R37–R40 on the four
TMUX EN nets. The PCF8574's ~50 µA high-side drive can't cleanly pull those down, so it couldn't
reliably control the muxes. Removed from commit `6797124` onward; the production board (`29e5414`)
carries no pull resistors on any PCF8574 output or TMUX EN/select line — confirmed by netlist sweep.

### Facts that are easy to misread from the schematic
- **U19 (5V0 buck) EN** connects only to D12 (5V1 zener to GND) → always enabled; the
  5V0 rail is NOT firmware-controllable.
- **D3 LED** = LTE module status (module GPIO_1/LED_STAT via Q18), not a power LED.
- **D5/D8 LEDs** = relay coil state indicators, hardware-wired across the coil low side.
- **D2 LED** = RS-485 TX activity, hardware-wired to MB_TX_EN.
- **SIMIN1** is tied to SIM_VCC via R51 (no card-detect switch on J9): the module always
  sees "SIM present". If the SIM ever reads as absent, force `AT#SIMDET=1`.
- **LTE UART naming:** MCU-sheet labels are ESP-perspective (GPIO2 = ESP TX), but exported
  net names on the LTE sheet are module-perspective (`UART_RX` net is on the ESP TX pin).
  Electrical truth: GPIO2 → TXS0104 → module C103/TXD (input); GPIO3 ← C104/RXD (output).
