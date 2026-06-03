#!/usr/bin/env python3
"""
Exercise the full MQTT AT-command set on the Telit LE910R1-EU over its built-in
USB (libusb), plaintext and over TLS/SSL.

Brokers (--broker):
  mosquitto       test.mosquitto.org:1883  plaintext, anonymous   (default)
  mosquitto-ssl   test.mosquitto.org:8883  TLS (Verify None), anonymous
  env             values from .env (MQTT_ADDRESS/PORT/USERNAME/PASSWORD)

Covers: SGACT, SSLEN/SSLSECCFG/SSLSECCFG2/SSLSECDATA, MQEN, MQCFG, MQCFG2,
MQTCFG, MQWCFG, MQCONN, MQSUB, MQPUBS, MQPUBSEXT, #MQRING (URC), MQREAD,
MQUNS, MQDISC.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telit_at import AtPort  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INST = CID = SSID = 1
CTRLZ = b"\x1a"
results = []


def load_env():
    env = {}
    for path in (os.path.join(ROOT, ".env"), os.path.join(ROOT, "scripts", ".env")):
        if os.path.exists(path):
            for line in open(path):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def record(label, ok, detail=""):
    results.append((label, ok, str(detail).replace("\n", " | ")[:90]))


def step(at, label, command, timeout=10, expect_ok=True):
    print(f"\n>>> {command}")
    resp = at.cmd(command, timeout=timeout)
    print(resp if resp else "(no response)")
    ok = ("OK" in resp) if expect_ok else True
    record(label, ok, resp)
    return resp


def wait_ring(at, seconds=8):
    print(f"\n... waiting up to {seconds}s for #MQRING ...")
    txt = at.drain(seconds)
    if txt:
        print(txt)
    m = re.search(r"#MQRING:\s*\d+,(\d+),\"?([^\",]+)\"?,(\d+)", txt)
    return (int(m.group(1)), m.group(2), int(m.group(3))) if m else None


def setup_ssl(at, auth_mode, ca_pem):
    step(at, "SSLEN enable", f"AT#SSLEN={SSID},1")
    step(at, "SSLSECCFG2 TLSv1.2", f"AT#SSLSECCFG2={SSID},2", expect_ok=False)
    fmt = ",1" if auth_mode else ""          # PEM if verifying
    step(at, "SSLSECCFG auth", f"AT#SSLSECCFG={SSID},0,{auth_mode}{fmt}")
    if auth_mode and ca_pem:
        print(f"\n>>> AT#SSLSECDATA={SSID},1,1,{len(ca_pem)}  (store CA cert)")
        at._flush()
        at.write_raw(f"AT#SSLSECDATA={SSID},1,1,{len(ca_pem)}\r")
        time.sleep(0.4)
        prompt = at.drain(3)
        print(prompt)
        if ">" in prompt:
            at.write_raw(ca_pem.encode() + CTRLZ)
            res = at.drain(8)
            print(res)
            record("SSLSECDATA store CA", "OK" in res, res)
        else:
            record("SSLSECDATA store CA", False, "no '>' prompt")


def main():
    env = load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", choices=["mosquitto", "mosquitto-ssl", "env"],
                    default="mosquitto")
    ap.add_argument("--auth-mode", type=int, default=0, choices=[0, 1, 2],
                    help="SSL auth: 0=Verify None, 1=verify server (needs --ca)")
    ap.add_argument("--ca", default=None, help="path to CA cert (PEM) for auth-mode>=1")
    ap.add_argument("--test-willcfg", action="store_true",
                    help="also test AT#MQWCFG (resets module on M0Q.010001 fw)")
    args = ap.parse_args()

    if args.broker == "mosquitto":
        host, port, ssl, user, pw = "test.mosquitto.org", 1883, False, "", ""
    elif args.broker == "mosquitto-ssl":
        host, port, ssl, user, pw = "test.mosquitto.org", 8883, True, "", ""
    else:  # env
        host = env.get("MQTT_ADDRESS", "")
        port = int(env.get("MQTT_PORT", "8883"))
        user = env.get("MQTT_USERNAME", "")
        pw = env.get("MQTT_PASSWORD", "")
        ssl = port == 8883
        if not host:
            sys.exit("env broker selected but MQTT_ADDRESS missing in .env")
        if not pw:
            print("WARNING: MQTT_PASSWORD empty in .env — auth will likely fail.\n")

    ca_pem = open(args.ca).read() if args.ca else None
    mode = "TLS/SSL" if ssl else "plaintext"
    print(f"=== MQTT test: {host}:{port} ({mode}) user={user or '(anon)'} ===")

    with AtPort() as at:
        imei = re.sub(r"\D", "", at.cmd("AT+CGSN")) or "000000"
        suffix = imei[-6:]
        clientid = f"le910r1-{suffix}"
        topic = f"telit/le910r1/{suffix}/test"
        print(f"clientID={clientid}  topic={topic}")

        at.cmd("AT+CMEE=2")  # verbose error messages
        # PDP context (persists; repeat returns harmless ERROR)
        step(at, "SGACT activate", f"AT#SGACT={CID},1", timeout=20, expect_ok=False)
        # clean any prior MQTT session/state, then (re)enable the client
        at.cmd(f"AT#MQDISC={INST}")

        if ssl:
            setup_ssl(at, args.auth_mode, ca_pem)

        at.cmd(f"AT#MQEN={INST},1")
        en = step(at, "MQEN enabled", "AT#MQEN?")
        record("MQEN instance enabled", bool(re.search(rf"#MQEN:\s*{INST},1", en)))
        if ssl:
            # M0Q.010001 fw has NO <sslInstance> field (see AT#MQCFG=?); just sslEn=1.
            # TLS settings come from SSL socket SSId 1 configured via #SSLSECCFG.
            cfg = f'AT#MQCFG={INST},"{host}",{port},{CID},1'
        else:
            cfg = f'AT#MQCFG={INST},"{host}",{port},{CID}'
        if "OK" not in step(at, "MQCFG broker", cfg):
            record("ABORT", False, "MQCFG failed"); return summary()
        step(at, "MQCFG read", "AT#MQCFG?")
        step(at, "MQCFG2 keepalive", f"AT#MQCFG2={INST},60,1")
        step(at, "MQTCFG timeout", f"AT#MQTCFG={INST},10")
        # NOTE: AT#MQWCFG resets this module's firmware (M0Q.010001) and drops
        # USB — even the ,0 form. Skipped by default; enable with --test-willcfg.
        if args.test_willcfg:
            step(at, "MQWCFG last-will", f"AT#MQWCFG={INST},0")
        else:
            print("\n(skipping AT#MQWCFG — resets module on this firmware)")

        conn = step(at, "MQCONN connect",
                    f'AT#MQCONN={INST},"{clientid}","{user}","{pw}"', timeout=45)
        st = step(at, "MQCONN state", "AT#MQCONN?")
        connected = "OK" in conn and bool(re.search(rf"#MQCONN:\s*{INST},1", st))
        record("MQTT CONNECTED", connected, "state=1" if connected else "not connected")
        if not connected:
            print("\n(connection not established — skipping pub/sub)")
            cleanup(at, ssl); return summary()

        # subscribe / publish / receive round-trip
        # NOTE: M0Q.010001 fw rejects the optional <qos> on MQSUB (uses default qos 1)
        step(at, "MQSUB subscribe", f'AT#MQSUB={INST},"{topic}"')
        payload = "hello-from-LE910R1-MQPUBS"
        step(at, "MQPUBS publish", f'AT#MQPUBS={INST},"{topic}",0,1,"{payload}"', timeout=15)
        ring = wait_ring(at, 8)
        if ring:
            record("MQRING URC", True, f"mId={ring[0]} topic={ring[1]} len={ring[2]}")
            rd = step(at, "MQREAD read", f"AT#MQREAD={INST},{ring[0]}", timeout=8)
            record("MQREAD payload echo", payload in rd, f"present={payload in rd}")
        else:
            record("MQRING URC", False, "no URC (broker ACL may block self-echo)")

        # publish-extension (prompt-based)
        ext = "extended-payload-via-MQPUBSEXT"
        print(f'\n>>> AT#MQPUBSEXT={INST},"{topic}",0,1,{len(ext)}')
        at._flush(); at.write_raw(f'AT#MQPUBSEXT={INST},"{topic}",0,1,{len(ext)}\r')
        time.sleep(0.4); prompt = at.drain(3); print(prompt)
        if ">>>" in prompt:
            at.write_raw(ext); res = at.drain(8); print(res)
            record("MQPUBSEXT publish", "OK" in res, res)
            r2 = wait_ring(at, 8)
            if r2:
                rd2 = step(at, "MQREAD (ext)", f"AT#MQREAD={INST},{r2[0]}", timeout=8)
                record("MQREAD ext echo", ext in rd2, f"present={ext in rd2}")
        else:
            record("MQPUBSEXT publish", False, "no >>> prompt")

        step(at, "MQUNS unsubscribe", f'AT#MQUNS={INST},"{topic}"')
        cleanup(at, ssl)
    summary()


def cleanup(at, ssl):
    step(at, "MQDISC disconnect", f"AT#MQDISC={INST}")
    step(at, "MQEN disable", f"AT#MQEN={INST},0")
    if ssl:
        step(at, "SSLEN disable", f"AT#SSLEN={SSID},0", expect_ok=False)


def summary():
    print("\n" + "=" * 64 + "\nMQTT TEST SUMMARY\n" + "=" * 64)
    for label, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<22} {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n  {passed}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
