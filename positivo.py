# -*- coding: utf-8 -*-
"""
Conta Positivo Casa Inteligente.

O app da Positivo roda sobre a nuvem da Tuya, mas numa "ilha" separada do Smart Life.
Este módulo entra na conta Positivo do mesmo jeito que o app do celular (e-mail e senha),
lista os aparelhos e manda comandos. Os aparelhos continuam no app da Positivo, então
a Alexa segue funcionando como antes.

Não é uma API oficial: se a Positivo mudar o app, essa conexão pode parar de funcionar.
"""
import base64, hashlib, hmac, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid

ENDPOINT = "https://a1.tuyaus.com/api.json"
CLIENT_ID = "qk4c93xsttfjsm4ktr94"
APP_KEY = "A_9v5dh8yngmhtxdjen7t7qqx5ggmdf5su_w7vm9gr7meys387fpj7y7yrvx8w554cd"
COUNTRY = "55"
DEVICE_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "controlecasa.positivo").hex + uuid.uuid4().hex[:12]
SIGN_KEYS = {"a", "v", "lat", "lon", "lang", "deviceId", "imei", "imsi", "appVersion", "ttid", "isH5",
             "h5Token", "os", "clientId", "postData", "time", "requestId", "n4h5", "sid", "sp", "et"}
PREFIX = "pos:"

PRODUCT_HINT = {"pq860vo9ib50jhud": "cz", "lwpag3bu0faaowlj": "wnykq"}


class PositivoError(Exception):
    pass


class SessionExpired(PositivoError):
    pass


