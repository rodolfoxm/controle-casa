# -*- coding: utf-8 -*-
"""
Controle Casa - núcleo: configuração, conexão com a nuvem Tuya/Smart Life,
descoberta automática de dispositivos e servidor local que a interface usa.
"""
import collections, copy, json, os, re, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

VERSION = 11
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", APP_DIR)
CONF_DIR = os.path.join(os.environ["APPDATA"], "ControleCasa") if os.environ.get("APPDATA") else APP_DIR
CONF_PATH = os.path.join(CONF_DIR, "config.json")

REGIONS = [("us", "Américas (Oeste) — padrão no Brasil"), ("us-e", "Américas (Leste)"),
           ("eu", "Europa Central"), ("eu-w", "Europa Ocidental"), ("in", "Índia"),
           ("sg", "Singapura"), ("cn", "China")]

DEFAULT = {
    "configured": False,
    "demo": False,
    "api_region": "us",
    "access_id": "",
    "access_secret": "",
    "ac": {"room_name": "Quarto", "type": "auto", "infrared_id": "", "remote_id": "", "device_id": "",
           "method": "", "model": "", "modes": ["cool", "heat", "auto", "fan", "dry"], "temp_min": 17, "temp_max": 30},
    "room_sensor_id": "",
    "names": {},
    "channel_names": {},
    "hide": [],
    "wifi_ac_codes": {
        "power": "switch", "temp": "temp_set", "temp_scale": 1, "mode": "mode",
        "mode_map": {"cool": "cold", "heat": "hot", "auto": "auto", "fan": "wind", "dry": "wet"},
        "fan": "windspeed",
        "fan_map": {"auto": "auto", "low": "low", "mid": "middle", "high": "high"},
        "turbo": ""
    },
    "ui": {"theme": "auto", "tint": 12, "backdrop": "black"},
    "positivo": {"email": "", "password": ""},
    "rooms": ["Sala", "Cozinha", "Quarto", "Banheiro", "Escritório"],
    "device_room": {},
    "port": 8765
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config():
    cfg = {}
    for p in (CONF_PATH, os.path.join(APP_DIR, "config.json")):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f)
                break
            except Exception:
                pass
    old_ui = dict(cfg.get("ui") or {})
    cfg_saved_keys = set(cfg)
    cfg = _merge(DEFAULT, cfg)
    if any(k in old_ui for k in ("glass", "opacity", "real_glass", "blur")):   # versões 4/5 -> 6
        cfg["ui"]["tint"] = DEFAULT["ui"]["tint"]
    if "rooms" not in cfg_saved_keys and cfg.get("configured"):   # quem já usava a v10 mantém a lista daquela versão
        cfg["rooms"] = ["Sala", "Cozinha", "Quarto", "Suíte", "Estúdio"]
    if "backdrop" not in old_ui:                       # v7: fundo preto por padrão
        cfg["ui"]["backdrop"] = "black"
    for k in ("glass", "opacity", "real_glass", "blur", "refraction"):
        cfg["ui"].pop(k, None)
    if cfg["access_id"] and cfg["access_secret"] and "configured" not in cfg:
        cfg["configured"] = True
    return cfg


def save_config():
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONF_PATH)


CFG = load_config()
PORT = int(CFG.get("port", 8765))
URL = f"http://127.0.0.1:{PORT}/"

MODES = ["cool", "heat", "auto", "fan", "dry"]   # mesma ordem dos índices do IR da Tuya
FANS = ["auto", "low", "mid", "high"]
SW_RE = re.compile(r"^switch(_\d+)?$")
LIGHT_ON = "switch_led"
SCALES = {"bright_value_v2": (10, 1000), "bright_value": (25, 255),
          "temp_value_v2": (0, 1000), "temp_value": (0, 255)}
TEMP_CODES = ("va_temperature", "temp_current", "temp_indoor")
IR_AC_CATS = ("infrared_ac", "hwkt", "ktkzq")   # controle de ar virtual criado pelo hub IR


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _num(code):
    m = re.search(r"_(\d+)$", code)
    return int(m.group(1)) if m else 0


def to_pct(v, code):
    lo, hi = SCALES.get(code, (10, 1000))
    return clamp(round((float(v) - lo) / (hi - lo) * 100), 0, 100)


def from_pct(p, code):
    lo, hi = SCALES.get(code, (10, 1000))
    return int(round(lo + (hi - lo) * clamp(float(p), 0, 100) / 100))


def _codes(st):
    bc = next((c for c in ("bright_value_v2", "bright_value") if c in st), None)
    cc = next((c for c in ("colour_data_v2", "colour_data") if c in st), None)
    tc = next((c for c in ("temp_value_v2", "temp_value") if c in st), None)
    return bc, cc, tc


def _colour(st, cc):
    v = st.get(cc) or {}
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = {}
    return v if isinstance(v, dict) else {}


def _status_map(d):
    return {s["code"]: s["value"] for s in d.get("status", []) if "code" in s}


def parse_device(d):
    """Converte um aparelho da Tuya no formato do painel (None = não controlável)."""
    st = _status_map(d)
    did = d["id"]
    base = dict(id=did, name=CFG["names"].get(did) or d.get("name", "?"),
                original_name=d.get("name", "?"), online=bool(d.get("online")),
                category=d.get("category", ""))
    if isinstance(st.get(LIGHT_ON), bool):
        bc, cc, tc = _codes(st)
        mode = st.get("work_mode", "white")
        L = dict(has_bright=bool(bc), has_color=bool(cc), has_temp=bool(tc), mode=mode,
                 brightness=100, temp=50, h=30, s=0)
        if bc and isinstance(st.get(bc), (int, float)):
            L["brightness"] = to_pct(st[bc], bc)
        if tc and isinstance(st.get(tc), (int, float)):
            L["temp"] = to_pct(st[tc], tc)
        if cc:
            col, smax = _colour(st, cc), (1000 if cc.endswith("v2") else 255)
            L["h"] = int(col.get("h", 0))
            L["s"] = round(float(col.get("s", 0)) / smax * 100)
            if mode == "colour":
                L["brightness"] = round(float(col.get("v", smax)) / smax * 100)
        return dict(base, kind="light", on=st[LIGHT_ON], light=L)

    sw = sorted([c for c, v in st.items() if SW_RE.match(c) and isinstance(v, bool)], key=_num)
    if not sw:
        return None
    if len(sw) > 1:
        names = CFG["channel_names"].get(did) or CFG["channel_names"].get(d.get("name", "")) or []
        chans = [dict(code=c, name=(names[i] if i < len(names) and names[i] else f"Tecla {i + 1}"), on=st[c])
                 for i, c in enumerate(sw)]
        return dict(base, kind="multi", on=any(ch["on"] for ch in chans), channels=chans)
    kind = "plug" if base["category"] in ("cz", "pc") else "switch"
    return dict(base, kind=kind, on=st[sw[0]])


