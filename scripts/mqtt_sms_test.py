#!/usr/bin/env python3
"""
Endurance / concurrency test for the real use case:
  MQTT pub/sub (data) + SMS send/receive (config) running AT THE SAME TIME over LTE.

Verifies:
  - MQTT stays connected (auto-reconnects if it drops, and counts drops)
  - SMS can be SENT while MQTT is active
  - SMS can be RECEIVED while MQTT is active (decoded, incl. concatenated)
  - optional: delete received SMS after reading (SIM storage is only ~25 slots!)

Examples:
  # 10 min, publish every 15s, no SMS sending, just watch RX + stability:
  scripts/.venv/bin/python scripts/mqtt_sms_test.py --minutes 10

  # 30 min, send a test SMS every 5 min to your phone, delete RX after reading:
  scripts/.venv/bin/python scripts/mqtt_sms_test.py --minutes 30 \
      --sms-interval 300 --dest +48792846824 --delete-rx
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telit_at import AtPort        # noqa: E402
from read_sms import decode_pdu    # noqa: E402

HOST, PORT = "test.mosquitto.org", 1883
TOPIC = "telit/le910r1/endurance"


def mqtt_connected(at):
    return bool(re.search(r"#MQCONN:\s*1,1", at.cmd("AT#MQCONN?")))


def mqtt_connect(at, clientid):
    at.cmd("AT#SGACT=1,1", timeout=20)
    at.cmd("AT#MQDISC=1")
    at.cmd("AT#MQEN=1,1")
    at.cmd(f'AT#MQCFG=1,"{HOST}",{PORT},1')
    r = at.cmd(f'AT#MQCONN=1,"{clientid}","",""', timeout=40)
    if "OK" in r and mqtt_connected(at):
        at.cmd(f'AT#MQSUB=1,"{TOPIC}"')
        return True
    return False


def send_sms(at, dest, msg):
    at.cmd("AT+CMGF=1"); at.cmd('AT+CSCS="GSM"')
    at._flush(); at.ep_out.write(f'AT+CMGS="{dest}"\r'.encode())
    buf = b""; t = time.time() + 6
    while time.time() < t:
        buf += at._read(300)
        if b">" in buf:
            break
    if b">" not in buf:
        return False
    at.ep_out.write(msg.encode() + b"\x1a")
    buf = b""; t = time.time() + 60
    while time.time() < t:
        buf += at._read(400)
        if b"+CMGS" in buf or b"ERROR" in buf:
            break
    return b"+CMGS" in buf


def read_new_sms(at, delete=False):
    """Return list of (idx, sender, scts, text) for REC-UNREAD msgs (PDU mode)."""
    at.cmd("AT+CMGF=0")
    raw = at.cmd("AT+CMGL=4", timeout=30)   # 4 = ALL in PDU mode
    out = []
    for idx, stat, pdu in re.findall(
            r"\+CMGL:\s*(\d+),(\d+),[^,]*,\d+\s*\r?\n([0-9A-Fa-f]+)", raw):
        if stat != "0":                     # 0 = REC UNREAD
            continue
        try:
            d = decode_pdu(pdu)
            out.append((idx, d["sender"], d["scts"],
                        d["text"] if not d["binary"] else "[binary] " + d["text"][:60]))
        except Exception as e:
            out.append((idx, "?", "?", f"[decode error: {e}]"))
        if delete:
            at.cmd(f"AT+CMGD={idx}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--pub-interval", type=int, default=15, help="seconds between MQTT publishes")
    ap.add_argument("--sms-interval", type=int, default=0, help="seconds between test SMS sends (0=off)")
    ap.add_argument("--dest", default=None, help="destination number for --sms-interval")
    ap.add_argument("--delete-rx", action="store_true", help="delete received SMS after reading")
    ap.add_argument("--pin", default=None)
    args = ap.parse_args()

    stats = dict(cycles=0, mqtt_up=0, pub_ok=0, pub_try=0, reconnects=0,
                 sms_sent=0, sms_send_try=0, sms_rx=0)
    with AtPort() as at:
        at.cmd("AT+CMEE=2"); at.cmd("AT#SIMDET=1")
        st = at.cmd("AT+CPIN?")
        if "SIM PIN" in st and args.pin:
            at.cmd(f'AT+CPIN="{args.pin}"', timeout=15); time.sleep(4)
        at.cmd("AT+CNMI=2,1")
        cid = "le910r1-endurance"
        print(f"Connecting MQTT {HOST}:{PORT} ...")
        if not mqtt_connect(at, cid):
            sys.exit("initial MQTT connect failed")
        print(f"Running {args.minutes} min  (pub every {args.pub_interval}s"
              + (f", SMS every {args.sms_interval}s -> {args.dest}" if args.sms_interval and args.dest else "")
              + (", delete RX" if args.delete_rx else "") + ")\n")

        end = time.time() + args.minutes * 60
        last_pub = last_sms = 0
        while time.time() < end:
            now = time.time()
            up = mqtt_connected(at)
            stats["cycles"] += 1
            if not up:                       # auto-reconnect
                print("  ! MQTT down -> reconnecting")
                if mqtt_connect(at, cid):
                    stats["reconnects"] += 1; up = True
            stats["mqtt_up"] += up
            csq = re.search(r"\+CSQ:\s*(\d+)", at.cmd("AT+CSQ"))
            rssi = csq.group(1) if csq else "?"

            if now - last_pub >= args.pub_interval:
                last_pub = now; stats["pub_try"] += 1
                if "OK" in at.cmd(f'AT#MQPUBS=1,"{TOPIC}",0,1,"keepalive-{stats["cycles"]}"', 12):
                    stats["pub_ok"] += 1
                at.drain(2)

            sent = ""
            if args.sms_interval and args.dest and now - last_sms >= args.sms_interval:
                last_sms = now; stats["sms_send_try"] += 1
                ok = send_sms(at, args.dest, f"LE910R1 endurance ping #{stats['sms_send_try']}")
                stats["sms_sent"] += ok; sent = f" SMS_sent={'OK' if ok else 'FAIL'}"

            for idx, snd, scts, txt in read_new_sms(at, delete=args.delete_rx):
                stats["sms_rx"] += 1
                print(f"  *** RX SMS #{idx} from {snd} {scts}: {txt!r}")

            print(f"  t+{int(now-(end-args.minutes*60)):>4}s MQTT={'UP' if up else 'DOWN'} CSQ={rssi}{sent}")
            time.sleep(max(1, args.pub_interval))

    c = stats["cycles"] or 1
    print("\n" + "=" * 56 + "\nENDURANCE SUMMARY\n" + "=" * 56)
    print(f"  duration cycles    : {stats['cycles']}")
    print(f"  MQTT uptime        : {stats['mqtt_up']}/{c}  ({100*stats['mqtt_up']//c}%)")
    print(f"  MQTT reconnects    : {stats['reconnects']}")
    print(f"  MQTT publishes     : {stats['pub_ok']}/{stats['pub_try']}")
    print(f"  SMS sent           : {stats['sms_sent']}/{stats['sms_send_try']}")
    print(f"  SMS received       : {stats['sms_rx']}")


if __name__ == "__main__":
    main()
