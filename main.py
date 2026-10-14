# -*- coding: utf-8 -*-
"""
Controle Casa - programa de PC.

A janela usa o material Desktop Acrylic do próprio Windows (DWM): o compositor do sistema
desfoca, em tempo real, as janelas e imagens que estão atrás do app. A interface (WebView2)
é desenhada com fundo transparente por cima desse material.

Windows 11 22H2+ : DWMWA_SYSTEMBACKDROP_TYPE = DWMSBT_TRANSIENTWINDOW (Desktop Acrylic)
Windows 10 / 11 21H2: SetWindowCompositionAttribute com ACCENT_ENABLE_ACRYLICBLURBEHIND
"""
import os, subprocess, sys, threading, time, webbrowser

import core

TITLE = "Controle Casa"
APP_NAME = "ControleCasa"
W, H = 384, 820
STATE = {"window": None, "icon": None, "ac_on": False, "quitting": False}
IS_WIN = sys.platform == "win32"


# ======================================================================== Win32 / DWM
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    gdi32 = ctypes.windll.gdi32

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SendMessageW.restype = LRESULT
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    class MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

    class DWM_BLURBEHIND(ctypes.Structure):
        _fields_ = [("dwFlags", wintypes.DWORD), ("fEnable", wintypes.BOOL),
                    ("hRgnBlur", wintypes.HANDLE), ("fTransitionOnMaximized", wintypes.BOOL)]

    class ACCENT_POLICY(ctypes.Structure):
        _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

    class WINCOMPATTRDATA(ctypes.Structure):
        _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]

GWLP_WNDPROC = -4
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WM_NCCALCSIZE = 0x0083
WM_NCACTIVATE = 0x0086
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_TRANSIENTWINDOW = 3     # = Desktop Acrylic


def win_build():
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


def system_is_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return True


def wants_dark():
    t = core.CFG["ui"].get("theme", "auto")
    return system_is_dark() if t == "auto" else t == "dark"


def _dwm_int(h, attr, value):
    v = ctypes.c_int(value)
    return dwmapi.DwmSetWindowAttribute(h, attr, ctypes.byref(v), ctypes.sizeof(v))