def light_commands(st, data):
    bc, cc, tc = _codes(st)
    cmds = []
    smax = 1000 if (cc or "").endswith("v2") else 255

    def colour_value(h, s, v):
        val = {"h": int(h) % 360, "s": int(round(s / 100 * smax)), "v": int(max(round(v / 100 * smax), smax * 0.01))}
        return val if (cc or "").endswith("v2") else json.dumps(val)

    if data.get("color") and cc:
        cur_v = data.get("brightness")
        if cur_v is None:
            col = _colour(st, cc)
            cur_v = float(col.get("v", smax)) / smax * 100 if st.get("work_mode") == "colour" else \
                (to_pct(st[bc], bc) if bc and bc in st else 100)
        cmds += [{"code": "work_mode", "value": "colour"},
                 {"code": cc, "value": colour_value(data["color"]["h"], data["color"]["s"], cur_v)}]
    elif data.get("temp") is not None and tc:
        cmds += [{"code": "work_mode", "value": "white"}, {"code": tc, "value": from_pct(data["temp"], tc)}]
        if data.get("brightness") is not None and bc:
            cmds.append({"code": bc, "value": from_pct(data["brightness"], bc)})
    elif data.get("brightness") is not None:
        if st.get("work_mode") == "colour" and cc:
            col = _colour(st, cc)
            s = float(col.get("s", smax)) / smax * 100
            cmds.append({"code": cc, "value": colour_value(col.get("h", 0), s, data["brightness"])})
        elif bc:
            cmds.append({"code": bc, "value": from_pct(data["brightness"], bc)})
    if cmds and "on" not in data and not st.get(LIGHT_ON):
        cmds.insert(0, {"code": LIGHT_ON, "value": True})
    return cmds


def visible(devs):
    hide = set(CFG.get("hide", []))
    out = [d for d in devs if d["id"] not in hide and d.get("original_name") not in hide]
    for d in out:
        d["room"] = room_of(d)
    return out


# ---------------------------------------------------------------- cômodos
OTHER = "Outros"
SL_ROOMS = {"map": {}, "t": 0, "busy": False}     # cômodos cadastrados no Smart Life: id -> nome


def _plain(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(ch) != "Mn")


ROOM_WORDS = [("suite", "Suíte"), ("cozinha", "Cozinha"), ("sala", "Sala"), ("quarto", "Quarto"),
              ("banheiro", "Banheiro"), ("lavabo", "Banheiro"), ("escritorio", "Escritório"),
              ("studio", "Estúdio"), ("estudio", "Estúdio"), ("varanda", "Varanda"), ("sacada", "Varanda"),
              ("garagem", "Garagem"), ("lavanderia", "Lavanderia"), ("area externa", "Área externa"),
              ("quintal", "Área externa"), ("jardim", "Área externa"), ("corredor", "Corredor")]


def match_room(text):
    """'Luz Cozinha 1' -> Cozinha; 'Sala de estar' -> Sala. Usa os cômodos da lista quando bate."""
    p = _plain(text)
    rooms = CFG.get("rooms") or []
    for r in rooms:                           # nome exato de um cômodo da lista
        if _plain(r) and _plain(r) in p:
            return r
    for word, room in ROOM_WORDS:
        if word in p:
            return next((r for r in rooms if _plain(r) == _plain(room)), room)
    return None


def room_of(d):
    did = d["id"]
    if did in CFG["device_room"]:
        return CFG["device_room"][did] or OTHER
    if did in SL_ROOMS["map"]:
        return match_room(SL_ROOMS["map"][did]) or SL_ROOMS["map"][did]
    guess = match_room(d.get("name", "")) or match_room(d.get("original_name", ""))
    return guess or OTHER


def room_order(devs=None):
    order = list(CFG.get("rooms") or [])
    for d in devs or []:
        if d.get("room") and d["room"] not in order and d["room"] != OTHER:
            order.append(d["room"])
    if any(d.get("room") == OTHER for d in devs or []):
        order.append(OTHER)
    return order


