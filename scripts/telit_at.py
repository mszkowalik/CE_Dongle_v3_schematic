"""
Reusable AT transport for a Telit LE910R1/S1 over its BUILT-IN USB, using libusb
(pyusb) bulk endpoints — because on macOS the AT ports are vendor-specific (0xFF)
interfaces with no kernel driver, so no /dev/cu.usbmodem exists for pyserial.

Usage:
    from telit_at import AtPort
    with AtPort() as at:
        print(at.cmd("ATI"))
        print(at.cmd('AT#MQCONN=1,"id","",""', timeout=20))
        urc = at.drain(5)          # collect async URCs for 5 s
"""
import time
import usb.core
import usb.util

TELIT_VID = 0x1BC7
TERMINATORS = ("OK", "ERROR", "+CME ERROR", "+CMS ERROR", "NO CARRIER")


class AtPort:
    def __init__(self, vid=TELIT_VID, pid=None, iface=None):
        self.vid, self.pid, self.force_iface = vid, pid, iface
        self.dev = self.intf = self.ep_out = self.ep_in = None

    # ---- lifecycle -------------------------------------------------------
    def open(self):
        kw = {"idVendor": self.vid}
        if self.pid is not None:
            kw["idProduct"] = self.pid
        self.dev = usb.core.find(**kw)
        if self.dev is None:
            raise RuntimeError(f"No Telit device (VID={self.vid:#06x}). "
                               "Module powered on? DEVICE cable plugged in?")
        try:
            cfg = self.dev.get_active_configuration()
        except usb.core.USBError:
            self.dev.set_configuration()
            cfg = self.dev.get_active_configuration()

        candidates = []
        for intf in cfg:
            if self.force_iface is not None and intf.bInterfaceNumber != self.force_iface:
                continue
            if self.force_iface is None and intf.bInterfaceClass != 0xFF:
                continue
            ep_out = ep_in = None
            for ep in intf:
                if usb.util.endpoint_type(ep.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                    continue
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                    ep_out = ep
                else:
                    ep_in = ep
            if ep_out and ep_in:
                candidates.append((intf, ep_out, ep_in))

        for intf, ep_out, ep_in in candidates:
            ifnum = intf.bInterfaceNumber
            try:
                if self.dev.is_kernel_driver_active(ifnum):
                    self.dev.detach_kernel_driver(ifnum)
            except (NotImplementedError, usb.core.USBError):
                pass
            try:
                usb.util.claim_interface(self.dev, ifnum)
            except usb.core.USBError:
                continue
            self.intf, self.ep_out, self.ep_in = intf, ep_out, ep_in
            if self._probe_at():
                return self
            usb.util.release_interface(self.dev, ifnum)
        raise RuntimeError("No interface responded to AT.")

    def _probe_at(self):
        return "OK" in self.cmd("AT", timeout=2)

    def close(self):
        if self.dev and self.intf is not None:
            try:
                usb.util.release_interface(self.dev, self.intf.bInterfaceNumber)
            except usb.core.USBError:
                pass
        if self.dev:
            usb.util.dispose_resources(self.dev)

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # ---- io --------------------------------------------------------------
    def _flush(self):
        try:
            while True:
                self.ep_in.read(self.ep_in.wMaxPacketSize, timeout=80)
        except usb.core.USBError:
            pass

    def _read(self, ms):
        try:
            return bytes(self.ep_in.read(self.ep_in.wMaxPacketSize, timeout=ms))
        except usb.core.USBError:
            return b""

    def reopen(self, wait=2.0, attempts=12):
        """Re-acquire the device after a module reset / USB re-enumeration."""
        try:
            self.close()
        except Exception:
            pass
        self.dev = self.intf = self.ep_out = self.ep_in = None
        time.sleep(wait)
        for _ in range(attempts):
            try:
                return self.open()
            except Exception:
                time.sleep(1)
        raise RuntimeError("device did not re-enumerate after reset")

    def cmd(self, text, timeout=5, until=TERMINATORS, settle=0.05):
        """Send an AT command, read until a terminator or timeout (seconds).

        If the module resets (USB 'No such device'), reopen once and retry.
        """
        try:
            return self._cmd(text, timeout, until, settle)
        except usb.core.USBError as e:
            if e.errno != 19:  # not "No such device"
                raise
            print("  [transport] device reset detected — reopening...")
            self.reopen()
            return self._cmd(text, timeout, until, settle)

    def _cmd(self, text, timeout, until, settle):
        self._flush()
        self.ep_out.write((text + "\r").encode(), timeout=int(timeout * 1000))
        time.sleep(settle)
        buf = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            buf += self._read(300)
            txt = buf.decode("ascii", "replace")
            if any(("\r\n" + t in txt) or txt.strip().endswith(t) or txt.strip() == t
                   for t in until):
                break
        return buf.decode("ascii", "replace").strip()

    def write_raw(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.ep_out.write(data, timeout=5000)

    def drain(self, seconds):
        """Collect asynchronous URCs for a fixed window."""
        buf = bytearray()
        deadline = time.time() + seconds
        while time.time() < deadline:
            buf += self._read(300)
        return buf.decode("ascii", "replace").strip()


if __name__ == "__main__":
    # Smoke test: confirm the module is reachable over USB and print identity + SIM.
    with AtPort() as at:
        print(f"Found Telit device VID={at.dev.idVendor:#06x} PID={at.dev.idProduct:#06x}")
        print(f"AT command interface = #{at.intf.bInterfaceNumber}")
        print("USB interfaces:")
        for intf in at.dev.get_active_configuration():
            print(f"  if#{intf.bInterfaceNumber}  class={intf.bInterfaceClass:#04x}"
                  f"  sub={intf.bInterfaceSubClass:#04x}")
        for c in ["ATI", "AT+CGMM", "AT+CGMR", "AT+CGSN",
                  "AT#SIMDET?", "AT+CPIN?", "AT+CSQ", "AT+COPS?"]:
            print(f"\n>>> {c}\n{at.cmd(c)}")