class Acrylic:
    """Transforma a janela do WebView2 numa janela de vidro acrílico nativo, sem barra de título."""

    def __init__(self):
        self.h = 0
        self.proc = None
        self.old_proc = None
        self.mode = "none"
        self.dark = None

    # ---------- sem barra de título, mas mantendo sombra, cantos e animações nativas
    def _wndproc(self, h, msg, wp, lp):
        if msg == WM_NCCALCSIZE and wp:
            return 0                      # a área cliente ocupa a janela inteira
        if msg == WM_NCACTIVATE:
            lp = -1                       # não repinta a moldura antiga
        return user32.CallWindowProcW(self.old_proc, h, msg, wp, lp)

    def remove_titlebar(self):
        if self.proc:
            return
        self.proc = WNDPROC(self._wndproc)
        self.old_proc = user32.SetWindowLongPtrW(self.h, GWLP_WNDPROC, ctypes.cast(self.proc, ctypes.c_void_p))
        user32.SetWindowPos(self.h, None, 0, 0, 0, 0, SWP_FLAGS)

    # ---------- material
    def apply(self, h):
        self.h = h
        style = user32.GetWindowLongW(h, GWL_EXSTYLE)
        if style & WS_EX_LAYERED:          # janela "layered" não recebe backdrop do sistema
            user32.SetWindowLongW(h, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
        self.remove_titlebar()
        m = MARGINS(-1, -1, -1, -1)        # "folha de vidro": o material cobre a janela toda
        dwmapi.DwmExtendFrameIntoClientArea(h, ctypes.byref(m))
        bb = DWM_BLURBEHIND(0x1 | 0x2, True, gdi32.CreateRectRgn(0, 0, -1, -1), False)
        dwmapi.DwmEnableBlurBehindWindow(h, ctypes.byref(bb))   # liga o canal alfa da janela
        _dwm_int(h, DWMWA_WINDOW_CORNER_PREFERENCE, 2)          # cantos arredondados
        self.set_theme(force=True)

    def set_theme(self, force=False):
        if not self.h:
            return
        dark = wants_dark()
        if dark == self.dark and not force:
            return
        self.dark = dark
        _dwm_int(self.h, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        if win_build() >= 22621:
            _dwm_int(self.h, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)
            self.mode = "acrylic-dwm"
        else:
            self._accent_acrylic(dark)
            self.mode = "acrylic-legacy"

    def _accent_acrylic(self, dark):
        # Windows 10 / Win11 21H2: acrylic via SetWindowCompositionAttribute
        tint = 0x55101318 if dark else 0x55F3F3F3      # AABBGGRR
        accent = ACCENT_POLICY(4, 2, tint, 0)            # ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
        try:
            user32.SetWindowCompositionAttribute(self.h, ctypes.byref(data))
        except Exception as e:
            print("acrylic:", e)

    def drag(self):
        user32.ReleaseCapture()
        user32.SendMessageW(self.h, WM_NCLBUTTONDOWN, HTCAPTION, 0)


ACRYLIC = Acrylic()


def _hwnd():
    try:
        return int(STATE["window"].native.Handle.ToInt64())
    except Exception:
        try:
            return user32.FindWindowW(None, TITLE)
        except Exception:
            return 0


def on_ui_thread(fn):
    """Roda fn na thread da janela (obrigatório para mexer no WinForms)."""
    form = STATE["window"].native
    from System import Action
    form.Invoke(Action(fn))


def setup_black():
    """Fundo preto sólido: janela sem moldura, cantos arredondados e moldura escura do Windows."""
    h = _hwnd()
    if not h:
        return
    _dwm_int(h, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    _dwm_int(h, DWMWA_WINDOW_CORNER_PREFERENCE, 2)
    ACRYLIC.h = h
    ACRYLIC.mode = "black"


def setup_glass():
    h = _hwnd()
    if not h:
        return

    def ui():
        from System.Drawing import Color
        form = STATE["window"].native
        form.BackColor = Color.Black          # preto = transparente sobre o vidro do DWM
        ACRYLIC.apply(h)
        form.Invalidate()
    on_ui_thread(ui)
    threading.Thread(target=theme_watch, daemon=True).start()


def theme_watch():
    """Acompanha o tema do Windows (o pywebview também mexe no backdrop quando o tema muda)."""
    last = system_is_dark()
    while not STATE["quitting"]:
        time.sleep(1.5)
        cur = system_is_dark()
        if cur != last:
            last = cur
            time.sleep(0.4)
            on_ui_thread(lambda: ACRYLIC.set_theme(force=True))


def apply_theme():
    if STATE["window"] and ACRYLIC.h and ACRYLIC.mode != "black":
        on_ui_thread(lambda: ACRYLIC.set_theme(force=True))


APP_ID = "ControleCasa.Painel"


def app_identity():
    """Identidade própria na barra de tarefas: sem isso o Windows agrupa com o Python e usa o ícone dele."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def icon_path():
    for base in (core.RES, core.APP_DIR):
        p = os.path.join(base, "casa.ico")
        if os.path.exists(p):
            return p
    return None


def set_window_icon(h):
    """Garante a casinha na janela e na barra de tarefas (ícone grande e pequeno)."""
    p = icon_path()
    if not (IS_WIN and h and p):
        return
    try:
        LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x80
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        big = user32.LoadImageW(None, p, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, p, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if big:
            user32.SendMessageW(h, WM_SETICON, 1, big)
        if small:
            user32.SendMessageW(h, WM_SETICON, 0, small)
    except Exception as e:
        print("ícone:", e)


def promote_tray_icon():
    """Windows 11: tira a casinha dos 'ícones ocultos' (só na primeira vez; depois respeita o que você escolher)."""
    if not IS_WIN or core.CFG["ui"].get("tray_promoted"):
        return
    try:
        import winreg
        root = r"Control Panel\NotifyIconSettings"
        exe = os.path.basename(sys.executable).lower()
        done = False
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as s:
                        def val(n):
                            try:
                                return str(winreg.QueryValueEx(s, n)[0])
                            except OSError:
                                return ""
                        tip, path = val("InitialTooltip"), val("ExecutablePath").lower()
                        if tip == TITLE or (exe != "pythonw.exe" and path.endswith(exe)):
                            winreg.SetValueEx(s, "IsPromoted", 0, winreg.REG_DWORD, 1)
                            done = True
                except OSError:
                    pass
        if done:
            core.CFG["ui"]["tray_promoted"] = True
            core.save_config()
    except Exception as e:
        print("bandeja:", e)


def dpi_aware():
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


# ======================================================================== API para a interface
class Api:
    def drag(self):
        on_ui_thread(ACRYLIC.drag)

    def minimize(self):
        STATE["window"].minimize()

    def hide(self):
        STATE["window"].hide()

    def quit(self):
        quit_app()

    def open_url(self, url):
        webbrowser.open(url)

    def theme(self):
        return {"dark": wants_dark(), "mode": ACRYLIC.mode, "build": win_build()}

    def autostart(self, value=None):
        if value is not None and bool(value) != autostart_enabled():
            toggle_autostart()
        return autostart_enabled()


# ======================================================================== iniciar com o Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_cmd():
    if core.FROZEN:
        return f'"{sys.executable}" --bandeja'
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{exe}" "{os.path.abspath(__file__)}" --bandeja'


def autostart_enabled(*_):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except Exception:
        return False


def toggle_autostart(*_):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if autostart_enabled():
                winreg.DeleteValue(k, APP_NAME)
            else:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_cmd())
    except Exception as e:
        print("autostart:", e)
    if STATE["icon"]:
        STATE["icon"].update_menu()


def refresh_autostart_path():
    if not autostart_enabled():
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_cmd())
    except Exception:
        pass


# ======================================================================== bandeja
def draw_house(size=64, dot=False):
    from PIL import Image, ImageDraw
    S, w, white = 512, 46, (255, 255, 255, 255)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def line(pts):
        d.line(pts, fill=white, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=white)

    line([(44, 214), (256, 66), (468, 214)])
    line([(104, 250), (104, 444), (408, 444), (408, 250)])
    line([(352, 66), (412, 66), (412, 116)])
    if dot:
        d.ellipse([196, 300, 316, 420], fill=(79, 195, 247, 255))
    return im.resize((size, size), Image.LANCZOS)


def show_window(*_):
    w = STATE["window"]
    if w:
        w.show()
        w.restore()
    else:
        open_in_browser()


def open_in_browser():
    for e in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(e):
            subprocess.Popen([e, f"--app={core.URL}", f"--window-size={W},{H}"])
            return
    webbrowser.open(core.URL)


def refresh_icon():
    icon = STATE["icon"]
    if not icon:
        return
    try:
        if core.BACK:
            ac = core.BACK.state()["ac"]
            STATE["ac_on"] = bool(ac.get("power")) and ac.get("available", True)
    except Exception:
        pass
    icon.icon = draw_house(dot=STATE["ac_on"])
    icon.update_menu()


def toggle_ac(*_):
    if core.BACK:
        try:
            core.BACK.set_ac({"power": not STATE["ac_on"]})
        except Exception as e:
            print("ar:", e)
    refresh_icon()


def lights_off(*_):
    if core.BACK:
        threading.Thread(target=lambda: (core.BACK.lights_off(), refresh_icon()), daemon=True).start()


def quit_app(*_):
    STATE["quitting"] = True
    if STATE["icon"]:
        try:
            STATE["icon"].stop()
        except Exception:
            pass
    if STATE["window"]:
        try:
            STATE["window"].destroy()
        except Exception:
            pass
    os._exit(0)


def start_tray():
    try:
        import pystray
    except ImportError:
        return None
    menu = pystray.Menu(
        pystray.MenuItem("Abrir Controle Casa", show_window, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda i: "Desligar o ar" if STATE["ac_on"] else "Ligar o ar", toggle_ac,
                         enabled=lambda i: core.BACK is not None),
        pystray.MenuItem("Apagar todas as luzes", lights_off, enabled=lambda i: core.BACK is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Iniciar com o Windows", toggle_autostart, checked=autostart_enabled),
        pystray.MenuItem("Sair", quit_app),
    )
    icon = pystray.Icon(APP_NAME, draw_house(), TITLE, menu)
    STATE["icon"] = icon
    icon.run_detached()

    def loop():
        while True:
            refresh_icon()
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
    return icon


# ======================================================================== atualização: fecha a versão antiga
def _running_version():
    import json, urllib.request
    try:
        with urllib.request.urlopen(core.URL + "api/version", timeout=2) as r:
            return json.loads(r.read()).get("version", 0)
    except Exception:
        return 0


def _kill_port_owner():
    if not IS_WIN:
        return
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{core.PORT}") and parts[3].upper() in ("LISTENING", "ESCUTANDO"):
                pid = parts[4]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, creationflags=0x08000000)
    except Exception as e:
        print("atualização:", e)


def take_over_or_show():
    import urllib.request
    srv = core.start_server()
    if srv:
        return srv
    v = _running_version()
    if v and v >= core.VERSION:
        try:
            urllib.request.urlopen(urllib.request.Request(core.URL + "api/show", headers={core.APP_HEADER: "1"}), timeout=3)
        except Exception:
            pass
        return None
    if v:
        try:
            urllib.request.urlopen(urllib.request.Request(core.URL + "api/quit", headers={core.APP_HEADER: "1"}), timeout=2)
        except Exception:
            pass
    time.sleep(0.8)
    srv = core.start_server()
    if srv:
        return srv
    _kill_port_owner()
    for _ in range(10):
        time.sleep(0.5)
        srv = core.start_server()
        if srv:
            return srv
    return None


# ======================================================================== main
def main():
    dpi_aware()
    app_identity()
    start_hidden = "--bandeja" in sys.argv
    srv = take_over_or_show()
    if srv is None:
        return
    core.ON_SHOW = show_window
    core.ON_QUIT = quit_app
    core.ON_THEME = apply_theme
    refresh_autostart_path()
    start_tray()
    threading.Thread(target=lambda: [time.sleep(t) or promote_tray_icon() for t in (4, 20)], daemon=True).start()

    try:
        import webview
    except ImportError:
        if not start_hidden:
            open_in_browser()
        while True:
            time.sleep(3600)

    black = core.CFG["ui"].get("backdrop", "black") != "acrylic"
    if black:
        win = webview.create_window(
            TITLE, core.URL, width=W, height=H, min_size=(340, 520),
            frameless=True, easy_drag=False, resizable=True,
            background_color="#000000", js_api=Api(), hidden=start_hidden)
    else:
        win = webview.create_window(
            TITLE, core.URL, width=W, height=H, min_size=(340, 520),
            frameless=False, resizable=False, transparent=True,
            js_api=Api(), hidden=start_hidden)
    STATE["window"] = win

    def on_closing():
        if STATE["quitting"] or STATE["icon"] is None:
            return True
        win.hide()
        return False

    glass_done = {"ok": False}

    def on_shown():
        if glass_done["ok"] or not IS_WIN:
            return
        glass_done["ok"] = True
        time.sleep(0.05)
        set_window_icon(_hwnd())
        try:
            setup_black() if black else setup_glass()
        except Exception as e:
            print("vidro:", e)

    win.events.closing += on_closing
    win.events.shown += on_shown
    try:
        webview.start(private_mode=False, storage_path=os.path.join(core.CONF_DIR, "webview"), icon=icon_path())
    except TypeError:   # pywebview antigo, sem o parâmetro icon
        webview.start(private_mode=False, storage_path=os.path.join(core.CONF_DIR, "webview"))
    quit_app()


if __name__ == "__main__":
    main()