# ---------------------------------------------------------------- DEMO
class Demo:
    demo = True

    def __init__(self):
        self.ac = dict(power=True, mode="cool", temp=18, fan="auto", turbo=True)
        L = lambda **k: dict(dict(has_bright=True, has_color=True, has_temp=True, mode="white",
                                  brightness=80, temp=40, h=30, s=0), **k)
        raw = [
            dict(id="d1", name="Luz da Sala", kind="switch", online=True, on=True),
            dict(id="d2", name="Fita LED da Sala", kind="light", online=True, on=True,
                 light=L(mode="colour", h=270, s=90, brightness=70)),
            dict(id="d3", name="Tomada da TV", kind="plug", online=True, on=True),
            dict(id="d4", name="Luz da Cozinha", kind="switch", online=True, on=False),
            dict(id="d5", name="Cafeteira da Cozinha", kind="plug", online=True, on=False),
            dict(id="d6", name="Luz do Quarto", kind="switch", online=True, on=False),
            dict(id="d7", name="Abajur do Quarto", kind="light", online=True, on=True, light=L()),
            dict(id="d8", name="Interruptor do Escritório", kind="multi", online=True, on=True,
                 channels=[dict(code=f"switch_{i}", name=f"Tecla {i}", on=i == 1) for i in range(1, 3)]),
            dict(id="d9", name="Luminária do Escritório", kind="light", online=False, on=False, light=L()),
        ]
        for d in raw:
            d["original_name"] = d["name"]
        self.devices = raw

    def _named(self):
        out = []
        for d in self.devices:
            x = copy.deepcopy(d)
            x["name"] = CFG["names"].get(d["id"]) or d["original_name"]
            if x["kind"] == "multi":
                names = CFG["channel_names"].get(d["id"]) or []
                for i, ch in enumerate(x["channels"]):
                    if i < len(names) and names[i]:
                        ch["name"] = names[i]
            out.append(x)
        return out

    def state(self):
        return dict(demo=True, room=dict(name=CFG["ac"].get("room_name", "Quarto"), temp=26),
                    ac=dict(self.ac, available=True, **ac_profile()), devices=(v := visible(self._named())),
                    rooms=room_order(v))

    def set_ac(self, data):
        self.ac.update({k: v for k, v in data.items() if k in self.ac})
        return self.state()

    def set_device(self, dev_id, data):
        for d in self.devices:
            if d["id"] != dev_id:
                continue
            if "channel" in data:
                for ch in d["channels"]:
                    if ch["code"] == data["channel"]:
                        ch["on"] = bool(data["on"])
                d["on"] = any(ch["on"] for ch in d["channels"])
            elif "on" in data:
                d["on"] = bool(data["on"])
                for ch in d.get("channels", []):
                    ch["on"] = d["on"]
            if d["kind"] == "light":
                L = d["light"]
                if data.get("color"):
                    L.update(mode="colour", h=data["color"]["h"], s=data["color"]["s"]); d["on"] = True
                if data.get("temp") is not None:
                    L.update(mode="white", temp=data["temp"]); d["on"] = True
                if data.get("brightness") is not None:
                    L["brightness"] = data["brightness"]; d["on"] = True
        return self.state()

    def lights_off(self, room=None):
        for d in self.state()["devices"]:
            if d["kind"] in ("light", "switch", "multi") and d["online"] and (room is None or d["room"] == room):
                self.set_device(d["id"], {"on": False})
        return self.state()

    def discover(self):
        devs = [dict(id=d["id"], name=CFG["names"].get(d["id"]) or d["original_name"],
                     original_name=d["original_name"], kind=d["kind"], online=d["online"],
                     channels=[c["name"] for c in d.get("channels", [])],
                     hidden=d["id"] in CFG["hide"]) for d in self._named()]
        devs.append(dict(id="ir1", name="Controlador IR", original_name="Controlador IR", kind="hub",
                         online=True, channels=[], hidden=False))
        for d in devs:
            d["room"] = room_of(d)
            d["room_manual"] = d["id"] in CFG["device_room"]
        return dict(devices=devs, rooms=room_order(devs),
                    acs=[dict(type="ir", infrared_id="ir1", remote_id="r1", name="Ar-condicionado", hub="Controlador IR")],
                    sensors=[], current=CFG["ac"])

    def diagnose(self):
        return f"Controle Casa v{VERSION} · modo demonstração (nenhuma conta conectada)."

    def debug(self):
        return {"demo": True}