# ======================================================================== cores (formato bruto dos aparelhos)
def _hsv_to_rgb(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def colour_from_raw(code, raw):
    """'hhhhssssvvvv' (v2) ou 'rrggbbhhhhssvv' (v1) -> {'h','s','v'} no formato da nuvem oficial."""
    try:
        if isinstance(raw, dict):
            return raw
        if code.endswith("v2") and len(raw) >= 12:
            return {"h": int(raw[0:4], 16), "s": int(raw[4:8], 16), "v": int(raw[8:12], 16)}
        if len(raw) >= 14:
            return {"h": int(raw[6:10], 16), "s": int(raw[10:12], 16), "v": int(raw[12:14], 16)}
    except (TypeError, ValueError):
        pass
    return {"h": 0, "s": 0, "v": 0}


def colour_to_raw(code, value):
    v = value if isinstance(value, dict) else json.loads(value)
    h, s, val = int(v.get("h", 0)), int(v.get("s", 0)), int(v.get("v", 0))
    if code.endswith("v2"):
        return f"{h:04x}{s:04x}{val:04x}"
    r, g, b = _hsv_to_rgb(h, s / 255, val / 255)
    return f"{r:02x}{g:02x}{b:02x}{h:04x}{s:02x}{val:02x}"


# ======================================================================== cliente
class Positivo:
    def __init__(self, email, password):
        self.email, self.password = email.strip(), password
        self.sid = None
        self.lock = threading.Lock()
        self.schemas = {}       # devId -> {dp_id: code}
        self.codes = {}         # devId -> {code: dp_id}
        self.meta = {}          # devId -> {name, productId, gw}
        self._cache, self._cache_t = [], 0
        self.errors = []

    # ---------- protocolo do app
    @staticmethod
    def _post_hash(data):
        h = hashlib.md5(data.encode("utf-8")).hexdigest()
        return h[8:16] + h[0:8] + h[24:32] + h[16:24]

    def _sign(self, params):
        parts = []
        for k in sorted(params):
            v = params[k]
            if k not in SIGN_KEYS or v is None or str(v) == "":
                continue
            parts.append(f"{k}={self._post_hash(v) if k == 'postData' else v}")
        return hmac.new(APP_KEY.encode(), "||".join(parts).encode(), hashlib.sha256).hexdigest()

    def _call(self, action, post=None, version="1.0", sid=True, extra=None):
        params = {
            "a": action, "v": version, "clientId": CLIENT_ID, "deviceId": DEVICE_ID,
            "appVersion": "1.1.6", "appRnVersion": "5.14", "channel": "oem", "platform": "Linux",
            "os": "Android", "osSystem": "9", "lang": "pt", "timeZoneId": "America/Sao_Paulo",
            "ttid": "sdk_tuya@" + CLIENT_ID, "et": "0.0.1", "sdkVersion": "3.10.0",
            "requestId": str(uuid.uuid4()), "time": str(int(time.time())), **(extra or {}),
        }
        if sid:
            if not self.sid:
                self.login()
            params["sid"] = self.sid
        body = {}
        if post is not None:
            body["postData"] = json.dumps(post, separators=(",", ":"))
        params["sign"] = self._sign(dict(params, **body))
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode() if body else b"",
                                     headers={"User-Agent": "TY-UA=APP/Android/1.1.6/SDK/null",
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise PositivoError(f"A nuvem da Positivo recusou a conexão (HTTP {e.code}).")
        except Exception as e:
            raise PositivoError("Sem conexão com a nuvem da Positivo. Confira a internet do PC."
                                + (f" ({e})" if str(e) else ""))
        if res.get("success"):
            return res.get("result")
        code, msg = res.get("errorCode", ""), res.get("errorMsg", "")
        if code == "USER_SESSION_INVALID" or code == "USER_SESSION_LOSS":
            raise SessionExpired(msg)
        raise PositivoError(_explain(code, msg))

    def api(self, action, post=None, version="1.0", extra=None):
        with self.lock:
            try:
                return self._call(action, post, version, extra=extra)
            except SessionExpired:
                self.sid = None
                return self._call(action, post, version, extra=extra)

    def login(self):
        tok = self._call("tuya.m.user.email.token.create", {"countryCode": COUNTRY, "email": self.email}, sid=False)
        md5 = hashlib.md5(self.password.encode("utf-8")).hexdigest().encode()
        n, e = int(tok["publicKey"]), int(tok["exponent"])
        enc = pow(int.from_bytes(md5, "big"), e, n)
        passwd = "0" * 64 + enc.to_bytes((enc.bit_length() + 7) // 8, "big").hex()
        res = self._call("tuya.m.user.email.password.login", {
            "countryCode": COUNTRY, "email": self.email, "ifencrypt": 1, "options": '{"group": 1}',
            "passwd": passwd, "token": tok["token"]}, sid=False)
        self.sid = res["sid"]
        return True

    # ---------- aparelhos
    def _learn(self, dev_id):
        info = self.api("tuya.m.device.get", {"devId": dev_id})
        schema = info.get("schema") or "[]"
        schema = json.loads(schema) if isinstance(schema, str) else schema
        ids = {str(s["id"]): s["code"] for s in schema if "id" in s and "code" in s}
        self.schemas[dev_id] = ids
        self.codes[dev_id] = {c: i for i, c in ids.items()}
        self.meta[dev_id] = {"name": info.get("name", "?"), "productId": info.get("productId", ""),
                             "category": info.get("category", ""), "gw": info.get("devId", dev_id)}
        return info

    def _normalize(self, dev_id, dps, online):
        ids, meta = self.schemas.get(dev_id, {}), self.meta.get(dev_id, {})
        status = []
        for dp, val in (dps or {}).items():
            code = ids.get(str(dp))
            if not code:
                continue
            if code.startswith("colour_data"):
                val = colour_from_raw(code, val)
            status.append({"code": code, "value": val})
        codes = set(ids.values())
        cat = meta.get("category") or PRODUCT_HINT.get(meta.get("productId"), "")
        if not cat:
            name = meta.get("name", "").lower()
            if "switch_led" in codes:
                cat = "dj"
            elif codes & {"cur_power", "cur_current", "add_ele"} or "tomada" in name or "plug" in name:
                cat = "cz"
            elif codes & {"ir_send", "control", "study_code"}:
                cat = "wnykq"
            else:
                cat = "kg"
        return {"id": PREFIX + dev_id, "name": meta.get("name", "?"), "online": bool(online),
                "category": cat, "status": status, "source": "positivo"}

    def devices(self, force=False):
        if not force and time.time() - self._cache_t < 6:
            return self._cache
        out = []
        groups = self.api("tuya.m.location.list") or []
        for g in groups:
            gid = g.get("groupId") or g.get("gid") or g.get("id")
            for d in self.api("tuya.m.my.group.device.list", extra={"gid": str(gid)}) or []:
                did = d.get("devId")
                if not did:
                    continue
                if did not in self.schemas:
                    d = {**self._learn(did), **d}
                dps = d.get("dps")
                if dps is None:
                    dps = self.api("tuya.m.device.dp.get", {"devId": did}) or {}
                out.append(self._normalize(did, dps, d.get("isOnline", d.get("online", True))))
        self._cache, self._cache_t = out, time.time()
        return out

    def status(self, dev_id):
        raw = dev_id[len(PREFIX):] if dev_id.startswith(PREFIX) else dev_id
        for d in self.devices():
            if d["id"] == PREFIX + raw:
                return {s["code"]: s["value"] for s in d["status"]}
        return {}

    def send(self, dev_id, cmds):
        """cmds no formato da nuvem oficial [{'code','value'}] -> dps brutos do aparelho."""
        raw = dev_id[len(PREFIX):] if dev_id.startswith(PREFIX) else dev_id
        if raw not in self.codes:
            self._learn(raw)
        cmap, dps = self.codes[raw], {}
        for c in cmds:
            dp = cmap.get(c["code"])
            if dp is None:
                continue
            val = c["value"]
            if c["code"].startswith("colour_data"):
                val = colour_to_raw(c["code"], val)
            dps[dp] = val
        if not dps:
            raise PositivoError("Esse aparelho não tem essa função.")
        gw = self.meta.get(raw, {}).get("gw", raw)
        self.api("tuya.m.device.dp.publish", {"devId": raw, "gwId": gw, "dps": json.dumps(dps)})
        self._cache_t = 0


def _explain(code, msg):
    known = {
        "USER_PASSWD_WRONG": "E-mail ou senha da Positivo incorretos.",
        "USER_NAME_IS_NOT_EXIST": "Não existe conta Positivo com esse e-mail.",
        "ILLEGAL_CLIENT_ID": "A Positivo mudou o app e essa conexão não funciona mais.",
        "SING_VALIDATE_FALED": "A Positivo mudou o app e essa conexão não funciona mais.",
        "SIGN_VALIDATE_FAILED": "A Positivo mudou o app e essa conexão não funciona mais.",
        "API_OR_API_VERSION_WRONG": "A Positivo mudou o app e essa conexão não funciona mais.",
        "DEVICE_NOT_ONLINE": "O aparelho está offline.",
    }
    return known.get(code) or f"{msg or 'Erro'} ({code})"
