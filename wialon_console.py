"""
Consola de diagnostico Wialon Remote API.

Basado 100% en la documentacion oficial de Wialon Remote API:
  - token/login               https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/token/login
  - token/list                 https://sdk.wialon.com/wiki/en/kit/remoteapi/apiref/token/list
  - token/update (login.html)  https://wialon.com/storage/old_en/2015/07/New-Wialon-Authorization-Method_EN.pdf
  - core/logout                (mismo formato general del API)
  - core/search_items          https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/core/search_items
  - core/get_hw_types          https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/core/get_hw_types
  - unit/calc_last_message     https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/unit/calc_last_message
  - Formato de tokens          https://help.wialon.com/en/api/user-guide/data-format/tokens
  - Codigos de error           https://sdk.wialon.com/wiki/en/kit/remoteapi/apiref/errors/errors

No se persiste informacion de unidades en disco: todo vive en memoria mientras
la app esta conectada y se descarta al desconectar/cerrar. El panel de LOG solo
muestra en pantalla la trama cruda (request/response) para validar la conexion.

Vigencia del token: se obtiene con token/list (mas confiable que el campo 'tk'
embebido en la respuesta de token/login, que algunos hostings no rellenan) y se
localiza la entrada cuyo hash 'h' coincide con el token usado para conectar.
"""

import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from urllib.parse import quote as url_quote
from tkinter import messagebox, scrolledtext, ttk

import requests

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(APP_DIR, "icon.ico")

APP_TITLE = "Consola Wialon"
DEFAULT_HOST = "https://hst-api.wialon.com"
DEFAULT_TOKEN_PORTAL = "https://gps.epcom.net"
EXPECTED_TOKEN_LEN = 72  # "unique token name, 72 symbols" (doc. token/login)

# --- Flags de datos para core/search_items (avl_unit), segun la documentacion
# oficial (help.wialon.com/.../data-format/units, confirmado contra Item.java /
# Unit.java del SDK):
#   base(0x1) + customFields(0x8) + advancedProps/uid-ph-hw(0x100)
#   + lastMessage(0x400) + sensors(0x1000) + counters(0x2000)
#   + messageParams(0x100000) + position(0x400000) + profileFields(0x800000)
UNIT_FLAGS = (
    0x1 | 0x8 | 0x100 | 0x400 | 0x1000 | 0x2000 | 0x100000 | 0x400000 | 0x800000
)

# Flags de respuesta para token/login: base(0x1) + user(0x2) + token info(0x4)
LOGIN_FLAGS = 0x1 | 0x2 | 0x4

# Codigos de error documentados del Remote API
WIALON_ERRORS = {
    0: "Operacion exitosa",
    1: "Sesion invalida",
    2: "Nombre de servicio invalido",
    3: "Resultado invalido",
    4: "Entrada invalida",
    5: "Error al ejecutar la solicitud",
    6: "Error desconocido",
    7: "Acceso denegado",
    8: "Usuario o contrasena invalidos (token invalido)",
    9: "Servidor de autorizacion no disponible, intente mas tarde",
    10: "Se alcanzo el limite de solicitudes concurrentes",
    11: "Error al restablecer contrasena",
    14: "Error de facturacion",
    1001: "No hay mensajes para el intervalo seleccionado",
    1002: "El elemento ya existe o excede restricciones de facturacion",
    1003: "Solo se permite una solicitud de este tipo por el momento",
    1004: "Se excedio el limite de mensajes",
    1005: "Se excedio el tiempo limite de ejecucion",
    1006: "Se excedio el limite de intentos de codigo de doble factor",
    1011: "Su IP cambio o la sesion expiro",
}

# Bits de alcance (fl) de un token, documentados oficialmente en el PDF
# "New Wialon Authorization Method" (Apendice) y en help.wialon.com/.../
# data-format/tokens. Mismos bits para: crear un token (login.html/token/
# update) y para leer el alcance ya otorgado (token/list, tk en token/login).
TOKEN_ACCESS_FLAGS = [
    (0x100, "Rastreo en linea (ver unidades, informes, mensajes, POIs, geocercas)"),
    (0x200, "Ver datos (notificaciones, tareas, mantenimiento, actuar como usuario)"),
    (0x400, "Editar datos de bajo perfil (renombrar, campos, POIs, geocercas, comandos)"),
    (0x800, "Editar datos importantes (permisos, notificaciones, choferes, plantillas)"),
    (0x1000, "Editar datos criticos (eliminar unidad, sensores, contadores, mensajes)"),
    (0x2000, "Ejecutar comandos"),
]
TOKEN_FULL_ACCESS = -1

# Duraciones sugeridas para generar un token nuevo (segundos). El maximo
# documentado es 8,640,000 s = 100 dias; con 0 el token "no expira" pero
# Wialon lo borra igual si pasa 100 dias sin usarse.
DURATION_PRESETS = [
    ("1 dia", 86400),
    ("7 dias", 604800),
    ("30 dias (recomendado)", 2592000),
    ("100 dias (maximo)", 8640000),
    ("Sin expiracion fija*", 0),
]

SENSOR_NA_VALUE = -348201.3876  # valor "No disponible" documentado para sensores

# ---------------------------------------------------------------------------
# Paleta y tipografia (un solo acento, base neutra, sin morados de IA)
# ---------------------------------------------------------------------------
BG_APP = "#eef1f5"
HEADER_BG = "#12172a"
HEADER_BG_2 = "#1c2340"
SURFACE = "#ffffff"
ROW_ALT = "#f5f6f9"
BORDER = "#dde1e8"
HEAD_ROW_BG = "#f1f3f7"
TEXT_PRIMARY = "#151a26"
TEXT_MUTED = "#6b7280"
TEXT_ON_DARK = "#f5f7fb"
TEXT_ON_DARK_MUTED = "#93a0bd"
ACCENT = "#2f6fed"
ACCENT_DARK = "#1f4fc4"
ACCENT_SOFT = "#dbe6fe"
SUCCESS = "#1f9d55"
DANGER = "#dc2626"
DANGER_DARK = "#b91c1c"
WARNING = "#b45309"

STATUS_COLORS = {
    "verde": SUCCESS,
    "amarillo": "#eab308",
    "rojo": DANGER,
    "gris": "#9aa4b2",
}

FONT = "Segoe UI"
FONT_MONO = "Consolas"


def make_dot_image(color, bg, size=12):
    """Genera un icono de circulo solido (color de estado) en memoria, sin
    dependencias externas. Tk no renderiza emoji a color (solo contornos), asi
    que esta es la unica forma confiable de mostrar un indicador con color real
    dentro de una celda de Treeview."""
    radius = size / 2 - 1
    cx = cy = (size - 1) / 2
    img = tk.PhotoImage(width=size, height=size)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            row.append(color if dx * dx + dy * dy <= radius * radius else bg)
        rows.append(row)
    img.put(rows)
    return img