# ---------------------------------------------------------------- TUYA
class Tuya:
    demo = False

    def __init__(self, region, key, secret, detect=True):
        import tinytuya
        self.c = tinytuya.Cloud(apiRegion=region, apiKey=key, apiSecret=secret)
        err = getattr(self.c, "error", None)
        if err:
            raise ConnectionError(_err_text(err))
        self.lock = threading.Lock()
        self._cache, self._cache_t = [], 0
        a = CFG["ac"]
        self.ac_type = a.get("type", "auto")
        self.ir_id, self.remote_id, self.wifi_id = a.get("infrared_id", ""), a.get("remote_id", ""), a.get("device_id", "")
        self.wifi = CFG["wifi_ac_codes"]
        self.turbo = False
        self.last_ir = dict(power=True, mode="cool", temp=24, fan="auto")
        self.ir_method = CFG["ac"].get("method", "")   # forma de envio IR que funciona (fica salva)
        self.ir_status_ok = True
        self.ac_error = ""
        self.errors = collections.deque(maxlen=15)
        self.dev_mode = {}           # aparelho -> rota de comando que funcionou
        if detect:
            self._detect()

    def req(self, url, action="GET", post=None, query=None):
        with self.lock:
            r = self.c.cloudrequest(url, action=action, post=post, query=query)
        if not isinstance(r, dict):
            r = {"success": False, "msg": str(r)}
        if r.get("success") is False or r.get("Error"):
            line = f"{action} {url} -> {_err_text(r)}"
            print("[Tuya]", line)
            self.errors.append(time.strftime("%H:%M:%S ") + line)
        return r

    @staticmethod
    def ok(r):
        return isinstance(r, dict) and r.get("success") is True and r.get("result") is not False

    def devices(self, force=False, raise_errors=False):
        if not force and time.time() - self._cache_t < 4:
            return self._cache
        out, last = [], ""
        for _ in range(20):
            q = {"size": 100}
            if last:
                q["last_row_key"] = last
            r = self.req("/v1.0/iot-01/associated-users/devices", query=q)
            if raise_errors and r.get("success") is False:
                raise ConnectionError(_err_text(r))
            res = r.get("result") or {}
            out += res.get("devices", [])
            if not res.get("has_more"):
                break
            last = res.get("last_row_key", "")
        self._cache, self._cache_t = out, time.time()
        return out

    def ir_remotes(self, hub_id):
        return self.req(f"/v2.0/infrareds/{hub_id}/remotes").get("result") or []

    def _detect(self):
        if self.ac_type == "ir" and self.ir_id and self.remote_id:
            return
        if self.ac_type == "wifi" and self.wifi_id:
            return
        if self.ac_type == "irdev" and self.remote_id:
            return
        devs = self.devices(force=True)
        kt = [d for d in devs if d.get("category") == "kt"]
        if kt and self.ac_type in ("auto", "wifi"):
            self.wifi_id, self.ac_type = kt[0]["id"], "wifi"
            return
        for hub in [d for d in devs if d.get("category") == "wnykq"]:
            ac = [r for r in self.ir_remotes(hub["id"]) if str(r.get("category_id")) == "5"]
            if ac:
                self.ir_id, self.remote_id, self.ac_type = hub["id"], ac[0]["remote_id"], "ir"
                return
        virt = [d for d in devs if d.get("category") in IR_AC_CATS]
        if virt:
            self.remote_id, self.ac_type = virt[0]["id"], "irdev"

    def discover(self):
        devs = self.devices(force=True, raise_errors=True)
        acs, sensors, out = [], [], []
        ir_error = ""
        remote_ids = set()
        for d in devs:
            cat, st = d.get("category"), _status_map(d)
            if cat == "kt":
                acs.append(dict(type="wifi", device_id=d["id"], name=d.get("name", "Ar-condicionado")))
            if cat == "wnykq":
                rr = self.req(f"/v2.0/infrareds/{d['id']}/remotes")
                if not self.ok(rr):
                    ir_error = _ac_hint(_err_text(rr))
                for r in rr.get("result") or []:
                    remote_ids.add(r.get("remote_id"))
                    if str(r.get("category_id")) == "5":
                        acs.append(dict(type="ir", infrared_id=d["id"], remote_id=r["remote_id"],
                                        name=r.get("remote_name") or "Ar-condicionado", hub=d.get("name", "")))
            if cat in IR_AC_CATS:
                acs.append(dict(type="irdev", device_id=d["id"], remote_id=d["id"],
                                name=d.get("name", "Ar-condicionado"), hub="controle virtual"))
            if any(isinstance(st.get(c), (int, float)) for c in TEMP_CODES):
                sensors.append(dict(id=d["id"], name=d.get("name", "?")))
        for d in positivo_devices(force=True):
            p = parse_device(d)
            kind = p["kind"] if p else ("hub" if d.get("category") == "wnykq" else "other")
            out.append(dict(id=d["id"], name=CFG["names"].get(d["id"]) or d["name"], original_name=d["name"],
                            kind=kind, online=d["online"], channels=[ch["name"] for ch in (p or {}).get("channels", [])],
                            hidden=d["id"] in CFG["hide"], source="Positivo"))
        known = {a["remote_id"] for a in acs if a["type"] == "ir"}
        acs = [a for a in acs if not (a["type"] == "irdev" and a["remote_id"] in known)]
        for d in devs:
            if d["id"] in remote_ids or d.get("category") in IR_AC_CATS:
                continue
            p = parse_device(d)
            kind = p["kind"] if p else ("hub" if d.get("category") == "wnykq" else
                                         "ac" if d.get("category") == "kt" else "other")
            out.append(dict(id=d["id"], name=CFG["names"].get(d["id"]) or d.get("name", "?"),
                            original_name=d.get("name", "?"), kind=kind, online=bool(d.get("online")),
                            channels=[c["name"] for c in (p or {}).get("channels", [])],
                            hidden=d["id"] in CFG["hide"]))
        self.refresh_rooms(wait=True)
        for d in out:
            d["room"] = room_of(d)
            d["room_manual"] = d["id"] in CFG["device_room"]
        return dict(devices=out, acs=acs, sensors=sensors, current=CFG["ac"], ir_error=ir_error,
                    rooms=room_order(out))

    # --- ar-condicionado
    def ac_state(self):
        if self.ac_type == "ir" and self.ir_id and self.remote_id:
            r = {}
            if self.ir_status_ok:
                resp = self.req(f"/v2.0/infrareds/{self.ir_id}/remotes/{self.remote_id}/ac/status")
                if self.ok(resp) or isinstance(resp.get("result"), dict):
                    r = resp.get("result") or {}
                else:
                    self.ir_status_ok = False   # sem leitura de estado: usa o último enviado
            try:
                if not r:
                    raise ValueError
                self.last_ir = dict(power=str(r.get("power", "1")) == "1",
                                    mode=MODES[int(r.get("mode", 0)) % 5],
                                    temp=int(r.get("temp", 24)),
                                    fan=FANS[int(r.get("wind", 0)) % 4])
            except Exception:
                pass
            return dict(self.last_ir, turbo=self.turbo, available=True, error=self.ac_error, **ac_profile())
        if self.ac_type == "irdev" and self.remote_id:
            return dict(self.last_ir, turbo=self.turbo, available=True, error=self.ac_error, **ac_profile())
        if self.ac_type == "wifi" and self.wifi_id:
            st = self._status(self.wifi_id)
            w = self.wifi
            mode_rev = {v: k for k, v in w["mode_map"].items()}
            fan_rev = {v: k for k, v in w["fan_map"].items()}
            temp = st.get(w["temp"], 24)
            temp = round(temp / w.get("temp_scale", 1)) if isinstance(temp, (int, float)) else 24
            turbo = bool(st.get(w["turbo"])) if w.get("turbo") else self.turbo
            return dict(power=bool(st.get(w["power"], False)), mode=mode_rev.get(st.get(w["mode"]), "cool"),
                        temp=temp, fan=fan_rev.get(st.get(w["fan"]), "auto"), turbo=turbo, available=True)
        return dict(power=False, mode="cool", temp=24, fan="auto", turbo=False, available=False)

    def set_ac(self, data):
        new = dict(self.ac_state())
        new.update({k: data[k] for k in ("power", "mode", "temp", "fan", "turbo") if k in data})
        new["temp"] = clamp(int(new["temp"]), CFG["ac"].get("temp_min", 17), CFG["ac"].get("temp_max", 30))
        for k in ("error", "available", "modes", "temp_min", "temp_max", "model"):
            new.pop(k, None)
        if self.ac_type in ("ir", "irdev"):
            changed = {k for k in ("power", "mode", "temp", "fan", "turbo") if k in data}
            if not changed:
                return self.state(ac_override=dict(new, **ac_profile()))
            self.turbo = bool(new["turbo"])
            fan = "high" if self.turbo else new["fan"]
            self._ir_send(new, fan, changed)
            self.last_ir = dict(power=new["power"], mode=new["mode"], temp=new["temp"], fan=new["fan"])
        elif self.ac_type == "wifi":
            w, cmds = self.wifi, []
            if "power" in data: cmds.append({"code": w["power"], "value": bool(new["power"])})
            if "temp" in data: cmds.append({"code": w["temp"], "value": int(new["temp"] * w.get("temp_scale", 1))})
            if "mode" in data: cmds.append({"code": w["mode"], "value": w["mode_map"][new["mode"]]})
            if "fan" in data: cmds.append({"code": w["fan"], "value": w["fan_map"][new["fan"]]})
            if "turbo" in data:
                self.turbo = bool(new["turbo"])
                if w.get("turbo"):
                    cmds.append({"code": w["turbo"], "value": self.turbo})
                else:
                    cmds.append({"code": w["fan"], "value": w["fan_map"]["high" if self.turbo else new["fan"]]})
            self._send(self.wifi_id, cmds)
        self._cache_t = 0
        return self.state(ac_override=dict(new, **ac_profile()))

    def _find_hub(self):
        """Controle virtual sem hub conhecido: procura em qual Controlador IR ele está."""
        if self.ir_id or not self.remote_id:
            return
        for hub in [d for d in self.devices() if d.get("category") == "wnykq"]:
            if any(r.get("remote_id") == self.remote_id for r in self.ir_remotes(hub["id"])):
                self.ir_id = hub["id"]
                return

    def _ir_methods(self, new, fan, changed=None):
        """As formas que a Tuya aceita para mandar um comando de ar pelo IR.
        Cada ajuste vira UM disparo de IR (um bip no ar)."""
        changed = set(changed or {"power", "mode", "temp", "fan"})
        body = {"power": 1 if new["power"] else 0, "mode": MODES.index(new["mode"]),
                "temp": int(new["temp"]), "wind": FANS.index(fan)}
        # quando liga/desliga, manda só o liga/desliga; o ar volta como estava
        if "power" in changed or not new["power"]:
            keys = [("power", body["power"])]
        else:
            keys = []
            if "mode" in changed: keys.append(("mode", body["mode"]))
            if "temp" in changed: keys.append(("temp", body["temp"]))
            if changed & {"fan", "turbo"}: keys.append(("wind", body["wind"]))
        std_map = {"mode": "M", "temp": "T", "wind": "F"}
        std = []
        for code, value in keys:
            if code == "power":
                std.append({"code": "PowerOn" if value else "PowerOff", "value": "PowerOn" if value else "PowerOff"})
            else:
                std.append({"code": std_map[code], "value": value})
        base = f"/v2.0/infrareds/{self.ir_id}/air-conditioners/{self.remote_id}"

        def single():
            r = {}
            for i, (code, value) in enumerate(keys):
                if i:
                    time.sleep(0.4)
                r = self.req(base + "/command", "POST", {"code": code, "value": value})
                if not self.ok(r):
                    return r
            return r

        out = []
        if self.ir_id:
            out.append(("cena (v2)", lambda: self.req(base + "/scenes", "POST", body)))
            out.append(("tecla por tecla (v2)", single))
            out.append(("cena (v1)", lambda: self.req(
                f"/v1.0/infrareds/{self.ir_id}/air-conditioners/{self.remote_id}/scenes", "POST", body)))
        out.append(("comando padrão", lambda: self.req(
            f"/v1.0/iot-03/devices/{self.remote_id}/commands", "POST", {"commands": std})))
        return out

    def _remember_method(self, name):
        self.ir_method = name
        if CFG["ac"].get("method") != name:
            CFG["ac"]["method"] = name
            try:
                save_config()
            except Exception:
                pass

    def _ir_send(self, new, fan, changed=None):
        """Usa só a forma que já funcionou. Sem fallback depois disso: tentar outra forma
        poderia disparar o IR duas vezes (o ar apitaria em dobro)."""
        self._find_hub()
        tries = self._ir_methods(new, fan, changed)
        if self.ir_method:
            tries = [t for t in tries if t[0] == self.ir_method] or tries
        first_err = ""
        for name, fn in tries:
            r = fn()
            if self.ok(r):
                self._remember_method(name)
                self.ac_error = ""
                return
            first_err = first_err or _err_text(r)
        self.ac_error = _ac_hint(first_err or "O ar não respondeu.")
        raise RuntimeError("Ar-condicionado: " + self.ac_error)

    def test_ac(self):
        """Tenta cada forma de comando separadamente (liga o ar a 24°C) e relata o resultado."""
        self._find_hub()
        lines = [f"Ar escolhido: tipo={self.ac_type} hub={self.ir_id or '-'} controle={self.remote_id or self.wifi_id or '-'}"]
        if self.ac_type == "wifi":
            try:
                self.set_ac({"power": True, "temp": 24})
                lines.append("Ar Wi-Fi: comando aceito.")
            except Exception as e:
                lines.append(f"Ar Wi-Fi: falhou -> {e}")
            return "\n".join(lines)
        if not self.remote_id:
            return "\n".join(lines + ["Nenhum ar escolhido. Abra Ajustes → Gerenciar aparelhos."])
        if self.ir_id:
            r = self.req(f"/v2.0/infrareds/{self.ir_id}/remotes/{self.remote_id}/keys")
            if self.ok(r) or isinstance(r.get("result"), dict):
                k = r.get("result") or {}
                keys = ", ".join(str(x.get("key_name") or x.get("key")) for x in (k.get("key_list") or [])[:12])
                lines.append(f"Controle: categoria {k.get('category_id')} · marca {k.get('brand_id')} · {k.get('remote_index') or ''}")
                if keys:
                    lines.append("Teclas: " + keys)
            else:
                lines.append("Teclas do controle: " + _err_text(r))
        new = dict(power=True, mode="cool", temp=24, fan="auto")
        ok_any, first_err = False, ""
        for name, fn in self._ir_methods(new, "auto", {"power"}):
            r = fn()
            if self.ok(r):
                lines.append(f"✔ {name}: aceito — o app vai usar só esta forma daqui pra frente")
                self._remember_method(name)
                self.ac_error = ""
                ok_any = True
                break
            else:
                first_err = first_err or _err_text(r)
                lines.append(f"✘ {name}: {_err_text(r)}")
        lines.append("")
        lines.append("Resultado: " + ("pelo menos uma forma funcionou. O ar ligou?" if ok_any else
                                      "nenhuma forma foi aceita. " + _ac_hint(first_err)))
        return "\n".join(lines)

    # --- casa
    def _status(self, dev_id):
        if is_pos(dev_id):
            return POS["client"].status(dev_id) if POS["client"] else {}
        for d in self.devices():
            if d["id"] == dev_id:
                return _status_map(d)
        return {s["code"]: s["value"] for s in (self.req(f"/v1.0/iot-03/devices/{dev_id}/status").get("result") or [])}

    def room_temp(self):
        sid = CFG.get("room_sensor_id") or (self.wifi_id if self.ac_type == "wifi" else "")
        if not sid:
            return None
        st = self._status(sid)
        for code in TEMP_CODES:
            v = st.get(code)
            if isinstance(v, (int, float)):
                return round(v / 10) if v > 60 else v
        return None

    def state(self, ac_override=None):
        skip = {self.ir_id, self.remote_id, self.wifi_id}
        self.refresh_rooms()
        devs = [p for p in (parse_device(d) for d in self.devices() + positivo_devices() if d["id"] not in skip) if p]
        ac = ac_override or self.ac_state()
        ac.setdefault("available", True)
        v = visible(devs)
        return dict(demo=False, room=dict(name=CFG["ac"].get("room_name", "Quarto"), temp=self.room_temp()),
                    ac=ac, devices=v, rooms=room_order(v))

    def refresh_rooms(self, wait=False):
        """Lê os cômodos do Smart Life (a cada 10 min, em segundo plano). Sem permissão, só ignora."""
        if SL_ROOMS["busy"] or time.time() - SL_ROOMS["t"] < 600:
            return

        def work():
            SL_ROOMS["busy"] = True
            try:
                homes = {d.get("owner_id") for d in self.devices() if d.get("owner_id")}
                mp = {}
                for hid in homes:
                    r = self.req(f"/v1.0/homes/{hid}/rooms")
                    for room in ((r.get("result") or {}).get("rooms") or []) if self.ok(r) else []:
                        rd = self.req(f"/v1.0/homes/{hid}/rooms/{room.get('room_id')}/devices")
                        for dev in (rd.get("result") or []) if self.ok(rd) else []:
                            if dev.get("id"):
                                mp[dev["id"]] = room.get("name", "")
                SL_ROOMS["map"] = mp
            except Exception as e:
                print("cômodos:", e)
            finally:
                SL_ROOMS.update(t=time.time(), busy=False)
        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    def _send(self, dev_id, cmds):
        """Manda comandos; se o aparelho não aceitar o formato padrão, tenta os outros formatos da Tuya."""
        if not cmds:
            return
        if is_pos(dev_id):
            try:
                POS["client"].send(dev_id, cmds)
            except Exception as e:
                raise RuntimeError(f"Positivo: {e}")
            return
        props = json.dumps({c["code"]: c["value"] for c in cmds})
        routes = {
            "padrao": lambda: self.req(f"/v1.0/iot-03/devices/{dev_id}/commands", "POST", {"commands": cmds}),
            "dp": lambda: self.req(f"/v2.0/cloud/thing/{dev_id}/shadow/properties/issue", "POST", {"properties": props}),
            "v1": lambda: self.req(f"/v1.0/devices/{dev_id}/commands", "POST", {"commands": cmds}),
        }
        order = sorted(routes, key=lambda k: k != self.dev_mode.get(dev_id, "padrao"))
        first_err = ""
        for k in order:
            r = routes[k]()
            if self.ok(r):
                self.dev_mode[dev_id] = k
                return
            first_err = first_err or _err_text(r)
        raise RuntimeError(first_err or "O aparelho não respondeu.")

    def set_device(self, dev_id, data):
        st = self._status(dev_id)
        cmds = []
        if "channel" in data:
            cmds.append({"code": data["channel"], "value": bool(data["on"])})
        elif "on" in data:
            if isinstance(st.get(LIGHT_ON), bool):
                cmds.append({"code": LIGHT_ON, "value": bool(data["on"])})
            else:
                sw = [c for c, v in st.items() if SW_RE.match(c) and isinstance(v, bool)] or ["switch_1"]
                cmds += [{"code": c, "value": bool(data["on"])} for c in sw]
        if isinstance(st.get(LIGHT_ON), bool):
            cmds += light_commands(st, data)
        self._send(dev_id, cmds)
        self._cache_t = 0
        time.sleep(0.5)
        return self.state()

    def lights_off(self, room=None):
        for d in self.state()["devices"]:
            if d["kind"] in ("light", "switch", "multi") and d["online"] and d["on"] \
                    and (room is None or d["room"] == room):
                self.set_device(d["id"], {"on": False})
        return self.state()

    def diagnose(self):
        L = [f"Controle Casa v{VERSION} · região {CFG['api_region']}"]
        devs = self.devices(force=True)
        L.append(f"Aparelhos na conta: {len(devs)}")
        for d in devs:
            codes = ",".join(s.get("code", "?") for s in d.get("status", []))
            f = self.req(f"/v1.0/iot-03/devices/{d['id']}/functions")
            fn = "funções OK" if self.ok(f) else "funções: " + _err_text(f)
            L.append(f"- {d.get('name')} [{d.get('category')}] {'online' if d.get('online') else 'OFFLINE'} | {fn} | {codes}")
        L.append("")
        L.append(f"Ar: tipo={self.ac_type} hub={self.ir_id or '-'} controle={self.remote_id or '-'} wifi={self.wifi_id or '-'}"
                 f" método={self.ir_method or '-'}")
        for hub in [d for d in devs if d.get("category") == "wnykq"]:
            r = self.req(f"/v2.0/infrareds/{hub['id']}/remotes")
            if self.ok(r):
                rem = ", ".join(f"{x.get('remote_name')}(cat {x.get('category_id')})" for x in r.get("result") or [])
                L.append(f"Hub {hub.get('name')}: controles = {rem or 'nenhum'}")
            else:
                L.append(f"Hub {hub.get('name')}: lista de controles falhou -> {_err_text(r)}")
        if self.ir_id and self.remote_id:
            r = self.req(f"/v2.0/infrareds/{self.ir_id}/remotes/{self.remote_id}/ac/status")
            L.append("Status do ar: " + (json.dumps(r.get("result"), ensure_ascii=False) if self.ok(r) else _err_text(r)))
        if self.ac_error:
            L.append("Último erro do ar: " + self.ac_error)
        L.append("")
        if POS["client"]:
            try:
                pd = positivo_devices(force=True)
                L.append(f"Conta Positivo ({CFG['positivo']['email']}): {len(pd)} aparelho(s)")
                for d in pd:
                    L.append(f"- {d['name']} [{d['category']}] {'online' if d['online'] else 'OFFLINE'} | "
                             + ",".join(s['code'] for s in d['status']))
            except Exception as e:
                L.append(f"Conta Positivo: erro -> {e}")
        else:
            L.append("Conta Positivo: não conectada")
        if POS["error"]:
            L.append("Último erro da Positivo: " + POS["error"])
        if self.errors:
            L.append("")
            L.append("Últimos erros:")
            L += list(self.errors)
        return "\n".join(L)

    def debug(self):
        return dict(ac_type=self.ac_type, infrared_id=self.ir_id, remote_id=self.remote_id,
                    wifi_device_id=self.wifi_id,
                    devices=[dict(id=d["id"], name=d.get("name"), category=d.get("category"),
                                  online=d.get("online"), status=d.get("status")) for d in self.devices(True)])


