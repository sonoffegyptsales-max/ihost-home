#!/usr/bin/env python3
"""ihost.py - small CLI to control every device on a SONOFF iHost (eWeLink CUBE Open API V2).

Standard library only. Config lives in config.json next to this file.

Examples
  python ihost.py token                      # pair: click "Done" in the iHost web console
  python ihost.py list                       # all devices + state
  python ihost.py off all                    # everything off (asks for confirmation)
  python ihost.py on "kitchen"               # name contains "kitchen"
  python ihost.py toggle tag:room=living     # devices tagged room=living
  python ihost.py on cat:light               # by display category
  python ihost.py on ABC123 --channel 2      # one channel of a multi-gang switch
  python ihost.py set cat:light brightness=40 colortemp=20
  python ihost.py tag ABC123 room=living     # put a device in a group
  python ihost.py scene goodnight            # run a scene from scenes.json
  python ihost.py watch                      # live state changes (SSE)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
SCENES = os.path.join(HERE, "scenes.json")
API = "/open-api/v2/rest"

# convenience keys for `set` -> (capability, field)
SET_KEYS = {
    "brightness": ("brightness", "brightness"),
    "colortemp": ("color-temperature", "colorTemperature"),
    "percentage": ("percentage", "percentage"),
}

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Arabic device names on Windows consoles
except Exception:
    pass


# ---------------------------------------------------------------- config / http
def load_config():
    cfg = {"host": "http://ihost.local", "token": "", "timeout": 10}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    if os.environ.get("IHOST_HOST"):
        cfg["host"] = os.environ["IHOST_HOST"]
    if os.environ.get("IHOST_TOKEN"):
        cfg["token"] = os.environ["IHOST_TOKEN"]
    if not cfg["host"].startswith("http"):
        cfg["host"] = "http://" + cfg["host"]
    cfg["host"] = cfg["host"].rstrip("/")
    return cfg


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump({k: cfg[k] for k in ("host", "token", "timeout")}, f, indent=2)


class ApiError(Exception):
    pass


def call(cfg, method, path, body=None, auth=True):
    url = cfg["host"] + API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth:
        if not cfg["token"]:
            raise ApiError("no token - run:  python ihost.py token")
        req.add_header("Authorization", "Bearer " + cfg["token"])
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach {cfg['host']} ({e.reason}). Check host in config.json")
    try:
        res = json.loads(raw or b"{}")
    except ValueError:
        raise ApiError(f"non-JSON reply from {url}: {raw[:200]!r}")
    return res


def ok(res):
    return res.get("error") == 0


# ---------------------------------------------------------------- devices
def get_devices(cfg):
    res = call(cfg, "GET", "/devices")
    if not ok(res):
        if res.get("error") == 401:
            raise ApiError("token rejected (401) - run:  python ihost.py token")
        raise ApiError(f"device list failed: {res}")
    return res["data"].get("device_list", [])


def caps_of(dev):
    return dev.get("capabilities", []) or []


def cap_names(dev):
    return {c.get("capability") for c in caps_of(dev)}


def channels(dev):
    return [str(c["name"]) for c in caps_of(dev) if c.get("capability") == "toggle" and "name" in c]


def select(devs, target):
    """target: all | serial | cat:<category> | tag:<k>=<v> | <name substring>.
    Several selectors can be joined with ',' (union)."""
    out, seen = [], set()
    for t in [x.strip() for x in target.split(",") if x.strip()]:
        tl = t.lower()
        if tl == "all":
            hits = devs
        elif tl.startswith("cat:"):
            hits = [d for d in devs if (d.get("display_category") or "").lower() == tl[4:]]
        elif tl.startswith("tag:"):
            k, _, v = t[4:].partition("=")
            hits = [d for d in devs if str((d.get("tags") or {}).get(k, "")).lower() == v.lower()
                    or (not v and k in (d.get("tags") or {}))]
        else:
            exact = [d for d in devs if d.get("serial_number") == t]
            prefix = [d for d in devs if len(t) >= 6 and d.get("serial_number", "").lower().startswith(tl)]
            hits = exact or prefix or [d for d in devs if tl in (d.get("name") or "").lower()]
        for d in hits:
            if d["serial_number"] not in seen:
                seen.add(d["serial_number"])
                out.append(d)
    return out


def state_summary(dev):
    st = dev.get("state") or {}
    parts = []
    if "power" in st and "toggle" not in st:
        parts.append("power=" + str(st["power"].get("powerState")))
    if "toggle" in st:
        parts.append(" ".join(f"ch{k}={v.get('toggleState')}" for k, v in sorted(st["toggle"].items())))
    for cap, val in st.items():
        if cap in ("power", "toggle") or not isinstance(val, dict):
            continue
        scalars = [v for v in val.values() if isinstance(v, (str, int, float, bool))]
        if scalars:
            parts.append(f"{cap}={scalars[0]}")
    return " ".join(parts)


def user_tags(dev):
    """tags minus the iHost's own bookkeeping keys"""
    return {k: v for k, v in (dev.get("tags") or {}).items()
            if not k.startswith("_") and not k.startswith("deviceInfo") and k != "version"
            and v is not None and not isinstance(v, (dict, list))}


