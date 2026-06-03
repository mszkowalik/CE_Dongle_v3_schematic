#!/usr/bin/env python3
"""
Read and decode all SMS from the Telit LE910R1 over its built-in USB.

Reads in PDU mode (AT+CMGF=0) and decodes GSM-7 / UCS2 / 8-bit data codings,
reassembling concatenated (multipart) messages. Works around the EVB SIMIN
quirk by forcing AT#SIMDET=1.

Usage:
    scripts/.venv/bin/python scripts/read_sms.py            # SIM must be READY
    scripts/.venv/bin/python scripts/read_sms.py --pin 1111 # enter PIN first
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telit_at import AtPort  # noqa: E402

GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = {0x0a: '\f', 0x14: '^', 0x28: '{', 0x29: '}', 0x2f: '\\',
            0x3c: '[', 0x3d: '~', 0x3e: ']', 0x40: '|', 0x65: '€'}


def septets_from_bits(ud, start_bit, n):
    out = []
    for k in range(n):
        val = 0
        for b in range(7):
            p = start_bit + k * 7 + b
            if p // 8 < len(ud):
                val |= ((ud[p // 8] >> (p % 8)) & 1) << b
        out.append(val)
    return out


def gsm7_to_text(septets):
    out, i = [], 0
    while i < len(septets):
        s = septets[i]
        if s == 0x1b and i + 1 < len(septets):
            i += 1
            out.append(GSM7_EXT.get(septets[i], ''))
        else:
            out.append(GSM7[s] if s < len(GSM7) else '?')
        i += 1
    return ''.join(out)


def decode_addr(octs, i):
    """Decode an address field at octs[i]; return (string, next_i)."""
    alen = octs[i]                      # length in semi-octets (digits)
    toa = octs[i + 1]
    nbytes = (alen + 1) // 2
    body = octs[i + 2:i + 2 + nbytes]
    nxt = i + 2 + nbytes
    if (toa & 0x70) == 0x50:            # alphanumeric -> GSM7 packed
        nsept = (alen * 4) // 7
        txt = gsm7_to_text(septets_from_bits(bytes(body), 0, nsept))
        return txt, nxt
    digits = ""
    for b in body:
        digits += str(b & 0x0f) + str(b >> 4)
    digits = digits[:alen].replace("f", "")
    return ("+" + digits if (toa & 0x70) == 0x10 else digits), nxt


def decode_scts(octs, i):
    def sd(b):
        return (b & 0x0f) * 10 + (b >> 4)
    y, mo, d, h, mi, s = (sd(octs[i + k]) for k in range(6))
    return f"20{y:02d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"


def decode_pdu(pdu_hex):
    o = bytes.fromhex(pdu_hex.strip())
    i = 0
    smsc_len = o[i]; i += 1 + smsc_len
    first = o[i]; i += 1
    udhi = bool(first & 0x40)
    sender, i = decode_addr(o, i)
    i += 1                              # PID
    dcs = o[i]; i += 1
    scts = decode_scts(o, i); i += 7
    udl = o[i]; i += 1
    ud = o[i:]
    # UDH (concatenation)
    udh_octs = 0
    ref = part = parts = None
    if udhi:
        udhl = ud[0]
        udh = ud[1:1 + udhl]
        udh_octs = udhl + 1
        k = 0
        while k < len(udh):
            iei, ielen = udh[k], udh[k + 1]
            val = udh[k + 2:k + 2 + ielen]
            if iei == 0x00:            # concat, 8-bit ref
                ref, parts, part = val[0], val[1], val[2]
            elif iei == 0x08:          # concat, 16-bit ref
                ref, parts, part = (val[0] << 8) | val[1], val[2], val[3]
            k += 2 + ielen
    coding = dcs & 0x0c
    if coding == 0x00:                 # GSM-7
        fill = (7 - (udh_octs * 8) % 7) % 7
        start = udh_octs * 8 + fill
        n = udl - ((udh_octs * 8 + 6) // 7)
        text = gsm7_to_text(septets_from_bits(ud, start, max(n, 0)))
        binary = False
    elif coding == 0x08:               # UCS2
        text = ud[udh_octs:].decode("utf-16-be", "replace")
        binary = False
    else:                              # 8-bit (binary)
        text = ud[udh_octs:].hex()
        binary = True
    return dict(sender=sender, scts=scts, text=text, binary=binary,
                ref=ref, part=part, parts=parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin", default=None)
    ap.add_argument("--mem", default="SM", choices=["SM", "ME"])
    args = ap.parse_args()

    with AtPort() as at:
        at.cmd("AT+CMEE=2")
        at.cmd("AT#SIMDET=1")                       # bypass flaky SIMIN
        st = at.cmd("AT+CPIN?")
        if "SIM PIN" in st and args.pin:
            print("Entering PIN...")
            at.cmd(f'AT+CPIN="{args.pin}"', timeout=15)
            time.sleep(4); st = at.cmd("AT+CPIN?")
        if "READY" not in st:
            sys.exit(f"SIM not READY: {st.strip()}")
        print("ICCID:", at.cmd("AT#CCID").split(":")[-1].split("OK")[0].strip())
        at.cmd("AT+CMGF=0")                          # PDU mode
        at.cmd(f'AT+CPMS="{args.mem}"')
        raw = at.cmd('AT+CMGL=4', timeout=30)        # all messages, PDU

    # parse "+CMGL: idx,stat,alpha,len\r\n<pdu>"
    entries = re.findall(r"\+CMGL:\s*(\d+),(\d+),[^,]*,\d+\s*\r?\n([0-9A-Fa-f]+)", raw)
    print(f"\n{len(entries)} message(s) in {args.mem}\n" + "=" * 60)
    parts_buf = {}
    singles = []
    for idx, stat, pdu in entries:
        try:
            d = decode_pdu(pdu)
        except Exception as e:
            print(f"[{idx}] decode error: {e}"); continue
        d["idx"] = idx
        if d["ref"] is not None:
            parts_buf.setdefault((d["sender"], d["ref"]), {})[d["part"]] = d
        else:
            singles.append(d)

    def show(d, label):
        kind = " [binary]" if d["binary"] else ""
        print(f"\n#{label}  from {d['sender']}  {d['scts']}{kind}")
        print("   " + (d["text"] if not d["binary"]
                       else d["text"][:120] + ("..." if len(d["text"]) > 120 else "")))

    for d in singles:
        show(d, d["idx"])
    for (sender, ref), pmap in parts_buf.items():
        merged = "".join(pmap[k]["text"] for k in sorted(pmap))
        any_d = next(iter(pmap.values()))
        label = "+".join(pmap[k]["idx"] for k in sorted(pmap))
        binflag = " [binary]" if any_d["binary"] else ""
        print(f"\n#{label}  from {sender}  {any_d['scts']}  (concatenated {len(pmap)}/{any_d['parts']}){binflag}")
        print("   " + (merged if not any_d["binary"] else merged[:160] + "..."))


if __name__ == "__main__":
    main()