def ac_profile():
    a = CFG["ac"]
    return dict(modes=a.get("modes") or MODES, temp_min=a.get("temp_min", 17), temp_max=a.get("temp_max", 30),
                model=a.get("model", ""))


def _ac_hint(msg):
    low = msg.lower()
    if "permiss" in low or "1106" in low or "28841101" in low or "não autorizado" in low:
        return (msg + " → No projeto da Tuya: Service API → Go to Authorize → adicione 'IR Control Hub Open Service'"
                " e confira se o trial dele está ativo.")
    return msg


def _err_text(err):
    if isinstance(err, dict):
        code = str(err.get("code") or err.get("Errorcode") or "")
        msg = err.get("msg") or err.get("Error") or err.get("Payload") or ""
        hints = {
            "1004": "Access Secret incorreto.",
            "1005": "Access ID incorreto.",
            "2009": "Projeto sem permissão — confira se a API 'IoT Core' está autorizada.",
            "1106": "Sem permissão — confira se o app Smart Life está vinculado e se o serviço necessário está autorizado no projeto.",
            "28841101": "Serviço não autorizado no projeto — adicione o 'IR Control Hub Open Service' em Service API.",
            "2008": "Comando não suportado por este aparelho.",
            "2017": "Esse aparelho não aceita o formato de comando padrão.",
            "28841105": "O trial grátis do IoT Core expirou — renove em Cloud Services → IoT Core.",
            "28841002": "O trial grátis do IoT Core expirou — renove em Cloud Services → IoT Core.",
        }
        return hints.get(code) or f"{msg} (código {code})".strip()
    return str(err)