class AutoScrollbar(ttk.Scrollbar):
    """Scrollbar que se auto-oculta cuando todo el contenido ya es visible,
    para no mostrar una barra de desplazamiento lateral/vertical innecesaria."""

    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        ttk.Scrollbar.set(self, lo, hi)


def redact(value, keep=4):
    """Oculta parcialmente credenciales/sesiones antes de mostrarlas en el LOG."""
    if not value:
        return value
    s = str(value)
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}…{s[-keep:]}"


class WialonError(Exception):
    def __init__(self, code, extra=None):
        self.code = code
        self.extra = extra
        desc = WIALON_ERRORS.get(code, "Error no documentado")
        super().__init__(f"[{code}] {desc}")


def parse_token_info(raw_tk):
    """Normaliza el campo 'tk' del login (a veces viene como JSON-string)."""
    if raw_tk is None:
        return None
    if isinstance(raw_tk, str):
        try:
            raw_tk = json.loads(raw_tk)
        except (ValueError, TypeError):
            return None
    if isinstance(raw_tk, dict):
        return raw_tk
    return None


def decode_token_scope(fl):
    if fl is None:
        return ["Desconocido (el servidor no incluyo el detalle)"]
    if fl == TOKEN_FULL_ACCESS:
        return ["Acceso completo (todos los permisos)"]
    granted = [label for bit, label in TOKEN_ACCESS_FLAGS if fl & bit]
    return granted or [f"Sin permisos reconocidos (fl={fl})"]


def human_duration(seconds):
    seconds = int(seconds)
    if seconds <= 0:
        return "0 min"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours:
        parts.append(f"{hours} h")
    if not days and minutes:
        parts.append(f"{minutes} min")
    return " ".join(parts) if parts else "menos de 1 min"


def build_token_summary(token_info):
    """Convierte el objeto 'tk' crudo en textos listos para mostrar/validar."""
    if not token_info:
        return None

    app = token_info.get("app") or "-"
    ct = token_info.get("ct") or 0
    at = token_info.get("at") or 0
    dur = token_info.get("dur") or 0
    fl = token_info.get("fl")
    items = token_info.get("items") or []

    created_str = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M:%S") if ct else "-"
    activation_str = (
        datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M:%S")
        if at
        else "Inmediata (desde la creacion)"
    )

    expiry_ts = None
    remaining_s = None
    if dur > 0:
        base_ts = at if at else ct
        if base_ts:
            expiry_ts = base_ts + dur
            remaining_s = expiry_ts - time.time()
        duration_str = human_duration(dur) + " desde la activacion"
    else:
        duration_str = "Sin duracion fija (se autoelimina si no se usa en 100 dias)"

    if expiry_ts:
        expiry_str = datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M:%S") + " (aprox.)"
    else:
        expiry_str = "Sin vencimiento fijo"

    scope_lines = decode_token_scope(fl)
    if items:
        restriction_str = f"Limitado a {len(items)} elemento(s): {', '.join(str(i) for i in items[:20])}"
        if len(items) > 20:
            restriction_str += ", ..."
    else:
        restriction_str = "Sin restriccion (todos los elementos accesibles al usuario)"

    ll = token_info.get("ll") or 0
    last_used_str = datetime.fromtimestamp(ll).strftime("%Y-%m-%d %H:%M:%S") if ll else "-"

    return {
        "app": app,
        "created_str": created_str,
        "activation_str": activation_str,
        "duration_str": duration_str,
        "expiry_str": expiry_str,
        "remaining_s": remaining_s,
        "scope_lines": scope_lines,
        "restriction_str": restriction_str,
        "last_used_str": last_used_str,
        "raw": token_info,
    }


class WialonClient:
    """Cliente minimo del Wialon Remote API (solo lo que la consola necesita)."""

    def __init__(self, host, log_fn=None):
        self.host = host.rstrip("/")
        self.sid = None
        self.username = None
        self.token_info = None
        self.log_fn = log_fn or (lambda *_: None)

    def _url(self):
        return f"{self.host}/wialon/ajax.html"

    def _call(self, svc, params, use_sid=True, redact_keys=()):
        query = {"svc": svc, "params": json.dumps(params, ensure_ascii=False)}
        if use_sid and self.sid:
            query["sid"] = self.sid

        log_params = dict(params)
        for k in redact_keys:
            if k in log_params:
                log_params[k] = redact(log_params[k])
        log_sid = redact(self.sid) if (use_sid and self.sid) else None
        self.log_fn(
            "REQ",
            f"svc={svc} params={json.dumps(log_params, ensure_ascii=False)}"
            + (f" sid={log_sid}" if log_sid else ""),
        )

        resp = requests.get(self._url(), params=query, timeout=20)
        resp.raise_for_status()
        text = resp.text
        shown = text if len(text) <= 6000 else text[:6000] + " …(truncado)"
        self.log_fn("RESP", shown)

        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            raise WialonError(data["error"], data)
        return data

    def login(self, token):
        data = self._call(
            "token/login",
            {"token": token, "fl": LOGIN_FLAGS},
            use_sid=False,
            redact_keys=("token",),
        )
        self.sid = data.get("eid")
        self.username = (data.get("user") or {}).get("nm") or data.get("au")
        self.token_info = parse_token_info(data.get("tk"))
        if not self.sid:
            raise WialonError(1, data)
        return data

    def logout(self):
        try:
            self._call("core/logout", {})
        finally:
            self.sid = None
            self.username = None
            self.token_info = None

    def search_units(self):
        data = self._call(
            "core/search_items",
            {
                "spec": {
                    "itemsType": "avl_unit",
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name",
                },
                "force": 1,
                "flags": UNIT_FLAGS,
                "from": 0,
                "to": 0,
            },
        )
        return data.get("items") or []

    def get_hw_types(self):
        data = self._call("core/get_hw_types", {})
        result = {}
        if isinstance(data, list):
            for hw in data:
                if isinstance(hw, dict) and "id" in hw:
                    result[hw["id"]] = hw.get("name", str(hw["id"]))
        return result

    def calc_last_message(self, unit_id, sensor_ids):
        return self._call(
            "unit/calc_last_message", {"unitId": unit_id, "sensors": list(sensor_ids)}
        )

    def list_tokens(self):
        """token/list: fuente autoritativa de vigencia/alcance por token (mas
        confiable que el campo 'tk' embebido en token/login, que no siempre
        viene relleno segun el hosting)."""
        data = self._call("token/list", {})
        return data if isinstance(data, list) else []


def calc_sensor_value(sensor, params):
    """Calcula el valor de un sensor a partir del parametro crudo y su tabla de
    calculo (tbl: [{x, a, b}] => y = a*x + b por tramos), tal como documenta
    Wialon en 'Sensors: Calculation table explained' y el esquema de
    unit/update_sensor."""
    p = sensor.get("p")
    if not p or p not in params:
        return None
    try:
        x = float(params[p])
    except (TypeError, ValueError):
        return None

    tbl = sensor.get("tbl") or []
    if tbl:
        applicable = None
        for entry in sorted(tbl, key=lambda e: e.get("x", 0)):
            if x >= entry.get("x", 0):
                applicable = entry
        if applicable is None:
            applicable = tbl[0]
        a = applicable.get("a", 1)
        b = applicable.get("b", 0)
        return a * x + b
    return x