def switch_state(dev, action, channel=None):
    """Build the state object for on/off/toggle, or None if the device can't switch."""
    chans = channels(dev)
    if channel:
        if str(channel) not in chans:
            return None
        return {"toggle": {str(channel): {"toggleState": action}}}
    if chans:  # multi-gang: switch every channel
        return {"toggle": {c: {"toggleState": action} for c in chans}}
    if "power" in cap_names(dev):
        return {"power": {"powerState": action}}
    return None


def put_state(cfg, dev, state, dry):
    label = f"{dev.get('name')!s:28} {dev['serial_number']}"
    if dry:
        print(f"  DRY  {label}  {json.dumps(state, ensure_ascii=False)}")
        return True
    res = call(cfg, "PUT", f"/devices/{urllib.parse.quote(dev['serial_number'])}", {"state": state})
    good = ok(res)
    print(f"  {'OK  ' if good else 'FAIL'} {label}" + ("" if good else f"  -> {res.get('error')} {res.get('message')}"))
    return good


def apply(cfg, targets, make_state, dry=False, include_offline=False):
    okc = fail = skip = 0
    for d in targets:
        if not d.get("online", True) and not include_offline:
            print(f"  SKIP {d.get('name')!s:28} {d['serial_number']}  (offline)")
            skip += 1
            continue
        st = make_state(d)
        if not st:
            skip += 1
            continue
        if put_state(cfg, d, st, dry):
            okc += 1
        else:
            fail += 1
    print(f"done: {okc} ok, {fail} failed, {skip} skipped")
    return fail == 0