# ---------------------------------------------------------------- conta Positivo
POS = {"client": None, "error": ""}


def is_pos(dev_id):
    return str(dev_id).startswith("pos:")


def positivo_devices(force=False):
    cl = POS["client"]
    if not cl:
        return []
    try:
        devs = cl.devices(force=force)
        POS["error"] = ""
        return devs
    except Exception as e:
        POS["error"] = str(e)
        print("[Positivo]", e)
        return cl._cache            # mantém o último estado conhecido


def start_positivo():
    p = CFG.get("positivo") or {}
    if not p.get("email") or not p.get("password"):
        POS["client"] = None
        return
    try:
        import positivo
        POS["client"] = positivo.Positivo(p["email"], positivo.unprotect(p["password"]))
    except Exception as e:
        POS.update(client=None, error=str(e))


def connect_positivo(email, password):
    import positivo
    if not password and CFG["positivo"].get("password") and email == CFG["positivo"].get("email"):
        password = positivo.unprotect(CFG["positivo"]["password"])
    cl = positivo.Positivo(email, password)
    try:
        cl.login()
        devs = cl.devices(force=True)
    except Exception as e:
        return dict(ok=False, error=str(e))
    CFG["positivo"] = {"email": email.strip(), "password": positivo.protect(password)}
    save_config()
    POS.update(client=cl, error="")
    return dict(ok=True, count=len(devs), names=[d["name"] for d in devs])