def find_ignition_sensor(unit):
    for sensor in (unit.get("sens") or {}).values():
        if sensor.get("t") == "engine operation":
            return sensor
    return None


def human_age(seconds):
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{int(seconds)} s"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} d"


def extract_token_from_text(text):
    """Saca el token de lo que el usuario pegue: la URL completa de retorno
    de login.html (…&access_token=XXXX&user_name=YYYY) o el token pelado."""
    text = (text or "").strip()
    match = re.search(r"access_token=([0-9A-Za-z]+)", text)
    if match:
        return match.group(1)
    return text


class App:
    MOVING_SPEED_KMH = 1  # pos.s > 1 km/h se considera "en movimiento"

    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1320x800")
        root.minsize(1040, 620)
        root.configure(bg=BG_APP)
        if os.path.exists(ICON_PATH):
            try:
                root.iconbitmap(default=ICON_PATH)
            except tk.TclError:
                pass

        self.client = None
        self.connected = False
        self.hw_types = {}
        self.units_by_id = {}
        self.result_queue = queue.Queue()
        self.refresh_job = None
        self.refresh_in_progress = False

        self._setup_style()
        self._build_status_icons()
        self._build_header()
        self._build_toolbar()
        self._build_notebook()
        self._build_status_bar()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        sys.excepthook = self._excepthook
        self.root.after(150, self._poll_queue)

    # ---------- estilo ----------

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.style = style

        style.configure(".", font=(FONT, 10), background=BG_APP, foreground=TEXT_PRIMARY)
        style.configure("TFrame", background=BG_APP)
        style.configure("TLabel", background=BG_APP, foreground=TEXT_PRIMARY)
        style.configure("Muted.TLabel", background=BG_APP, foreground=TEXT_MUTED, font=(FONT, 9))

        style.configure("Header.TFrame", background=HEADER_BG)
        style.configure("Header.TLabel", background=HEADER_BG, foreground=TEXT_ON_DARK)
        style.configure(
            "HeaderTitle.TLabel", background=HEADER_BG, foreground=TEXT_ON_DARK,
            font=(FONT, 15, "bold"),
        )
        style.configure(
            "HeaderMuted.TLabel", background=HEADER_BG, foreground=TEXT_ON_DARK_MUTED,
            font=(FONT, 9),
        )

        style.configure("Toolbar.TFrame", background=SURFACE)
        style.configure("Toolbar.TLabel", background=SURFACE, foreground=TEXT_PRIMARY, font=(FONT, 9))
        style.configure("ToolbarMuted.TLabel", background=SURFACE, foreground=TEXT_MUTED, font=(FONT, 9))
        style.configure("Divider.TFrame", background=BORDER)

        style.configure(
            "TEntry", fieldbackground="#ffffff", foreground=TEXT_PRIMARY,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=6,
        )
        style.map("TEntry", bordercolor=[("focus", ACCENT)])

        style.configure("TSpinbox", fieldbackground="#ffffff", padding=4, arrowsize=13)
        style.map("TSpinbox", bordercolor=[("focus", ACCENT)])

        style.configure(
            "TButton", font=(FONT, 9, "bold"), padding=(12, 7),
            background="#e5e8ee", foreground=TEXT_PRIMARY, borderwidth=0,
        )
        style.map("TButton", background=[("active", "#d5d9e2")])

        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_DARK), ("disabled", "#9db8f5")],
            foreground=[("disabled", "#eef2ff")],
        )

        style.configure("Danger.TButton", background=DANGER, foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", DANGER_DARK)])

        style.configure(
            "Ghost.TButton", background=HEADER_BG_2, foreground=TEXT_ON_DARK, padding=(10, 6),
        )
        style.map(
            "Ghost.TButton",
            background=[("active", "#28305a"), ("disabled", HEADER_BG)],
            foreground=[("disabled", TEXT_ON_DARK_MUTED)],
        )

        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT_PRIMARY, font=(FONT, 9))
        style.map("TCheckbutton", background=[("active", SURFACE)])

        style.configure("TNotebook", background=BG_APP, borderwidth=0, tabmargins=(10, 8, 10, 0))
        style.configure(
            "TNotebook.Tab", background=BG_APP, foreground=TEXT_MUTED,
            padding=(18, 9), font=(FONT, 10, "bold"), borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", SURFACE)],
            foreground=[("selected", TEXT_PRIMARY)],
        )

        style.configure(
            "Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT_PRIMARY,
            font=(FONT, 10), rowheight=27, borderwidth=0,
        )
        style.configure(
            "Treeview.Heading", background=HEAD_ROW_BG, foreground=TEXT_MUTED,
            font=(FONT, 9, "bold"), relief="flat", padding=(10, 8),
        )
        style.map("Treeview.Heading", background=[("active", "#e3e7ee")])
        style.map(
            "Treeview", background=[("selected", ACCENT_SOFT)],
            foreground=[("selected", TEXT_PRIMARY)],
        )

        style.configure("Vertical.TScrollbar", background="#d7dbe3", troughcolor=BG_APP, borderwidth=0, arrowsize=12)
        style.configure("Horizontal.TScrollbar", background="#d7dbe3", troughcolor=BG_APP, borderwidth=0, arrowsize=12)

    def _build_status_icons(self):
        """Un icono de circulo por (estado, fondo-de-fila) para que el color
        se vea solido tanto en filas pares/impares (zebra) como en la leyenda."""
        self.status_icons = {}
        for tag, color in STATUS_COLORS.items():
            for variant, bg in (("even", SURFACE), ("odd", ROW_ALT), ("app", BG_APP)):
                self.status_icons[(tag, variant)] = make_dot_image(color, bg, size=12)

    # ---------- construccion de UI ----------

    def _build_header(self):
        header = ttk.Frame(self.root, style="Header.TFrame")
        header.pack(fill="x")

        row1 = ttk.Frame(header, style="Header.TFrame", padding=(18, 14, 18, 6))
        row1.pack(fill="x")

        title_box = ttk.Frame(row1, style="Header.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="Consola Wialon", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_box, text="Diagnostico y monitoreo de flota en tiempo real",
            style="HeaderMuted.TLabel",
        ).pack(anchor="w")

        status_box = ttk.Frame(row1, style="Header.TFrame")
        status_box.pack(side="right")
        top_line = ttk.Frame(status_box, style="Header.TFrame")
        top_line.pack(anchor="e")
        self.status_dot = tk.Canvas(top_line, width=12, height=12, highlightthickness=0, bg=HEADER_BG)
        self.status_dot.pack(side="left", padx=(0, 6))
        self._dot = self.status_dot.create_oval(1, 1, 11, 11, fill="#5b6478", outline="")
        self.status_var = tk.StringVar(value="Desconectado")
        ttk.Label(top_line, textvariable=self.status_var, style="Header.TLabel", font=(FONT, 10, "bold")).pack(side="left")

        self.token_expiry_var = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self.token_expiry_var, style="HeaderMuted.TLabel").pack(anchor="e")

        row2 = ttk.Frame(header, style="Header.TFrame", padding=(18, 4, 18, 16))
        row2.pack(fill="x")

        ttk.Label(row2, text="HOST", style="HeaderMuted.TLabel").pack(side="left")
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        ttk.Entry(row2, textvariable=self.host_var, width=26).pack(side="left", padx=(6, 18))

        ttk.Label(row2, text="API KEY / TOKEN", style="HeaderMuted.TLabel").pack(side="left")
        self.token_var = tk.StringVar()
        self.token_var.trace_add("write", lambda *_: self._update_token_len_hint())
        self.token_entry = ttk.Entry(row2, textvariable=self.token_var, width=40, show="•")
        self.token_entry.pack(side="left", padx=(6, 6))

        self.token_len_lbl = tk.Label(
            row2, text=f"0/{EXPECTED_TOKEN_LEN}", bg=HEADER_BG, fg=TEXT_ON_DARK_MUTED,
            font=(FONT, 9), padx=0,
        )
        self.token_len_lbl.pack(side="left", padx=(0, 10))

        self.show_token_var = tk.BooleanVar(value=False)
        self.eye_btn = ttk.Button(
            row2, text="Mostrar", width=8, style="Ghost.TButton", command=self._toggle_token_visible,
        )
        self.eye_btn.pack(side="left", padx=(0, 14))

        self.connect_btn = ttk.Button(
            row2, text="Conectar", style="Accent.TButton", command=self.toggle_connect,
        )
        self.connect_btn.pack(side="left")

        ttk.Label(
            row2, text="(Genera o revisa la vigencia de tu token en la pestana \"Token\")",
            style="HeaderMuted.TLabel",
        ).pack(side="left", padx=(14, 0))

    def _toggle_token_visible(self):
        show = not self.show_token_var.get()
        self.show_token_var.set(show)
        self.token_entry.config(show="" if show else "•")
        self.eye_btn.config(text="Ocultar" if show else "Mostrar")

    def _update_token_len_hint(self):
        n = len(self.token_var.get().strip())
        self.token_len_lbl.config(
            text=f"{n}/{EXPECTED_TOKEN_LEN}",
            fg="#8fd6a5" if n == EXPECTED_TOKEN_LEN else TEXT_ON_DARK_MUTED,
        )

    def _build_toolbar(self):
        wrap = ttk.Frame(self.root, style="TFrame", padding=(10, 8, 10, 0))
        wrap.pack(fill="x")

        bar = ttk.Frame(wrap, style="Toolbar.TFrame", padding=(12, 10))
        bar.pack(fill="x")

        ttk.Label(bar, text="Buscar (nombre / UID)", style="ToolbarMuted.TLabel").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(bar, textvariable=self.search_var, width=26).pack(side="left", padx=(6, 20))

        ttk.Label(bar, text="Actualizar cada (s)", style="ToolbarMuted.TLabel").pack(side="left")
        self.interval_var = tk.IntVar(value=15)
        ttk.Spinbox(bar, from_=5, to=300, textvariable=self.interval_var, width=5).pack(
            side="left", padx=(6, 20)
        )

        ttk.Label(bar, text="Sin conexion tras (h)", style="ToolbarMuted.TLabel").pack(side="left")
        self.noconn_hours_var = tk.IntVar(value=24)
        ttk.Spinbox(bar, from_=1, to=720, textvariable=self.noconn_hours_var, width=5).pack(
            side="left", padx=(6, 20)
        )

        self.count_var = tk.StringVar(value="0 unidades")
        ttk.Label(bar, textvariable=self.count_var, style="Toolbar.TLabel", font=(FONT, 9, "bold")).pack(side="right")

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 10))

        self._build_list_tab()
        self._build_kpi_tab()
        self._build_token_tab()
        self._build_log_tab()

    def _build_list_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Listado  ")

        # Columnas curadas para que quepan sin scroll lateral en una ventana
        # normal. Lat/Lon/Rumbo/Sat/Trafico/ID siguen disponibles con doble
        # clic (JSON completo), ya que no son datos de un vistazo rapido.
        self.list_cols = ("nm", "uid", "hw", "ph", "last", "speed", "mileage", "eh", "status")
        headers = {
            "nm": "Nombre", "uid": "UID", "hw": "Modelo / HW", "ph": "Telefono",
            "last": "Ult. mensaje", "speed": "Vel. (km/h)",
            "mileage": "Km", "eh": "Horas motor", "status": "Estado",
        }
        widths = {
            "nm": 160, "uid": 150, "hw": 150, "ph": 110, "last": 150,
            "speed": 90, "mileage": 90, "eh": 100, "status": 130,
        }
        self.list_tree = self._make_tree(frame, self.list_cols, headers, widths)
        self.list_tree.bind("<Double-1>", self._show_unit_detail)

        ttk.Label(
            frame, text="Doble clic en una unidad para ver TODOS sus campos (JSON crudo,"
            " incluye lat/lon, rumbo, satelites y trafico).",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=6, pady=(6, 2))

    CARD_WIDTH = 250
    CARD_HEIGHT = 216
    CARD_GAP = 14

    def _build_kpi_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  KPI  ")

        legend = ttk.Frame(frame, padding=(6, 8))
        legend.pack(fill="x")
        for tag, label in (
            ("verde", "En movimiento"),
            ("amarillo", "Ralenti (motor encendido, detenida)"),
            ("rojo", "Detenida"),
            ("gris", "Sin conexion / sin datos"),
        ):
            tk.Label(
                legend, image=self.status_icons[(tag, "app")], text=f" {label}", compound="left",
                bg=BG_APP, fg=TEXT_MUTED, font=(FONT, 9),
            ).pack(side="left", padx=(0, 18))

        board = ttk.Frame(frame)
        board.pack(fill="both", expand=True)

        self.kpi_canvas = tk.Canvas(board, bg=BG_APP, highlightthickness=0)
        kpi_vsb = AutoScrollbar(board, orient="vertical", command=self.kpi_canvas.yview)
        self.kpi_canvas.configure(yscrollcommand=kpi_vsb.set)
        self.kpi_canvas.grid(row=0, column=0, sticky="nsew")
        kpi_vsb.grid(row=0, column=1, sticky="ns")
        board.rowconfigure(0, weight=1)
        board.columnconfigure(0, weight=1)

        self.kpi_inner = tk.Frame(self.kpi_canvas, bg=BG_APP)
        self._kpi_window = self.kpi_canvas.create_window((0, 0), window=self.kpi_inner, anchor="nw")

        self.kpi_inner.bind(
            "<Configure>",
            lambda e: self.kpi_canvas.configure(scrollregion=self.kpi_canvas.bbox("all")),
        )
        self.kpi_canvas.bind("<Configure>", self._reflow_kpi_cards)
        self.kpi_canvas.bind_all("<MouseWheel>", self._on_kpi_mousewheel, add="+")

        self._kpi_cards = []
        self._kpi_cols_configured = 0
        self.kpi_empty_lbl = tk.Label(
            self.kpi_inner, text="Sin unidades para mostrar.", bg=BG_APP, fg=TEXT_MUTED, font=(FONT, 10),
        )

    def _on_kpi_mousewheel(self, event):
        # Solo desplazar si el mouse esta sobre el tab KPI (evita robar scroll a otros tabs)
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        w = widget
        while w is not None:
            if w is self.kpi_canvas:
                self.kpi_canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            w = w.master

    def _make_tree(self, parent, cols, headers, widths=None):
        widths = widths or {}
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)

        tree = ttk.Treeview(container, columns=cols, show="tree headings", selectmode="browse")
        tree.heading("#0", text="")
        tree.column("#0", width=30, minwidth=30, stretch=False, anchor="center")
        for c in cols:
            tree.heading(c, text=headers[c], command=lambda c=c, t=tree: self._sort_tree(t, c))
            w = widths.get(c, 110)
            tree.column(c, width=w, minwidth=max(50, w - 30), anchor="w", stretch=True)

        tree.tag_configure("odd", background=ROW_ALT)
        tree.tag_configure("even", background=SURFACE)

        vsb = AutoScrollbar(container, orient="vertical", command=tree.yview)
        hsb = AutoScrollbar(container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        return tree

    def _clear_frame(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    # ---------- tab Token: vigencia del actual + generador ----------

    def _build_token_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Token  ")

        self.token_canvas = tk.Canvas(frame, bg=BG_APP, highlightthickness=0)
        vsb = AutoScrollbar(frame, orient="vertical", command=self.token_canvas.yview)
        self.token_canvas.configure(yscrollcommand=vsb.set)
        self.token_canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        inner = tk.Frame(self.token_canvas, bg=BG_APP)
        win = self.token_canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda e: self.token_canvas.configure(scrollregion=self.token_canvas.bbox("all")),
        )
        self.token_canvas.bind(
            "<Configure>", lambda e: self.token_canvas.itemconfigure(win, width=e.width)
        )
        self.token_canvas.bind_all("<MouseWheel>", self._on_token_mousewheel, add="+")

        tk.Label(
            inner, text="Vigencia del token conectado", bg=BG_APP, fg=TEXT_PRIMARY,
            font=(FONT, 12, "bold"),
        ).pack(anchor="w", padx=16, pady=(18, 4))
        self.token_status_frame = tk.Frame(
            inner, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1,
        )
        self.token_status_frame.pack(fill="x", padx=16, pady=(0, 8))
        self._render_token_status(None)

        self._build_token_generator_section(inner)

    def _on_token_mousewheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        w = widget
        while w is not None:
            if w is self.token_canvas:
                self.token_canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            w = w.master

    def _render_token_status(self, summary):
        self._clear_frame(self.token_status_frame)

        if not summary:
            tk.Label(
                self.token_status_frame, bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 10),
                justify="left", wraplength=620,
                text="Conectate para ver aqui la vigencia y el alcance real de tu token.",
                padx=14, pady=14,
            ).pack(anchor="w")
            return

        body = tk.Frame(self.token_status_frame, bg=SURFACE, padx=16, pady=14)
        body.pack(fill="x")

        def row(label, value):
            r = tk.Frame(body, bg=SURFACE)
            r.pack(fill="x", pady=4)
            tk.Label(
                r, text=label, bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 9, "bold"),
                width=14, anchor="w",
            ).pack(side="left")
            tk.Label(
                r, text=value, bg=SURFACE, fg=TEXT_PRIMARY, font=(FONT, 10), anchor="w",
                justify="left", wraplength=460,
            ).pack(side="left", fill="x")

        row("Aplicacion", summary["app"])
        row("Creado", summary["created_str"])
        row("Activacion", summary["activation_str"])
        row("Duracion", summary["duration_str"])
        row("Vence", summary["expiry_str"])
        row("Ultimo uso", summary["last_used_str"])
        row("Unidades", summary["restriction_str"])

        tk.Label(
            body, text="Alcance de permisos", bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 9, "bold"),
        ).pack(anchor="w", pady=(10, 2))
        for line in summary["scope_lines"]:
            tk.Label(body, text=f"- {line}", bg=SURFACE, fg=TEXT_PRIMARY, font=(FONT, 10), anchor="w").pack(anchor="w")

        tk.Label(
            body, text="Detalle crudo (token/list)", bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 9, "bold"),
        ).pack(anchor="w", pady=(12, 2))
        text = tk.Text(body, height=7, wrap="word", font=(FONT_MONO, 9), bg=SURFACE, relief="flat")
        text.pack(fill="x")
        text.insert("1.0", json.dumps(summary["raw"], indent=2, ensure_ascii=False, sort_keys=True))
        text.configure(state="disabled")

    def _build_token_generator_section(self, parent):
        tk.Label(
            parent, text="Generar un token nuevo", bg=BG_APP, fg=TEXT_PRIMARY,
            font=(FONT, 12, "bold"),
        ).pack(anchor="w", padx=16, pady=(16, 4))

        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=16, pady=(0, 20))
        body = tk.Frame(card, bg=SURFACE, padx=16, pady=14)
        body.pack(fill="x")

        tk.Label(
            body, bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 9), justify="left", wraplength=640,
            text=(
                "Wialon no permite crear un token por API sin que el usuario inicie sesion el "
                "mismo (la contrasena nunca pasa por esta app). Configura lo que necesitas: el "
                "boton de abajo abre el portal de acceso en tu navegador para iniciar sesion ahi."
            ),
        ).pack(anchor="w", pady=(0, 12))

        def field(label_text):
            r = tk.Frame(body, bg=SURFACE)
            r.pack(fill="x", pady=4)
            tk.Label(
                r, text=label_text, bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 9, "bold"),
                width=16, anchor="w",
            ).pack(side="left")
            return r

        r = field("Portal de acceso")
        self.tokgen_portal_var = tk.StringVar(value=DEFAULT_TOKEN_PORTAL)
        ttk.Entry(r, textvariable=self.tokgen_portal_var).pack(side="left", fill="x", expand=True)

        r = field("Nombre de la app")
        self.tokgen_client_var = tk.StringVar(value=APP_TITLE.replace(" ", ""))
        ttk.Entry(r, textvariable=self.tokgen_client_var).pack(side="left", fill="x", expand=True)

        r = field("Usuario (opcional)")
        self.tokgen_user_var = tk.StringVar(value="")
        ttk.Entry(r, textvariable=self.tokgen_user_var).pack(side="left", fill="x", expand=True)

        r = field("Vigencia")
        self.tokgen_duration_var = tk.StringVar(value=DURATION_PRESETS[2][0])
        ttk.Combobox(
            r, textvariable=self.tokgen_duration_var, state="readonly",
            values=[label for label, _ in DURATION_PRESETS],
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            body, text="*Wialon elimina cualquier token si pasa 100 dias sin usarse.",
            bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 8),
        ).pack(anchor="w", padx=(120, 0))

        tk.Label(
            body, text="Permisos del token", bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 9, "bold"),
        ).pack(anchor="w", pady=(14, 4))

        self.tokgen_full_access_var = tk.BooleanVar(value=True)
        self.tokgen_flag_vars = {bit: tk.BooleanVar(value=False) for bit, _ in TOKEN_ACCESS_FLAGS}
        self._tokgen_flag_checks = []

        ttk.Checkbutton(
            body, text="Acceso completo (recomendado para esta consola de diagnostico)",
            variable=self.tokgen_full_access_var, command=self._tokgen_toggle_full_access,
        ).pack(anchor="w")

        perm_box = tk.Frame(body, bg=SURFACE)
        perm_box.pack(anchor="w", fill="x", padx=(20, 0), pady=(2, 0))
        for bit, label in TOKEN_ACCESS_FLAGS:
            cb = ttk.Checkbutton(perm_box, text=label, variable=self.tokgen_flag_vars[bit], state="disabled")
            cb.pack(anchor="w")
            self._tokgen_flag_checks.append(cb)

        self.tokgen_url_var = tk.StringVar(value="")
        btn_row = tk.Frame(body, bg=SURFACE)
        btn_row.pack(fill="x", pady=(14, 4))
        ttk.Button(
            btn_row, text="Abrir portal para generar el token", style="Accent.TButton",
            command=self._tokgen_open,
        ).pack(side="left")
        ttk.Button(btn_row, text="Copiar enlace", command=self._tokgen_copy).pack(side="left", padx=(8, 0))

        ttk.Entry(body, textvariable=self.tokgen_url_var, state="readonly").pack(fill="x", pady=(4, 14))
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(0, 12))

        tk.Label(
            body, bg=SURFACE, fg=TEXT_PRIMARY, font=(FONT, 9), justify="left", wraplength=640,
            text=(
                "1) Inicia sesion en la pagina que se abrio.\n"
                "2) Al terminar volveras a login.html con access_token=... en la barra de "
                "direcciones del navegador: copia esa URL completa (o solo el token).\n"
                "3) Pegala abajo y presiona \"Usar este token\"."
            ),
        ).pack(anchor="w", pady=(0, 8))

        self.tokgen_paste_var = tk.StringVar(value="")
        paste_row = tk.Frame(body, bg=SURFACE)
        paste_row.pack(fill="x")
        ttk.Entry(paste_row, textvariable=self.tokgen_paste_var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            paste_row, text="Usar este token", style="Accent.TButton", command=self._tokgen_use,
        ).pack(side="left", padx=(8, 0))

    def _tokgen_toggle_full_access(self):
        state = "disabled" if self.tokgen_full_access_var.get() else "normal"
        for cb in self._tokgen_flag_checks:
            cb.config(state=state)

    def _tokgen_build_url(self):
        duration = next(s for label, s in DURATION_PRESETS if label == self.tokgen_duration_var.get())
        if self.tokgen_full_access_var.get():
            access_type = TOKEN_FULL_ACCESS
        else:
            access_type = 0
            for bit, _ in TOKEN_ACCESS_FLAGS:
                if self.tokgen_flag_vars[bit].get():
                    access_type |= bit
            if access_type == 0:
                access_type = 0x100  # valor por defecto documentado si no se marca nada
        portal = (self.tokgen_portal_var.get().strip() or DEFAULT_TOKEN_PORTAL).rstrip("/")
        if not portal.startswith("http"):
            portal = "https://" + portal
        params = {
            "client_id": self.tokgen_client_var.get().strip() or "ConsolaWialon",
            "access_type": access_type,
            "activation_time": 0,
            "duration": duration,
            "lang": "es",
            "flags": "0x1",
        }
        if self.tokgen_user_var.get().strip():
            params["user"] = self.tokgen_user_var.get().strip()
        query = "&".join(f"{k}={url_quote(str(v), safe='')}" for k, v in params.items())
        return f"{portal}/login.html?{query}"

    def _tokgen_open(self):
        url = self._tokgen_build_url()
        self.tokgen_url_var.set(url)
        webbrowser.open(url)

    def _tokgen_copy(self):
        url = self.tokgen_url_var.get() or self._tokgen_build_url()
        self.tokgen_url_var.set(url)
        self.root.clipboard_clear()
        self.root.clipboard_append(url)

    def _tokgen_use(self):
        extracted = extract_token_from_text(self.tokgen_paste_var.get())
        if not extracted:
            messagebox.showwarning(APP_TITLE, "Pega el token o la URL con access_token=... primero.")
            return
        self.token_var.set(extracted)
        messagebox.showinfo(
            APP_TITLE, "Token colocado en el campo de conexion (arriba). Presiona \"Conectar\" cuando quieras.",
        )

    def _build_log_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  LOG  ")

        bar = ttk.Frame(frame, padding=(4, 6))
        bar.pack(fill="x")
        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto-scroll", variable=self.autoscroll_var).pack(side="left")
        ttk.Button(bar, text="Limpiar log", command=self._clear_log).pack(side="left", padx=8)
        ttk.Label(
            bar, text="Solo se muestra en pantalla (no se guarda en disco).",
            style="Muted.TLabel",
        ).pack(side="left", padx=8)

        self.log_text = scrolledtext.ScrolledText(
            frame, wrap="word", state="disabled", font=(FONT_MONO, 9),
            bg=SURFACE, fg=TEXT_PRIMARY, borderwidth=0, relief="flat",
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.log_text.tag_configure("REQ", foreground=ACCENT_DARK)
        self.log_text.tag_configure("RESP", foreground=SUCCESS)
        self.log_text.tag_configure("ERR", foreground=DANGER)

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=HEAD_ROW_BG)
        bar.pack(fill="x", side="bottom")
        self.footer_var = tk.StringVar(value="Listo.")
        tk.Label(
            bar, textvariable=self.footer_var, anchor="w", bg=HEAD_ROW_BG, fg=TEXT_MUTED,
            font=(FONT, 9), padx=10, pady=4,
        ).pack(fill="x")

    # ---------- logging ----------

    def _client_log(self, kind, text):
        self.result_queue.put(("log", kind, text))

    def _append_log(self, kind, text):
        ts = datetime.now().strftime("%H:%M:%S")
        arrow = {"REQ": ">>", "RESP": "<<", "ERR": "!!"}.get(kind, "--")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {arrow} {kind} {text}\n", kind)
        self.log_text.configure(state="disabled")
        if self.autoscroll_var.get():
            self.log_text.see("end")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ---------- conexion ----------

    def toggle_connect(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        token = self.token_var.get().strip()
        host = self.host_var.get().strip() or DEFAULT_HOST
        if not token:
            messagebox.showwarning(APP_TITLE, "Ingresa tu API Key / Token de Wialon.")
            return

        self.connect_btn.config(state="disabled")
        self.status_var.set("Conectando...")
        client = WialonClient(host, log_fn=self._client_log)

        def work():
            try:
                data = client.login(token)
            except WialonError as e:
                self.result_queue.put(("login_err", str(e)))
                return
            except requests.RequestException as e:
                self.result_queue.put(("login_err", f"Error de red: {e}"))
                return

            # token/list es la fuente autoritativa (mas confiable que el
            # campo 'tk' embebido en el login, ver comentario en list_tokens).
            summary = None
            try:
                for entry in client.list_tokens():
                    if entry.get("h") == token:
                        summary = build_token_summary(entry)
                        break
            except Exception:
                pass
            if summary is None and client.token_info:
                summary = build_token_summary(client.token_info)

            self.result_queue.put(("login_ok", client, data, summary))

        threading.Thread(target=work, daemon=True).start()

    def _disconnect(self, silent=False):
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        client = self.client
        self.connected = False
        self.client = None

        if client:
            def work():
                try:
                    client.logout()
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        self.connect_btn.config(text="Conectar", style="Accent.TButton", state="normal")
        self._render_token_status(None)
        self.token_expiry_var.set("")
        self.status_var.set("Desconectado")
        self.status_dot.itemconfig(self._dot, fill="#5b6478")

        # No conservar datos de unidades tras desconectar.
        self.units_by_id = {}
        self._refresh_list_tree([])
        self._refresh_kpi_cards([])
        self.count_var.set("0 unidades")
        if not silent:
            self.footer_var.set("Desconectado.")

    def _excepthook(self, exc_type, exc, tb):
        messagebox.showerror(APP_TITLE, f"Error inesperado: {exc}")

    def on_close(self):
        if self.connected and self.client:
            try:
                self.client.logout()
            except Exception:
                pass
        self.root.destroy()

    # ---------- cola de resultados (hilo -> UI) ----------

    def _poll_queue(self):
        try:
            while True:
                item = self.result_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2])
                elif kind == "login_ok":
                    self._on_login_ok(item[1], item[2], item[3])
                elif kind == "login_err":
                    self._on_login_err(item[1])
                elif kind == "hwtypes":
                    self.hw_types = item[1]
                elif kind == "units":
                    self._on_units(item[1])
                elif kind == "units_err":
                    self.footer_var.set(f"Error al actualizar unidades: {item[1]}")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _on_login_ok(self, client, data, token_summary):
        self.client = client
        self.connected = True
        self.connect_btn.config(text="Desconectar", style="Danger.TButton", state="normal")
        who = client.username or "usuario"
        self.status_var.set(f"Conectado como: {who}")
        self.status_dot.itemconfig(self._dot, fill=SUCCESS)
        self.footer_var.set("Conectado. Obteniendo unidades...")

        self._render_token_status(token_summary)
        if token_summary:
            if token_summary["remaining_s"] is not None:
                self.token_expiry_var.set(f"Token vence en {human_duration(token_summary['remaining_s'])}")
            else:
                self.token_expiry_var.set("Token sin vencimiento fijo")
        else:
            self.token_expiry_var.set("No se pudo obtener el detalle del token (revisa el LOG)")

        def work():
            try:
                hw = client.get_hw_types()
                self.result_queue.put(("hwtypes", hw))
            except Exception:
                pass  # no es critico para el resto de la app

        threading.Thread(target=work, daemon=True).start()

        self._schedule_refresh(immediate=True)

    def _on_login_err(self, msg):
        self.connect_btn.config(state="normal")
        self.status_var.set("Desconectado")
        messagebox.showerror(APP_TITLE, f"No se pudo conectar:\n{msg}")

    # ---------- refresco de unidades ----------

    def _schedule_refresh(self, immediate=False):
        if not self.connected:
            return
        if immediate:
            self._do_refresh()
        interval_ms = max(5, self.interval_var.get()) * 1000
        self.refresh_job = self.root.after(interval_ms, self._schedule_refresh)

    def _do_refresh(self):
        if self.refresh_in_progress or not self.client:
            return
        self.refresh_in_progress = True
        client = self.client
        no_conn_s = max(1, self.noconn_hours_var.get()) * 3600

        def work():
            try:
                items = client.search_units()
                now_ts = time.time()
                for unit in items:
                    self._resolve_ignition(client, unit, now_ts, no_conn_s)
                self.result_queue.put(("units", items))
            except WialonError as e:
                self.result_queue.put(("units_err", str(e)))
            except requests.RequestException as e:
                self.result_queue.put(("units_err", f"Error de red: {e}"))
            finally:
                self.refresh_in_progress = False

        threading.Thread(target=work, daemon=True).start()

    def _resolve_ignition(self, client, unit, now_ts, no_conn_s):
        """Determina, cuando es posible, si el motor esta encendido usando el
        sensor de tipo 'engine operation' (ignicion) via unit/calc_last_message
        -- solo para unidades detenidas y con conexion reciente, para minimizar
        llamadas extra al API."""
        unit["_ignition"] = None
        lmsg = unit.get("lmsg") or {}
        t = lmsg.get("t", 0)
        if not t or (now_ts - t) > no_conn_s:
            return
        pos = lmsg.get("pos") or {}
        speed = pos.get("s", 0) or 0
        if speed > self.MOVING_SPEED_KMH:
            return
        sensor = find_ignition_sensor(unit)
        if not sensor:
            return
        try:
            result = client.calc_last_message(unit["id"], [sensor["id"]])
        except Exception:
            return
        val = result.get(str(sensor["id"]))
        if val is None or val == SENSOR_NA_VALUE:
            return
        unit["_ignition"] = bool(val)

    def _on_units(self, items):
        self.units_by_id = {u["id"]: u for u in items}
        self.count_var.set(f"{len(items)} unidades")
        self.footer_var.set(f"Ultima actualizacion: {datetime.now().strftime('%H:%M:%S')}")
        self._apply_filter()

    # ---------- render de listas ----------

    def _apply_filter(self):
        term = self.search_var.get().strip().lower()
        items = list(self.units_by_id.values())
        if term:
            items = [
                u for u in items
                if term in str(u.get("nm", "")).lower() or term in str(u.get("uid", "")).lower()
            ]
        self._refresh_list_tree(items)
        self._refresh_kpi_cards(items)

    def _classify(self, unit, now_ts, no_conn_s):
        lmsg = unit.get("lmsg") or {}
        t = lmsg.get("t", 0)
        if not t:
            return "gris", "Sin datos", None
        age = now_ts - t
        if age > no_conn_s:
            return "gris", "Sin conexion", age
        pos = lmsg.get("pos") or {}
        speed = pos.get("s", 0) or 0
        if speed > self.MOVING_SPEED_KMH:
            return "verde", "En movimiento", age
        ign = unit.get("_ignition")
        if ign is True:
            return "amarillo", "Ralenti", age
        return "rojo", "Detenida", age

    def _refresh_list_tree(self, items):
        tree = self.list_tree
        tree.delete(*tree.get_children())
        now_ts = time.time()
        no_conn_s = max(1, self.noconn_hours_var.get()) * 3600
        for idx, u in enumerate(items):
            lmsg = u.get("lmsg") or {}
            pos = lmsg.get("pos") or {}
            t = lmsg.get("t")
            last_str = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else "-"
            tag, status_label, _ = self._classify(u, now_ts, no_conn_s)
            hw_name = self.hw_types.get(u.get("hw"), u.get("hw", "-"))
            row = (
                u.get("nm", "-"),
                u.get("uid", "-"),
                hw_name,
                u.get("ph", "-") or "-",
                last_str,
                pos.get("s", "-"),
                u.get("cnm", "-"),
                u.get("cneh", "-"),
                status_label,
            )
            zebra = "odd" if idx % 2 else "even"
            icon = self.status_icons[(tag, zebra)]
            tree.insert("", "end", iid=str(u["id"]), text="", image=icon, values=row, tags=(zebra,))

    STATUS_ORDER = {"verde": 0, "amarillo": 1, "rojo": 2, "gris": 3}

    def _refresh_kpi_cards(self, items):
        for card in self._kpi_cards:
            card.destroy()
        self._kpi_cards = []

        now_ts = time.time()
        no_conn_s = max(1, self.noconn_hours_var.get()) * 3600
        classified = [(u, *self._classify(u, now_ts, no_conn_s)) for u in items]
        classified.sort(key=lambda row: (self.STATUS_ORDER[row[1]], str(row[0].get("nm", ""))))

        if not classified:
            self.kpi_empty_lbl.grid(row=0, column=0, padx=4, pady=20, sticky="w")
        else:
            self.kpi_empty_lbl.grid_forget()
            for u, tag, status_label, age in classified:
                pos = (u.get("lmsg") or {}).get("pos") or {}
                card = self._make_kpi_card(
                    self.kpi_inner, u, tag, status_label,
                    speed=pos.get("s"), mileage=u.get("cnm"), eh=u.get("cneh"),
                    age_str=human_age(age) if age is not None else "-",
                )
                self._kpi_cards.append(card)

        self._reflow_kpi_cards()

    def _make_kpi_card(self, parent, unit, tag, status_label, speed, mileage, eh, age_str):
        color = STATUS_COLORS[tag]
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.configure(width=self.CARD_WIDTH, height=self.CARD_HEIGHT)
        card.pack_propagate(False)

        strip = tk.Frame(card, bg=color, height=4)
        strip.pack(fill="x", side="top")

        body = tk.Frame(card, bg=SURFACE, padx=14, pady=10)
        body.pack(fill="both", expand=True)

        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, image=self.status_icons[(tag, "even")], bg=SURFACE).pack(side="left")
        tk.Label(
            head, text=status_label, bg=SURFACE, fg=color, font=(FONT, 9, "bold"),
        ).pack(side="left", padx=(6, 0))

        name = unit.get("nm", "-")
        if len(name) > 26:
            name = name[:25] + "…"
        tk.Label(
            body, text=name, bg=SURFACE, fg=TEXT_PRIMARY, font=(FONT, 11, "bold"), anchor="w",
        ).pack(fill="x", pady=(8, 0))
        tk.Label(
            body, text=f"UID: {unit.get('uid', '-')}", bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 8),
            anchor="w",
        ).pack(fill="x")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(8, 8))

        stats = tk.Frame(body, bg=SURFACE)
        stats.pack(fill="both", expand=True)
        stats.columnconfigure(0, weight=1)
        stats.columnconfigure(1, weight=1)

        def stat(r, c, label, value):
            box = tk.Frame(stats, bg=SURFACE)
            box.grid(row=r, column=c, sticky="w", pady=(0, 6))
            tk.Label(box, text=label, bg=SURFACE, fg=TEXT_MUTED, font=(FONT, 8)).pack(anchor="w")
            tk.Label(box, text=value, bg=SURFACE, fg=TEXT_PRIMARY, font=(FONT, 10, "bold")).pack(anchor="w")

        stat(0, 0, "VELOCIDAD", f"{speed} km/h" if speed is not None else "-")
        stat(0, 1, "KILOMETRAJE", f"{mileage} km" if mileage is not None else "-")
        stat(1, 0, "HORAS MOTOR", f"{eh} h" if eh is not None else "-")
        stat(1, 1, "ULT. CONEXION", age_str)

        unit_id = unit["id"]

        def open_detail(_e=None, uid=unit_id):
            self._show_unit_detail_by_id(uid)

        for w in (card, strip, body, head, stats):
            w.bind("<Button-1>", open_detail)
        for child in body.winfo_children():
            child.bind("<Button-1>", open_detail)

        return card

    def _reflow_kpi_cards(self, _event=None):
        canvas_width = self.kpi_canvas.winfo_width()
        if canvas_width <= 1 or not self._kpi_cards:
            return

        # El frame interno debe medir lo mismo que el canvas para que las
        # columnas con weight=1 puedan repartirse el sobrante horizontal en
        # vez de dejarlo como una franja vacia pegada al borde derecho.
        self.kpi_canvas.itemconfigure(self._kpi_window, width=canvas_width)

        col_w = self.CARD_WIDTH + self.CARD_GAP
        cols = max(1, canvas_width // col_w)
        for i in range(max(cols, self._kpi_cols_configured)):
            self.kpi_inner.columnconfigure(i, weight=(1 if i < cols else 0))
        self._kpi_cols_configured = cols

        half_gap = self.CARD_GAP // 2
        for i, card in enumerate(self._kpi_cards):
            r, c = divmod(i, cols)
            card.grid(row=r, column=c, padx=half_gap, pady=half_gap, sticky="nw")
        self.kpi_canvas.configure(scrollregion=self.kpi_canvas.bbox("all"))

    def _sort_tree(self, tree, col):
        data = [(tree.set(k, col), k) for k in tree.get_children("")]

        def key(pair):
            val = pair[0]
            try:
                return (0, float(val))
            except (TypeError, ValueError):
                return (1, str(val).lower())

        reverse = getattr(tree, "_sort_reverse", {}).get(col, False)
        data.sort(key=key, reverse=reverse)
        for index, (_, k) in enumerate(data):
            tree.move(k, "", index)
        state = getattr(tree, "_sort_reverse", {})
        state[col] = not reverse
        tree._sort_reverse = state

    def _show_unit_detail(self, _event):
        sel = self.list_tree.selection()
        if not sel:
            return
        self._show_unit_detail_by_id(int(sel[0]))

    def _show_unit_detail_by_id(self, unit_id):
        unit = self.units_by_id.get(unit_id)
        if not unit:
            return

        top = tk.Toplevel(self.root)
        top.title(f"Unidad: {unit.get('nm', unit.get('id'))}")
        top.geometry("700x600")
        text = scrolledtext.ScrolledText(top, wrap="word", font=(FONT_MONO, 9))
        text.pack(fill="both", expand=True)
        text.insert("1.0", json.dumps(unit, indent=2, ensure_ascii=False, sort_keys=True))
        text.configure(state="disabled")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