def confirm(targets, what, yes, include_offline=False):
    if not include_offline:
        targets = [d for d in targets if d.get("online", True)]
    if yes or len(targets) <= 1:
        return True
    print(f"{what} on {len(targets)} devices:")
    for d in targets:
        print(f"   - {d.get('name')}  [{d.get('display_category')}]")
    try:
        return input("continue? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ---------------------------------------------------------------- commands
def cmd_token(cfg, a):
    if a.host:
        cfg["host"] = a.host if a.host.startswith("http") else "http://" + a.host
    print(f"Requesting token from {cfg['host']} ...")
    print('Open the iHost web console in a browser and click "Done" on the popup (valid ~5 min).')
    deadline = time.time() + a.wait
    while time.time() < deadline:
        # newer firmware (2.14+) rejects the call without app_name ("app_name is required!")
        res = call(cfg, "GET", "/bridge/access_token?app_name=" + urllib.parse.quote(a.app_name), auth=False)
        if ok(res) and res.get("data", {}).get("token"):
            cfg["token"] = res["data"]["token"]
            save_config(cfg)
            print(f"token saved to {CONFIG}")
            return True
        print(f"  waiting... ({res.get('message', res.get('error'))})")
        time.sleep(3)
    print("timed out - run again and click Done in the web console")
    return False


def cmd_list(cfg, a):
    devs = get_devices(cfg)
    if a.target:
        devs = select(devs, a.target)
    if a.json:
        print(json.dumps(devs, indent=2, ensure_ascii=False))
        return True
    print(f"{'SERIAL':36} {'NAME':24} {'CATEGORY':12} {'ON':3} {'TAGS':16} STATE")
    for d in devs:
        tags = ",".join(f"{k}={v}" for k, v in user_tags(d).items())
        print(f"{d['serial_number']:36} {str(d.get('name'))[:24]:24} {str(d.get('display_category'))[:12]:12} "
              f"{'yes' if d.get('online') else 'NO ':3} {tags[:16]:16} {state_summary(d)}")
    print(f"{len(devs)} devices")
    return True


def cmd_switch(cfg, a):
    targets = select(get_devices(cfg), a.target)
    if not targets:
        print(f"no device matches '{a.target}'")
        return False
    switchable = [d for d in targets if switch_state(d, a.action, a.channel)]
    if not switchable:
        print("matched devices have no on/off capability" + (f" with channel {a.channel}" if a.channel else ""))
        return False
    if not a.dry_run and not confirm(switchable, a.action.upper(), a.yes, a.include_offline):
        print("cancelled")
        return False
    return apply(cfg, switchable, lambda d: switch_state(d, a.action, a.channel), a.dry_run, a.include_offline)


def parse_set(pairs):
    state = {}
    for p in pairs:
        k, _, v = p.partition("=")
        if not v:
            raise ApiError(f"bad value '{p}', use key=value")
        if k in SET_KEYS:
            cap, field = SET_KEYS[k]
            state[cap] = {field: int(v)}
        elif k == "power":
            state["power"] = {"powerState": v}
        else:
            raise ApiError(f"unknown key '{k}' (use {', '.join(list(SET_KEYS) + ['power'])} or `raw`)")
    return state


def cmd_set(cfg, a):
    wanted = parse_set(a.values)
    targets = select(get_devices(cfg), a.target)

    def mk(d):  # only send capabilities the device actually has
        names = cap_names(d)
        return {k: v for k, v in wanted.items() if k in names} or None

    targets = [d for d in targets if mk(d)]
    if not targets:
        print("no matched device supports " + ", ".join(wanted))
        return False
    if not a.dry_run and not confirm(targets, "SET " + " ".join(a.values), a.yes, a.include_offline):
        print("cancelled")
        return False
    return apply(cfg, targets, mk, a.dry_run, a.include_offline)


def cmd_raw(cfg, a):
    state = json.loads(a.state_json)
    targets = select(get_devices(cfg), a.target)
    if not targets:
        print(f"no device matches '{a.target}'")
        return False
    if not a.dry_run and not confirm(targets, "RAW", a.yes, a.include_offline):
        return False
    return apply(cfg, targets, lambda d: state, a.dry_run, a.include_offline)


def cmd_tag(cfg, a):
    devs = select(get_devices(cfg), a.target)
    if not devs:
        print(f"no device matches '{a.target}'")
        return False
    good = True
    for d in devs:
        tags = dict(d.get("tags") or {})
        for p in a.pairs:
            k, _, v = p.partition("=")
            tags[k] = v if v else None  # "key=" clears the tag (iHost merges tags; null clears)
        res = call(cfg, "PUT", f"/devices/{urllib.parse.quote(d['serial_number'])}", {"tags": tags})
        print(f"  {'OK  ' if ok(res) else 'FAIL'} {d.get('name')}  tags={tags}")
        good &= ok(res)
    return good


def cmd_rename(cfg, a):
    res = call(cfg, "PUT", f"/devices/{urllib.parse.quote(a.serial)}", {"name": a.name})
    print("OK" if ok(res) else f"FAIL {res}")
    return ok(res)


def load_scenes():
    if not os.path.exists(SCENES):
        raise ApiError(f"no {SCENES} - copy scenes.example.json to scenes.json")
    with open(SCENES, encoding="utf-8") as f:
        return json.load(f)


def cmd_scenes(cfg, a):
    for name, steps in load_scenes().items():
        print(f"{name}:")
        for s in steps:
            print(f"   {s.get('target')} -> " + json.dumps({k: v for k, v in s.items() if k != 'target'}, ensure_ascii=False))
    return True


def cmd_scene(cfg, a):
    scenes = load_scenes()
    if a.name not in scenes:
        print(f"unknown scene '{a.name}'. available: {', '.join(scenes)}")
        return False
    devs = get_devices(cfg)
    good = True
    for step in scenes[a.name]:
        targets = select(devs, step["target"])
        print(f"- {step['target']} ({len(targets)} devices)")
        if "action" in step:
            ch = step.get("channel")
            mk = lambda d, s=step, ch=ch: switch_state(d, s["action"], ch)
        elif "set" in step:
            wanted = parse_set([f"{k}={v}" for k, v in step["set"].items()])
            mk = lambda d, w=wanted: ({k: v for k, v in w.items() if k in cap_names(d)} or None)
        elif "state" in step:
            mk = lambda d, s=step: s["state"]
        else:
            print("   step needs action / set / state")
            good = False
            continue
        good &= apply(cfg, targets, mk, a.dry_run, a.include_offline)
        if step.get("delay"):
            time.sleep(float(step["delay"]))
    return good


def cmd_watch(cfg, a):
    if not cfg["token"]:
        raise ApiError("no token - run:  python ihost.py token")
    names = {}
    try:
        names = {d["serial_number"]: d.get("name") for d in get_devices(cfg)}
    except ApiError:
        pass
    url = cfg["host"] + "/open-api/v2/sse/bridge?access_token=" + urllib.parse.quote(cfg["token"])
    print("listening for events (Ctrl+C to stop)...")
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "text/event-stream"}),
                                        timeout=None) as r:
                event, data = None, []
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif line == "" and (event or data):
                        print_event(event, "\n".join(data), names)
                        event, data = None, []
        except KeyboardInterrupt:
            return True
        except Exception as e:  # gateway reboot, wifi drop... reconnect
            print(f"  [stream lost: {e}; reconnecting in 5s]")
            time.sleep(5)