# ---------------------------------------------------------------- controle global
BACK = None
SETUP = {"tuya": None}
ON_SHOW = None   # main.py registra aqui como trazer a janela para frente
ON_QUIT = None
ON_THEME = None  # main.py aplica o tema (claro/escuro) no acrylic
LOCK = threading.Lock()


def make_backend():
    global BACK
    start_positivo()
    if CFG.get("demo"):
        BACK = Demo()
    elif CFG.get("configured") and CFG["access_id"] and CFG["access_secret"]:
        try:
            BACK = Tuya(CFG["api_region"], CFG["access_id"], CFG["access_secret"])
        except Exception as e:
            print("Falha ao conectar na Tuya:", e)
            BACK = None
            return str(e)
    else:
        BACK = None
    return None


def test_connection(key, secret, region="auto"):
    """Testa as chaves; com region=auto tenta todos os data centers."""
    try:
        import tinytuya  # noqa
    except ImportError:
        return dict(ok=False, error="Biblioteca tinytuya não instalada.")
    order = [r for r, _ in REGIONS] if region == "auto" else [region]
    first_ok, last_err = None, "Não foi possível conectar."
    for r in order:
        try:
            t = Tuya(r, key, secret, detect=False)
        except Exception as e:
            last_err = str(e)
            continue
        try:
            n = len(t.devices(force=True, raise_errors=True))
        except Exception as e:
            last_err = str(e)
            first_ok = first_ok or (r, t)
            continue
        if n:
            SETUP["tuya"] = t
            CFG.update(api_region=r, access_id=key, access_secret=secret)
            return dict(ok=True, region=r, count=n, linked=True)
        first_ok = first_ok or (r, t)
    if first_ok:
        r, t = first_ok
        SETUP["tuya"] = t
        CFG.update(api_region=r, access_id=key, access_secret=secret)
        return dict(ok=True, region=r, count=0, linked=False)
    return dict(ok=False, error=last_err)