def print_event(event, data, names):
    ts = time.strftime("%H:%M:%S")
    try:
        body = json.loads(data)
    except ValueError:
        print(f"{ts} {event} {data}")
        return
    sn = (body.get("endpoint") or {}).get("serial_number") or (body.get("payload") or {}).get("serial_number")
    who = f"{names.get(sn, '')} [{sn}]" if sn else ""
    payload = body.get("payload", body)
    print(f"{ts} {event} {who} {json.dumps(payload, ensure_ascii=False)}")


def cmd_info(cfg, a):
    res = call(cfg, "GET", "/bridge")
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return ok(res)


# ---------------------------------------------------------------- cli
def main(argv=None):
    p = argparse.ArgumentParser(prog="ihost", description="Control SONOFF iHost devices via Open API V2",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="TARGET: all | <serial> | <name part> | cat:<category> | tag:<key>=<value>  "
                                       "(join several with commas)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("token", help="pair with the gateway and save the token")
    s.add_argument("--host", help="iHost IP or http://host (saved to config)")
    s.add_argument("--wait", type=int, default=300, help="seconds to wait for Done (default 300)")
    s.add_argument("--app-name", default="ihost-control", help="name shown in the iHost popup")
    s.set_defaults(fn=cmd_token)

    s = sub.add_parser("info", help="gateway info")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("list", help="list devices")
    s.add_argument("target", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    def common(sp):
        sp.add_argument("-y", "--yes", action="store_true", help="no confirmation prompt")
        sp.add_argument("-n", "--dry-run", action="store_true", help="show what would be sent")
        sp.add_argument("--include-offline", action="store_true")

    for action in ("on", "off", "toggle"):
        s = sub.add_parser(action, help=f"{action} matching devices")
        s.add_argument("target")
        s.add_argument("-c", "--channel", help="only this channel of multi-gang devices")
        common(s)
        s.set_defaults(fn=cmd_switch, action=action)

    s = sub.add_parser("set", help="brightness=0-100 colortemp=0-100 percentage=0-100 power=on|off")
    s.add_argument("target")
    s.add_argument("values", nargs="+")
    common(s)
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("raw", help="send a raw state JSON, e.g. '{\"percentage\":{\"percentage\":40}}'")
    s.add_argument("target")
    s.add_argument("state_json")
    common(s)
    s.set_defaults(fn=cmd_raw)

    s = sub.add_parser("tag", help="set tags (groups): tag <target> room=living  (room= removes)")
    s.add_argument("target")
    s.add_argument("pairs", nargs="+")
    s.set_defaults(fn=cmd_tag)

    s = sub.add_parser("rename", help="rename a device")
    s.add_argument("serial")
    s.add_argument("name")
    s.set_defaults(fn=cmd_rename)

    s = sub.add_parser("scenes", help="list scenes in scenes.json")
    s.set_defaults(fn=cmd_scenes)

    s = sub.add_parser("scene", help="run a scene from scenes.json")
    s.add_argument("name")
    s.add_argument("-n", "--dry-run", action="store_true")
    s.add_argument("--include-offline", action="store_true")
    s.set_defaults(fn=cmd_scene)

    s = sub.add_parser("watch", help="print live device events (SSE)")
    s.set_defaults(fn=cmd_watch)

    a = p.parse_args(argv)
    cfg = load_config()
    try:
        return 0 if a.fn(cfg, a) else 1
    except ApiError as e:
        print("error:", e)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