def public_config():
    return dict(positivo_email=CFG["positivo"].get("email", ""), positivo_on=POS["client"] is not None,
                configured=CFG["configured"], demo=CFG["demo"], region=CFG["api_region"],
                access_id=CFG["access_id"], has_secret=bool(CFG["access_secret"]),
                ui=CFG["ui"], ac=CFG["ac"], regions=REGIONS, version=VERSION)


# ---------------------------------------------------------------- HTTP
APP_HEADER = "X-Controle-Casa"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, name, ctype):
        with open(os.path.join(RES, name), "rb") as f:
            return self._send(200, f.read(), ctype)

    # ---- segurança: só aceita pedidos do próprio PC e do próprio app
    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost")      # barra "DNS rebinding"

    def _from_app(self):
        # sites abertos no navegador não conseguem mandar este cabeçalho para cá (CORS)
        return self.headers.get(APP_HEADER) == "1"

    def _deny(self):
        self._send(403, {"error": "acesso negado"})

    def do_GET(self):
        if not self._host_ok():
            return self._deny()
        try:
            p = self.path.split("?")[0]
            if p in ("/api/quit", "/api/show") and not self._from_app():
                return self._deny()
            if p in ("/", "/index.html"):
                return self._file("index.html", "text/html")
            if p == "/icone.png":
                return self._file("icone.png", "image/png")
            if p == "/api/config":
                return self._send(200, public_config())
            if p == "/api/state":
                if BACK is None:
                    return self._send(200, dict(needs_setup=True, error=getattr(Handler, "boot_error", None)))
                return self._send(200, BACK.state())
            if p == "/api/setup/discover":
                src = BACK if (BACK is not None and SETUP["tuya"] is None) else (SETUP["tuya"] or BACK)
                if src is None:
                    return self._send(400, {"error": "Conecte a conta primeiro."})
                return self._send(200, src.discover())
            if p == "/api/version":
                return self._send(200, {"version": VERSION})
            if p == "/api/diag":
                if BACK is None:
                    return self._send(200, {"text": "Nenhuma conta conectada."})
                return self._send(200, {"text": BACK.diagnose()})
            if p == "/api/quit":
                self._send(200, {"ok": True})
                if ON_QUIT:
                    threading.Timer(0.2, ON_QUIT).start()
                return
            if p == "/api/show":
                if ON_SHOW:
                    ON_SHOW()
                return self._send(200, {"ok": True})
            if p == "/api/debug":
                return self._send(200, BACK.debug() if BACK else {})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        global BACK
        if not self._host_ok() or not self._from_app():
            return self._deny()
        try:
            p, b = self.path, self._body()
            if p == "/api/ac/test":
                if BACK is None:
                    return self._send(200, {"text": "Nenhuma conta conectada."})
                if isinstance(BACK, Demo):
                    return self._send(200, {"text": "Modo demonstração: não há ar de verdade para testar."})
                return self._send(200, {"text": BACK.test_ac()})
            if p == "/api/ac":
                return self._send(200, BACK.set_ac(b))
            if p == "/api/lights-off":
                return self._send(200, BACK.lights_off(b.get("room")))
            if p.startswith("/api/device/"):
                return self._send(200, BACK.set_device(p.split("/")[3], b))
            if p == "/api/setup/test":
                key, secret = (b.get("access_id") or "").strip(), (b.get("access_secret") or "").strip()
                if not secret and b.get("keep_secret"):
                    secret = CFG["access_secret"]
                if not key or not secret:
                    return self._send(200, dict(ok=False, error="Preencha o Access ID e o Access Secret."))
                CFG["demo"] = False
                return self._send(200, test_connection(key, secret, b.get("region", "auto")))
            if p == "/api/setup/demo":
                CFG["demo"] = True
                SETUP["tuya"] = None
                BACK = Demo()
                return self._send(200, {"ok": True})
            if p == "/api/setup/save":
                with LOCK:
                    for k in ("names", "channel_names", "hide", "room_sensor_id", "device_room", "rooms"):
                        if k in b:
                            CFG[k] = b[k]
                    if "ac" in b:
                        CFG["ac"] = _merge(CFG["ac"], b["ac"])
                    CFG["configured"] = True
                    save_config()
                    if CFG["demo"]:
                        BACK = BACK if isinstance(BACK, Demo) else Demo()
                    else:
                        SETUP["tuya"] = None
                        err = make_backend()
                        if err:
                            return self._send(200, dict(ok=False, error=err))
                return self._send(200, {"ok": True})
            if p == "/api/ui":
                CFG["ui"] = _merge(CFG["ui"], b)
                save_config()
                if "theme" in b and ON_THEME:
                    ON_THEME()
                return self._send(200, CFG["ui"])
            if p == "/api/positivo/connect":
                email = (b.get("email") or "").strip()
                if not email:
                    return self._send(200, dict(ok=False, error="Preencha o e-mail."))
                return self._send(200, connect_positivo(email, b.get("password") or ""))
            if p == "/api/positivo/disconnect":
                CFG["positivo"] = {"email": "", "password": ""}
                save_config()
                POS.update(client=None, error="")
                return self._send(200, {"ok": True})
            if p == "/api/reset":
                CFG.update(configured=False, demo=False, access_secret="")
                save_config()
                BACK, SETUP["tuya"] = None, None
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})


def start_server():
    """Sobe o servidor numa thread. Retorna None se já tiver outra instância rodando."""
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        return None
    Handler.boot_error = make_backend()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
