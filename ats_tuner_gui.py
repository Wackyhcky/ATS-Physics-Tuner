"""ATS Physics Tuner - truck & car physics for American Truck Simulator.

Same Windows 11 (Fluent, dark) look as the GTA IV Exporter: sidebar navigation,
rounded cards, sv_ttk widgets. Pages:
  Trucks / Cars  -> per-vehicle engine, gearbox and chassis tuning (separate files)
  Shared         -> def/vehicle/physics.sii (applies to both)
  Steering       -> steering wheel rotation, auto-switch, MOZA wheelbase
  Mod info       -> name, author, version, description, icon
  Build          -> write the mod, presets, log
  Settings       -> game and mod folders
"""
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import tuner_core as core  # noqa: E402
import moza  # noqa: E402
from autoswitch import AutoSwitcher  # noqa: E402
from fluent import (ACCENT, BAD, BG, CARD, CONTENT, FG, FG2, FG3, ICONS, LINE, OK, WARN, Card, Fonts,  # noqa: E402
                    Link, NavItem, Scroll, Tile, apply_theme, dark_title_bar, elide)

APP_NAME = "ATS Physics Tuner"
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
SETTINGS_DIR = os.path.join(_APPDATA, "ATSPhysicsTuner")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
OLD_SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuner_settings.json")
PART_TITLES = {"engine": ("Engine", "Power, engine brake and how fast it revs."),
               "transmission": ("Transmission", "Final drive and shift speed."),
               "chassis": ("Chassis & handling", "Weight, steering lock, suspension and roll.")}


def load_settings():
    for fn in (SETTINGS_FILE, OLD_SETTINGS):   # old builds kept settings next to the script
        try:
            with open(fn, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def save_settings(data):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def model_title(model):
    """kenworth.w900 -> Kenworth W900, intnational.lt -> International LT, ford.f150_23 -> Ford F150 23"""
    brand, _, name = model.partition(".")
    brand = {"intnational": "International", "westernstar": "Western Star"}.get(brand, brand.title())
    name = name.replace("_", " ")
    name = " ".join(w.upper() if len(w) <= 3 or any(c.isdigit() for c in w) else w.title() for w in name.split())
    return f"{brand} {name}".strip()


# ===================================================================== widgets
class ParamRow:
    """Switch + slider + value box + reset, on a card."""

    def __init__(self, app, parent, row, cls, p):
        self.key = p[0]
        kind, lo, hi, default = core.param_spec(cls, p)
        self.default, self.lo, self.hi = default, lo, hi
        self.on = tk.BooleanVar(value=False)
        self.val = tk.DoubleVar(value=default)
        self.entry_var = tk.StringVar(value=core.fmt(default))
        ttk.Checkbutton(parent, text=p[2], variable=self.on, command=self._sync,
                        style="Card.Switch.TCheckbutton").grid(row=row, column=0, sticky="w", pady=5)
        app._label(parent, "× stock" if kind == "mult" else "", font=app.f.small, fg=FG3).grid(
            row=row, column=1, sticky="w", padx=(6, 12))
        res = (hi - lo) / 200
        self.scale = ttk.Scale(parent, from_=lo, to=hi, variable=self.val, style="Card.Horizontal.TScale",
                               command=lambda _=None: self.entry_var.set(core.fmt(round(self.val.get() / res) * res)))
        self.scale.grid(row=row, column=2, sticky="ew", pady=5)
        self.entry = ttk.Entry(parent, textvariable=self.entry_var, width=8, style="Card.TEntry")
        self.entry.grid(row=row, column=3, padx=(12, 8))
        self.entry.bind("<Return>", self._from_entry)
        self.entry.bind("<FocusOut>", self._from_entry)
        rst = tk.Label(parent, text=ICONS["refresh"], font=app.f.icon_small, fg=FG3, bg=CARD, cursor="hand2")
        rst.grid(row=row, column=4)
        rst.bind("<Button-1>", lambda e: self.reset())
        rst.bind("<Enter>", lambda e: rst.configure(fg=ACCENT))
        rst.bind("<Leave>", lambda e: rst.configure(fg=FG3))
        self._sync()

    def _clamp(self, v):
        return min(max(float(v), self.lo), self.hi)

    def _from_entry(self, _=None):
        try:
            v = self._clamp(self.entry_var.get())
        except ValueError:
            v = self.val.get()
        self.val.set(v)
        self.entry_var.set(core.fmt(v))

    def _sync(self):
        on = self.on.get()
        self.scale.state(["!disabled"] if on else ["disabled"])
        self.entry.state(["!disabled"] if on else ["disabled"])

    def reset(self):
        self.val.set(self.default)
        self.entry_var.set(core.fmt(self.default))
        self.on.set(False)
        self._sync()

    def get(self):
        self._from_entry()
        return {"on": self.on.get(), "value": float(self.entry_var.get())}

    def set(self, d):
        self.on.set(d.get("on", False))
        v = self._clamp(d.get("value", self.default))
        self.val.set(v)
        self.entry_var.set(core.fmt(v))
        self._sync()


class VehiclePage:
    """Trucks or Cars page."""

    def __init__(self, app, page, cls):
        self.app, self.cls = app, cls
        self.rows, self.wheel_rows, self.model_vars = {}, {}, {}
        plural_name = "Trucks" if cls == "truck" else "Cars"
        self.sub = app._page_header(page, plural_name)
        bar = app._button_bar(page)
        app._btn(bar, "Reset page", self.reset, icon="refresh").pack(side="left", padx=(0, 8))
        app._btn(bar, "Build mod", app.build, accent=True, icon="wrench").pack(side="left")

        sc = Scroll(page, bg=CONTENT)
        sc.pack(fill="both", expand=True, pady=(18, 0))
        p = sc.inner
        if cls == "truck":
            note = ("Only changes truck files. Truck files don't set handling values themselves, so steering, "
                    "damping, sway bar, centre of gravity and air resistance are written as exact values.")
        else:
            note = ("Only changes the drivable cars. Handling values multiply each car's own stock value, "
                    "so every car keeps its own feel.")
        app._label(p, note, font=app.f.small, fg=FG2, bg=CONTENT, wraplength=900).pack(
            anchor="w", padx=36, pady=(0, 12))

        if cls == "car":
            b = app._settings_card(p, "Anti-flip", "Stops cars rolling over in fast corners: lowers the centre "
                                                   "of gravity and stiffens the sway bar and dampers.")
            row = tk.Frame(b, bg=CARD)
            row.grid(row=0, column=0, columnspan=5, sticky="w")
            for name in core.ANTI_FLIP:
                app._btn(row, name, lambda n=name: self.apply_preset(core.ANTI_FLIP[n]),
                         bg="Card").pack(side="left", padx=(0, 8))
            app._btn(row, "Off", lambda: [self.rows[k].reset() for k in core.ANTI_FLIP["Max"]],
                     bg="Card").pack(side="left")

        for part in core.PARTS:
            title, sub = PART_TITLES[part]
            body = app._settings_card(p, title, sub)
            body.columnconfigure(0, minsize=360)   # line the sliders up across cards
            body.columnconfigure(1, weight=0, minsize=60)
            body.columnconfigure(2, weight=1)
            r = 0
            for prm in core.VEHICLE_PARAMS:
                if prm[1] == part:
                    self.rows[prm[0]] = ParamRow(app, body, r, cls, prm)
                    r += 1

        self.wheel_body = app._settings_card(
            p, "Front wheel angle per model",
            "How far the tyres turn (10-70°). This is not the steering wheel rotation (that's on the Steering "
            "page). Overrides 'Max steering angle' above for the models you switch on.")
        self.models_body = app._settings_card(
            p, "Apply to", "Which %s the Engine, Transmission and Chassis settings change." % plural_name.lower())
        tk.Frame(p, bg=CONTENT, height=12).pack()

    # ---- models / per-model wheel angle (filled once the game is read)
    def load(self, data, cfg):
        names = sorted(data.vehicles[self.cls])
        self.sub.configure(text="%s · %s" % (plural(len(names), self.cls),
                                             "def/vehicle/%s/" % self.cls))
        for w in self.models_body.winfo_children():
            w.destroy()
        sel = cfg.get("models", {}).get(self.cls)
        self.model_vars = {}
        links = tk.Frame(self.models_body, bg=CARD)
        links.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        Link(links, "Select all", lambda: [v.set(True) for v in self.model_vars.values()], self.app.f).pack(
            side="left")
        Link(links, "Select none", lambda: [v.set(False) for v in self.model_vars.values()], self.app.f).pack(
            side="left", padx=(16, 0))
        for i, m in enumerate(names):
            v = tk.BooleanVar(value=sel is None or m in sel)
            ttk.Checkbutton(self.models_body, text=model_title(m), variable=v, style="Card.TCheckbutton").grid(
                row=1 + i // 3, column=i % 3, sticky="w", padx=(0, 28), pady=2)
            self.model_vars[m] = v
        self.load_wheel(data, cfg.get("wheel", {}).get(self.cls, {}))

    def load_wheel(self, data, saved):
        app, body = self.app, self.wheel_body
        for w in body.winfo_children():
            w.destroy()
        self.wheel_rows = {}
        body.columnconfigure(0, minsize=250)
        body.columnconfigure(1, weight=0, minsize=170)
        body.columnconfigure(2, weight=1)
        for r, model in enumerate(sorted(data.vehicles[self.cls])):
            stock = data.stock_steering(self.cls, model)
            stock_txt = ("stock " + "/".join(core.fmt(v) for v in stock) + "°") if stock else "game default"
            start = min(max(saved.get(model, stock[0] if stock else 35.0), 10.0), 70.0)
            on = tk.BooleanVar(value=model in saved)
            val = tk.DoubleVar(value=start)
            txt = tk.StringVar(value=core.fmt(start))
            sw = ttk.Checkbutton(body, text=model_title(model), variable=on, style="Card.Switch.TCheckbutton")
            sw.grid(row=r, column=0, sticky="w", pady=3)
            app._label(body, stock_txt, font=app.f.small, fg=FG3, width=14).grid(row=r, column=1, sticky="w",
                                                                                 padx=(6, 12))
            sc = ttk.Scale(body, from_=10, to=70, variable=val, style="Card.Horizontal.TScale",
                           command=lambda _=None, v=val, t=txt: t.set(core.fmt(round(v.get() * 2) / 2)))
            sc.grid(row=r, column=2, sticky="ew")
            en = ttk.Entry(body, textvariable=txt, width=6, style="Card.TEntry")
            en.grid(row=r, column=3, padx=(12, 4))
            app._label(body, "°", fg=FG2).grid(row=r, column=4, sticky="w")

            def sync(on=on, sc=sc, en=en):
                for w in (sc, en):
                    w.state(["!disabled"] if on.get() else ["disabled"])
            sw.configure(command=sync)
            sync()
            self.wheel_rows[model] = (on, val, txt)

    def wheel(self):
        out = {}
        for model, (on, val, txt) in self.wheel_rows.items():
            if on.get():
                try:
                    out[model] = min(max(float(txt.get()), 10.0), 70.0)
                except ValueError:
                    out[model] = round(val.get() * 2) / 2
        return out

    def selected_models(self):
        return [m for m, v in self.model_vars.items() if v.get()]

    def apply_preset(self, values):
        for k, v in values.items():
            self.rows[k].set({"on": True, "value": v})

    def get(self):
        return {k: r.get() for k, r in self.rows.items()}

    def set(self, d):
        for k, r in self.rows.items():
            if k in (d or {}):
                r.set(d[k])

    def reset(self):
        for r in self.rows.values():
            r.reset()
        for on, _, _ in self.wheel_rows.values():
            on.set(False)


# ========================================================================= app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1320x900")
        self.minsize(1100, 720)
        ico = os.path.join(HERE, "app.ico")
        if os.path.isfile(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass
        self.data = None
        self.cfg = load_settings()
        self._compact = False
        self.switcher = None
        self._rot_cache = {}
        self._auto_msg = ""

        s = self.cfg
        self.game_dir = tk.StringVar(value=s.get("game_dir") or core.DEFAULT_GAME)
        self.mod_dir = tk.StringVar(value=s.get("mod_dir") or core.DEFAULT_MODS)
        rot = s.get("rotation", {})
        for cls in ("truck", "car"):  # very old builds put wheel degrees in the tyre angle
            old = s.get(cls, {}).get("max_steering_angle", {})
            if old.get("value", 0) >= 90:
                rot.setdefault(cls, old["value"])
                s[cls].pop("max_steering_angle")
        self.rot_truck = tk.StringVar(value=core.fmt(rot.get("truck", 1800)))
        self.rot_car = tk.StringVar(value=core.fmt(rot.get("car", 900)))
        self.auto_on = tk.BooleanVar(value=s.get("autoswitch", False))
        self.moza_on = tk.BooleanVar(value=s.get("moza", True))
        info = {**core.DEFAULT_INFO, **s.get("info", {})}
        self.i_name = tk.StringVar(value=info["name"])
        self.i_author = tk.StringVar(value=info["author"])
        self.i_version = tk.StringVar(value=info["version"])
        self.i_icon = tk.StringVar(value=info["icon"])
        self.i_bump = tk.BooleanVar(value=info.get("bump", False))
        self.i_summary = tk.BooleanVar(value=info.get("summary", True))
        self.status_var = tk.StringVar(value="Reading game files…")
        self.shared_vars, self.shared_stock = {}, {}

        self.f = Fonts(self)
        apply_theme(self)
        self._build()
        dark_title_bar(self)
        self.desc.insert("1.0", info["description"])
        self.truck.set(s.get("truck"))
        self.car.set(s.get("car"))
        for v in (self.i_name, self.i_version, self.i_author, self.mod_dir):
            v.trace_add("write", lambda *a: self._update_summary())
        self.game_dir.trace_add("write", lambda *a: self._check_paths())
        self.mod_dir.trace_add("write", lambda *a: self._check_paths())
        self._check_paths()
        self._show("truck")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_rotation_cache()
        self.after(100, self.load_game)
        if self.auto_on.get():
            self.toggle_auto(save=False)

    # ------------------------------------------------------------ small builders (as in the GTA IV Exporter)
    def _label(self, parent, text="", font=None, fg=FG, bg=CARD, anchor="w", justify="left", **kw):
        return tk.Label(parent, text=text, font=font or self.f.body, fg=fg, bg=bg, anchor=anchor, justify=justify, **kw)

    def _page_header(self, page, title):
        head = tk.Frame(page, bg=CONTENT)
        head.pack(fill="x", padx=36, pady=(30, 0))
        tk.Label(head, text=title, font=self.f.title, fg=FG, bg=CONTENT).pack(side="left")
        sub = tk.Label(head, text="", font=self.f.body, fg=FG2, bg=CONTENT)
        sub.pack(side="left", padx=(14, 0), pady=(10, 0))
        return sub

    def _button_bar(self, page):
        bar = tk.Frame(page, bg=CONTENT)
        bar.pack(fill="x", padx=36, pady=(14, 0))
        inner = tk.Frame(bar, bg=CONTENT)
        inner.pack(side="right")
        return inner

    def _btn(self, parent, text, command, accent=False, bg="Page", icon=None):
        label = ("%s  %s" % (ICONS[icon], text)) if icon else text
        return ttk.Button(parent, text=label, command=command,
                          style="%s.%s" % (bg, "Accent.TButton" if accent else "TButton"))

    def _settings_card(self, parent, title, subtitle=None):
        card = Card(parent)
        card.pack(fill="x", padx=36, pady=(0, 12))
        self._label(card.body, title, font=self.f.section).pack(anchor="w")
        if subtitle:
            sub = self._label(card.body, subtitle, font=self.f.small, fg=FG3, wraplength=900)
            sub.pack(anchor="w", pady=(2, 0), fill="x")
            card.body.bind("<Configure>", lambda e, w=sub: w.configure(wraplength=max(200, e.width - 8)), add="+")
        body = tk.Frame(card.body, bg=CARD)
        body.pack(fill="x", pady=(10, 0))
        body.columnconfigure(1, weight=1)
        return body

    def _path_row(self, parent, row, label, var, browse):
        self._label(parent, label, fg=FG2).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)
        self._btn(parent, "Browse…", browse, bg="Card").grid(row=row, column=2, padx=(8, 0), pady=4)
        st = self._label(parent, "", font=self.f.small, width=26)
        st.grid(row=row, column=3, sticky="w", padx=(12, 0))
        return st

    # ------------------------------------------------------------ layout
    def _build(self):
        top = tk.Frame(self, bg=BG, height=44)
        top.pack(fill="x")
        ham = tk.Label(top, text=ICONS["menu"], font=self.f.icon, fg=FG, bg=BG, cursor="hand2")
        ham.pack(side="left", padx=(18, 14), pady=10)
        ham.bind("<Button-1>", lambda e: self._toggle_sidebar())
        tk.Label(top, text=ICONS["gauge"], font=self.f.icon, fg=ACCENT, bg=BG).pack(side="left", padx=(0, 10))
        tk.Label(top, text="ATS Physics Tuner", font=self.f.strong, fg=FG, bg=BG).pack(side="left")
        tk.Label(top, text="truck & car physics for American Truck Simulator", font=self.f.small, fg=FG2,
                 bg=BG).pack(side="left", padx=(10, 0))
        tk.Label(top, textvariable=self.status_var, font=self.f.small, fg=FG2, bg=BG).pack(side="right", padx=18)

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)
        self.side = tk.Frame(main, bg=BG, width=250)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.nav = {
            "truck": NavItem(self.side, "truck", "Trucks", lambda: self._show("truck"), self.f),
            "car": NavItem(self.side, "car", "Cars", lambda: self._show("car"), self.f),
            "shared": NavItem(self.side, "sliders", "Shared physics", lambda: self._show("shared"), self.f),
            "steering": NavItem(self.side, "steer", "Steering", lambda: self._show("steering"), self.f),
            "info": NavItem(self.side, "tag", "Mod info", lambda: self._show("info"), self.f),
            "build": NavItem(self.side, "wrench", "Build", lambda: self._show("build"), self.f),
            "settings": NavItem(self.side, "settings", "Settings", lambda: self._show("settings"), self.f),
        }
        for n in self.nav.values():
            n.pack(fill="x", padx=6, pady=1)
        about = NavItem(self.side, "info", "About", self._about, self.f)
        about.pack(side="bottom", fill="x", padx=6, pady=(4, 10))
        self.about_item = about
        self.game_card = Card(self.side, bg=BG, pad=(12, 10))
        self.game_card.pack(side="bottom", fill="x", padx=6, pady=(0, 2))
        self._label(self.game_card.body, "GAME", font=self.f.small, fg=FG2).pack(anchor="w")
        self._label(self.game_card.body, "American Truck Simulator", font=self.f.strong).pack(anchor="w", pady=(2, 0))
        self.game_info = self._label(self.game_card.body, "Reading…", font=self.f.small, fg=FG2)
        self.game_info.pack(anchor="w")
        self.side_auto = self._label(self.game_card.body, "", font=self.f.small, fg=OK, wraplength=210)
        self.side_auto.pack(anchor="w", fill="x")

        content = Card(main, bg=BG, color=CONTENT, border="#1a1a1a", radius=8, pad=(0, 0), fill=True)
        content.pack(side="left", fill="both", expand=True)
        holder = content.body
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for name in ("truck", "car", "shared", "steering", "info", "build", "settings"):
            page = tk.Frame(holder, bg=CONTENT)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self.truck = VehiclePage(self, self.pages["truck"], "truck")
        self.car = VehiclePage(self, self.pages["car"], "car")
        self._build_shared(self.pages["shared"])
        self._build_steering(self.pages["steering"])
        self._build_info(self.pages["info"])
        self._build_build(self.pages["build"])
        self._build_settings(self.pages["settings"])

    def _toggle_sidebar(self):
        self._compact = not self._compact
        self.side.configure(width=60 if self._compact else 250)
        for n in list(self.nav.values()) + [self.about_item]:
            n.compact(self._compact)
        if self._compact:
            self.game_card.pack_forget()
        else:
            self.game_card.pack(side="bottom", fill="x", padx=6, pady=(0, 2), after=self.about_item)

    def _show(self, name):
        self.pages[name].tkraise()
        for k, n in self.nav.items():
            n.select(k == name)
        if name == "build":
            self._update_summary()

    def _about(self):
        messagebox.showinfo(APP_NAME, "%s\n\nTune truck and car physics in American Truck Simulator separately, "
                                      "switch steering wheel rotation (and a MOZA wheelbase) automatically "
                                      "between them, and pack it all as a normal .scs mod.\n\nBy wackyhacky."
                            % APP_NAME)

    # ------------------------------------------------------------ shared page
    def _build_shared(self, page):
        self._page_header(page, "Shared physics").configure(text="def/vehicle/physics.sii")
        bar = self._button_bar(page)
        self._btn(bar, "Reset page", self._reset_shared, icon="refresh").pack(side="left")
        sc = Scroll(page, bg=CONTENT)
        sc.pack(fill="both", expand=True, pady=(18, 0))
        warn = Card(sc.inner)
        warn.pack(fill="x", padx=36, pady=(0, 12))
        r = tk.Frame(warn.body, bg=CARD)
        r.pack(fill="x")
        tk.Label(r, text=ICONS["info"], font=self.f.icon, fg=WARN, bg=CARD).pack(side="left", padx=(0, 12))
        self._label(r, "ATS uses ONE physics file for every vehicle, so these change trucks AND cars. "
                       "Use the Trucks and Cars pages to tune them separately.", wraplength=820).pack(side="left")
        self.shared_body = self._settings_card(sc.inner, "Values", "Stock value on the right. Leave a value at "
                                                                   "stock to keep the game's own.")
        tk.Frame(sc.inner, bg=CONTENT, height=12).pack()

    def _load_shared(self, stock, saved):
        b = self.shared_body
        for w in b.winfo_children():
            w.destroy()
        self.shared_stock, self.shared_vars = stock, {}
        keys = sorted(stock, key=lambda k: (k not in core.SHARED_LABELS, k))
        b.columnconfigure(1, weight=1)
        for r, k in enumerate(keys):
            self._label(b, core.SHARED_LABELS.get(k, k.replace("_", " ").capitalize())).grid(
                row=r, column=0, sticky="w", pady=3)
            self._label(b, k, font=self.f.small, fg=FG3).grid(row=r, column=1, sticky="w", padx=16)
            v = tk.StringVar(value=core.fmt((saved or {}).get(k, stock[k])))
            ttk.Entry(b, textvariable=v, width=10, style="Card.TEntry").grid(row=r, column=2, pady=3)
            self._label(b, "stock %s" % core.fmt(stock[k]), font=self.f.small, fg=FG3, width=14).grid(
                row=r, column=3, sticky="w", padx=(12, 0))
            self.shared_vars[k] = v

    def _shared(self):
        out = {}
        for k, v in self.shared_vars.items():
            try:
                out[k] = float(v.get())
            except ValueError:
                out[k] = self.shared_stock[k]
        return out

    def _reset_shared(self):
        for k, v in self.shared_vars.items():
            v.set(core.fmt(self.shared_stock[k]))

    # ------------------------------------------------------------ steering page
    def _build_steering(self, page):
        self._page_header(page, "Steering").configure(text="Steering wheel rotation for trucks and cars")
        sc = Scroll(page, bg=CONTENT)
        sc.pack(fill="both", expand=True, pady=(18, 0))
        p = sc.inner

        b = self._settings_card(p, "Steering wheel rotation", "Lock-to-lock degrees of the in-cab wheel "
                                "(Options > Controls > Steering rotation). ATS keeps one value per profile, so "
                                "it is switched for you when you change vehicle.")
        for r, (label, var, cls) in enumerate((("Trucks", self.rot_truck, "truck"), ("Cars", self.rot_car, "car"))):
            tk.Label(b, text=ICONS[cls], font=self.f.icon, fg=FG2, bg=CARD).grid(row=r, column=0, padx=(0, 12))
            self._label(b, label, width=10).grid(row=r, column=1, sticky="w")
            ttk.Entry(b, textvariable=var, width=8, style="Card.TEntry").grid(row=r, column=2, pady=4)
            self._label(b, "°", fg=FG2).grid(row=r, column=3, sticky="w", padx=(4, 16))
            self._btn(b, "Use now", lambda c=cls: self.use_rotation(c), bg="Card").grid(row=r, column=4, sticky="w")
        b.columnconfigure(1, weight=0)
        b.columnconfigure(5, weight=1)

        a = self._settings_card(p, "Auto-switch", "Watches the game log for truck/car changes. When ATS is in "
                                "front it opens the console (~), types g_steer_anim_range, presses Enter and "
                                "closes it again - about half a second. Keep this app open while you play.")
        ttk.Checkbutton(a, text="Switch steering wheel rotation automatically", variable=self.auto_on,
                        command=self.toggle_auto, style="Card.Switch.TCheckbutton").grid(row=0, column=0, sticky="w")
        self.auto_label = self._label(a, "Off", font=self.f.small, fg=FG3)
        self.auto_label.grid(row=1, column=0, sticky="w", pady=(8, 0))

        m = self._settings_card(p, "MOZA wheelbase", "Also sets the base's own rotation over USB when you change "
                                "vehicle. MOZA Pit House must be closed while you drive (it locks the port); "
                                "your force-feedback settings are stored on the base.")
        ttk.Checkbutton(m, text="Set MOZA rotation with auto-switch", variable=self.moza_on,
                        command=self.toggle_auto, style="Card.Switch.TCheckbutton").grid(row=0, column=0, sticky="w")
        self._btn(m, "Test connection", self.test_moza, bg="Card").grid(row=0, column=2, sticky="e")
        self.moza_label = self._label(m, "", font=self.f.small, fg=FG3)
        self.moza_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _rotation(self, cls):
        var = self.rot_truck if cls == "truck" else self.rot_car
        try:
            return min(max(float(var.get()), 90.0), 3600.0)
        except ValueError:
            return 1800.0 if cls == "truck" else 900.0

    def use_rotation(self, cls):
        deg = self._rotation(cls)
        cmd = "%s %s" % (core.STEER_CVAR, core.fmt(deg))
        if core.game_running():
            self.clipboard_clear()
            self.clipboard_append(cmd)
            messagebox.showinfo(APP_NAME, "ATS is running, so it can't be changed from outside right now.\n\n"
                                          "Copied to clipboard:\n    %s\n\nIn game press ~, Ctrl+V, Enter. "
                                          "Or turn on Auto-switch." % cmd)
            return
        docs = os.path.dirname(self.mod_dir.get())
        cfgs = core.profile_configs(docs)
        if not cfgs:
            messagebox.showerror(APP_NAME, "No profile config_local.cfg found under\n%s" % docs)
            return
        for c in cfgs:
            core.write_steer_range(c, deg)
        messagebox.showinfo(APP_NAME, "Steering rotation set to %s° in %s.\n(Backup: config_local.cfg.tuner_backup)"
                            % (core.fmt(deg), plural(len(cfgs), "profile")))

    def _refresh_rotation_cache(self):
        self._rot_cache = {"truck": self._rotation("truck"), "car": self._rotation("car")}
        self.after(500, self._refresh_rotation_cache)

    def _auto_status(self, msg):
        if msg != self._auto_msg:
            self._auto_msg = msg
            self.after(0, self._show_auto, msg)

    def _show_auto(self, msg):
        if not self.auto_on.get():
            return
        text = msg.replace("Auto-switch: ", "")
        bad = "wheel:" in text
        self.auto_label.configure(text=text, fg=WARN if bad else OK)
        self.side_auto.configure(text="Auto-switch: " + text.split(" | ")[0], fg=OK)
        if " | " in text:
            hw = text.split(" | ", 1)[1].replace("wheel: ", "")
            self.moza_label.configure(text=hw, fg=WARN if bad else OK)

    @staticmethod
    def _moza_set(deg):
        port, done = moza.set_rotation(deg)
        return "MOZA %s° (%s)" % (core.fmt(done), port)

    def test_moza(self):
        try:
            ports = moza.find_ports()
            if not ports:
                raise moza.MozaError("No MOZA USB device found.")
            with moza.MozaBase(ports[0]) as b:
                cur = b.get_rotation()
                if cur is None:
                    raise moza.MozaError("%s opened but the base didn't answer." % ports[0])
                b.set_rotation(cur)
                after = b.get_rotation()
            self.moza_label.configure(text="Connected on %s - rotation %s°" % (ports[0], cur), fg=OK)
            messagebox.showinfo(APP_NAME, "MOZA connected on %s.\nCurrent rotation: %s° (read back after write: "
                                          "%s°).\n\nAuto-switch can control it." % (ports[0], cur, after))
        except moza.MozaError as e:
            self.moza_label.configure(text=str(e), fg=BAD)
            messagebox.showerror(APP_NAME, str(e))

    def toggle_auto(self, save=True):
        if self.switcher:
            self.switcher.stop()
            self.switcher = None
        if self.auto_on.get():
            log = os.path.join(os.path.dirname(self.mod_dir.get()), "game.log.txt")
            hw = self._moza_set if self.moza_on.get() else None
            self._auto_msg = ""
            self.switcher = AutoSwitcher(log, lambda cls: self._rot_cache.get(cls, 900.0), self._auto_status, hw)
            self.switcher.start()
            self.auto_label.configure(text="Starting…", fg=FG2)
        else:
            self.auto_label.configure(text="Off", fg=FG3)
            self.side_auto.configure(text="")
        if not self.moza_on.get():
            self.moza_label.configure(text="")
        if save:
            self.save_cfg()

    # ------------------------------------------------------------ mod info page
    def _build_info(self, page):
        self._page_header(page, "Mod info").configure(text="What the in-game Mod Manager shows")
        sc = Scroll(page, bg=CONTENT)
        sc.pack(fill="both", expand=True, pady=(18, 0))
        b = self._settings_card(sc.inner, "Package", "Name, author and version of the .scs mod.")
        for r, (label, var) in enumerate((("Mod name", self.i_name), ("Author", self.i_author))):
            self._label(b, label, fg=FG2).grid(row=r, column=0, sticky="w", padx=(0, 16), pady=4)
            ttk.Entry(b, textvariable=var, style="Card.TEntry").grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        self._label(b, "Version", fg=FG2).grid(row=2, column=0, sticky="w", padx=(0, 16), pady=4)
        vf = tk.Frame(b, bg=CARD)
        vf.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Entry(vf, textvariable=self.i_version, width=12, style="Card.TEntry").pack(side="left")
        ttk.Checkbutton(vf, text="Increase on every build (1.0 → 1.1 → 1.2)", variable=self.i_bump,
                        style="Card.Switch.TCheckbutton").pack(side="left", padx=(20, 0))

        d = self._settings_card(sc.inner, "Description", "Shown on the mod's page in the Mod Manager.")
        self.desc = tk.Text(d, height=9, wrap="word", font=self.f.body, bg="#232323", fg=FG, insertbackground=FG,
                            relief="flat", bd=0, padx=10, pady=8, highlightthickness=1, highlightbackground=LINE,
                            highlightcolor=ACCENT)
        self.desc.grid(row=0, column=0, columnspan=3, sticky="ew")
        d.columnconfigure(0, weight=1)
        ttk.Checkbutton(d, text="List the changed settings under my description", variable=self.i_summary,
                        style="Card.Switch.TCheckbutton").grid(row=1, column=0, sticky="w", pady=(10, 0))

        i = self._settings_card(sc.inner, "Icon", "Optional. Any picture - it's cropped to 276 × 162 for the "
                                                  "Mod Manager thumbnail.")
        self.icon_status = self._path_row(i, 0, "Picture", self.i_icon, self._pick_icon)
        Link(i, "Remove icon", lambda: self.i_icon.set(""), self.f).grid(row=1, column=1, sticky="w")
        self.i_icon.trace_add("write", lambda *a: self._check_paths())

    def _pick_icon(self):
        fn = filedialog.askopenfilename(filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("All", "*.*")])
        if fn:
            self.i_icon.set(os.path.normpath(fn))

    def _info(self):
        return {"name": self.i_name.get().strip(), "author": self.i_author.get().strip(),
                "version": self.i_version.get().strip(), "description": self.desc.get("1.0", "end").strip(),
                "icon": self.i_icon.get().strip(), "summary": self.i_summary.get(), "bump": self.i_bump.get()}

    def _bump_version(self):
        parts = (self.i_version.get().strip() or "1.0").split(".")
        if parts[-1].isdigit():
            parts[-1] = str(int(parts[-1]) + 1)
        else:
            parts.append("1")
        self.i_version.set(".".join(parts))

    # ------------------------------------------------------------ build page
    def _build_build(self, page):
        self.build_sub = self._page_header(page, "Build")
        bar = self._button_bar(page)
        self._btn(bar, "Open mod folder", self.open_folder, icon="folder").pack(side="left", padx=(0, 8))
        self._btn(bar, "Import preset", self.import_preset, icon="download").pack(side="left", padx=(0, 8))
        self._btn(bar, "Export preset", self.export_preset, icon="upload").pack(side="left", padx=(0, 8))
        self._btn(bar, "Remove mod", self.remove, icon="delete").pack(side="left", padx=(0, 8))
        self._btn(bar, "Build mod", self.build, accent=True, icon="wrench").pack(side="left")

        row = tk.Frame(page, bg=CONTENT)
        row.pack(fill="x", padx=36, pady=(18, 0))
        row.columnconfigure(0, weight=3, uniform="b")
        row.columnconfigure(1, weight=2, uniform="b")
        st = Card(row)
        st.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._label(st.body, "Mod").pack(anchor="w")
        self.build_big = self._label(st.body, "—", font=self.f.value)
        self.build_big.pack(anchor="w", pady=(6, 0))
        self.build_note = self._label(st.body, "", font=self.f.small, fg=FG2, wraplength=560)
        self.build_note.pack(anchor="w", fill="x", pady=(8, 0))
        sm = Card(row)
        sm.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._label(sm.body, "This mod").pack(anchor="w")
        grid = tk.Frame(sm.body, bg=CARD)
        grid.pack(fill="x", pady=(8, 0))
        self.summary_vals = {}
        for i, key in enumerate(("Name", "Version", "Author", "Trucks", "Cars", "Shared", "File")):
            self._label(grid, key, font=self.f.small, fg=FG3).grid(row=i, column=0, sticky="w", padx=(0, 16), pady=1)
            v = self._label(grid, "", font=self.f.small)
            v.grid(row=i, column=1, sticky="w", pady=1)
            self.summary_vals[key] = v

        logc = Card(page, fill=True)
        logc.pack(fill="both", expand=True, padx=36, pady=(12, 24))
        self._label(logc.body, "Log").pack(anchor="w", pady=(0, 8))
        lf = tk.Frame(logc.body, bg=CARD)
        lf.pack(fill="both", expand=True)
        self.log = tk.Text(lf, wrap="word", font=self.f.mono, state="disabled", bg="#232323", fg=FG2,
                           insertbackground=FG, relief="flat", bd=0, padx=10, pady=8, highlightthickness=0)
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _count_on(self, settings, wheel):
        n = sum(1 for v in settings.values() if v["on"]) + len(wheel)
        return "no changes" if n == 0 else plural(n, "setting")

    def _update_summary(self):
        if not hasattr(self, "summary_vals"):
            return
        out = os.path.join(self.mod_dir.get(), core.MOD_NAME)
        sv = self.summary_vals
        sv["Name"].configure(text=elide(self.i_name.get() or core.DEFAULT_INFO["name"], 40))
        sv["Version"].configure(text=self.i_version.get() or "1.0")
        sv["Author"].configure(text=self.i_author.get() or "—")
        sv["Trucks"].configure(text=self._count_on(self.truck.get(), self.truck.wheel()))
        sv["Cars"].configure(text=self._count_on(self.car.get(), self.car.wheel()))
        if self.shared_stock:
            n = sum(1 for k, v in self._shared().items() if abs(v - self.shared_stock[k]) > 1e-9)
            sv["Shared"].configure(text="no changes" if n == 0 else plural(n, "value"))
        sv["File"].configure(text=elide(core.MOD_NAME, 40))
        if os.path.isfile(out):
            t = time.strftime("%d %b %Y, %H:%M", time.localtime(os.path.getmtime(out)))
            self.build_big.configure(text="Installed", fg=OK)
            self.build_note.configure(text="%s\nLast built %s. In game: Mod Manager → enable it and put it at "
                                           "the top." % (elide(out, 90), t))
            self.build_sub.configure(text="Last built %s" % t)
        else:
            self.build_big.configure(text="Not built yet", fg=FG)
            self.build_note.configure(text="Press Build mod to write %s" % elide(out, 90))
            self.build_sub.configure(text="")

    # ------------------------------------------------------------ settings page
    def _build_settings(self, page):
        self._page_header(page, "Settings").configure(text="Where the game and your mods are")
        sc = Scroll(page, bg=CONTENT)
        sc.pack(fill="both", expand=True, pady=(18, 0))
        g = self._settings_card(sc.inner, "American Truck Simulator", "The install folder with the .scs archives "
                                                                      "(base.scs, def.scs, dlc_*.scs).")
        self.game_status = self._path_row(g, 0, "Game folder", self.game_dir, lambda: self._browse(self.game_dir))
        self._btn(g, "Reload game data", self.load_game, bg="Card").grid(row=1, column=1, sticky="w", pady=(6, 0))
        m = self._settings_card(sc.inner, "Mods", "Documents\\American Truck Simulator\\mod. The game log next to "
                                                  "it is used by Auto-switch.")
        self.mod_status = self._path_row(m, 0, "Mod folder", self.mod_dir, lambda: self._browse(self.mod_dir))
        a = self._settings_card(sc.inner, "App", "Settings are saved in %s" % SETTINGS_FILE)
        self._btn(a, "Reset every setting", self.reset_all, bg="Card").grid(row=0, column=0, sticky="w")

    def _browse(self, var):
        d = filedialog.askdirectory(initialdir=var.get())
        if d:
            var.set(os.path.normpath(d))

    def _set_status(self, label, ok, text):
        label.configure(text=("%s  %s" % (ICONS["check"] if ok else ICONS["cancel"], text)) if text else "",
                        fg=OK if ok else BAD)

    def _check_paths(self):
        gd = self.game_dir.get()
        n = len([f for f in os.listdir(gd) if f.endswith(".scs")]) if os.path.isdir(gd) else 0
        self._set_status(self.game_status, n > 0, plural(n, "archive") if n else "No .scs files here")
        md = self.mod_dir.get()
        self._set_status(self.mod_status, os.path.isdir(md), "Found" if os.path.isdir(md) else "Folder missing")
        ic = self.i_icon.get()
        self._set_status(self.icon_status, os.path.isfile(ic), ("Found" if os.path.isfile(ic) else "Not found")
                         if ic else "")
        self._update_summary()

    # ------------------------------------------------------------ settings persistence
    def collect(self):
        return {
            "game_dir": self.game_dir.get(), "mod_dir": self.mod_dir.get(),
            "truck": self.truck.get(), "car": self.car.get(), "shared": self._shared(),
            "models": {"truck": self.truck.selected_models(), "car": self.car.selected_models()}
            if self.data else self.cfg.get("models", {}),
            "wheel": {"truck": self.truck.wheel(), "car": self.car.wheel()}
            if self.data else self.cfg.get("wheel", {}),
            "rotation": {"truck": self._rotation("truck"), "car": self._rotation("car")},
            "autoswitch": self.auto_on.get(), "moza": self.moza_on.get(), "info": self._info(),
        }

    def save_cfg(self):
        save_settings(self.collect())

    def _on_close(self):
        if self.switcher:
            self.switcher.stop()
        self.save_cfg()
        self.destroy()

    # ------------------------------------------------------------ game data
    def load_game(self):
        self.status_var.set("Reading game files…")
        self.game_info.configure(text="Reading…")
        gd = self.game_dir.get()

        def work():
            try:
                data = core.GameData(gd, progress=lambda m: self.after(0, self.status_var.set, m))
                self.after(0, self._loaded, data)
            except Exception as e:  # noqa: BLE001
                self.after(0, self._load_failed, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, err):
        self.status_var.set("Couldn't read the game")
        self.game_info.configure(text="Not found - see Settings")
        messagebox.showerror(APP_NAME, "Couldn't read the game files:\n%s\n\nPick the game folder in Settings." % err)

    def _loaded(self, data):
        cfg = self.collect() if self.data else self.cfg
        self.data = data
        self.truck.load(data, cfg)
        self.car.load(data, cfg)
        if data.physics_text:
            self._load_shared(core.physics_values(data.physics_text), cfg.get("shared"))
        nt, nc = len(data.vehicles["truck"]), len(data.vehicles["car"])
        self.game_info.configure(text="%s · %s" % (plural(nt, "truck"), plural(nc, "car")))
        self.status_var.set("Ready")
        self._log("Read %s and %s from %s" % (plural(nt, "truck"), plural(nc, "car"), self.game_dir.get()))
        self._update_summary()
        if not nc:
            messagebox.showinfo(APP_NAME, "No drivable cars were found (they come with the Ford DLC). "
                                          "The Cars page will have nothing to change.")

    # ------------------------------------------------------------ actions
    def build(self):
        if not self.data:
            messagebox.showwarning(APP_NAME, "Still reading the game files - try again in a moment.")
            return
        old_version = self.i_version.get()
        if self.i_bump.get():
            self._bump_version()
        cfg = self.collect()
        out = os.path.join(cfg["mod_dir"], core.MOD_NAME)
        self._show("build")
        self.status_var.set("Building…")
        self.update_idletasks()
        try:
            n = core.build_mod(self.data, cfg, out)
        except Exception as e:  # noqa: BLE001
            self.i_version.set(old_version)
            self._log("Build failed: %s" % e)
            self.status_var.set("Build failed")
            messagebox.showerror(APP_NAME, "Build failed:\n%s" % e)
            return
        if n == 0:
            self.i_version.set(old_version)
            self._log("Nothing to build - every setting is stock.")
            self.status_var.set("Nothing to build")
            messagebox.showinfo(APP_NAME, "Every setting is stock, so no mod was written.\n"
                                          "Switch on at least one setting first.")
            return
        self.save_cfg()
        info = cfg["info"]
        by = " by %s" % info["author"] if info["author"] else ""
        self._log("Built %s v%s%s: %s → %s" % (info["name"], info["version"], by, plural(n, "file"), out))
        self.status_var.set("Built v%s" % info["version"])
        self._update_summary()
        messagebox.showinfo(APP_NAME, "%s  v%s%s\n\nWrote %s to:\n%s\n\nIn game: Mod Manager → enable '%s' and put "
                                      "it at the top. Restart the game or reload the profile after rebuilding."
                            % (info["name"], info["version"], by, plural(n, "file"), out, info["name"]))

    def open_folder(self):
        d = self.mod_dir.get()
        if os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo(APP_NAME, "The mod folder doesn't exist:\n%s" % d)

    def remove(self):
        out = os.path.join(self.mod_dir.get(), core.MOD_NAME)
        if not os.path.exists(out):
            messagebox.showinfo(APP_NAME, "The mod isn't installed.")
            return
        if messagebox.askyesno(APP_NAME, "Delete %s?" % out):
            os.remove(out)
            self._log("Removed %s" % out)
            self._update_summary()

    def reset_all(self):
        if messagebox.askyesno(APP_NAME, "Reset every truck, car and shared setting to stock?"):
            self.truck.reset()
            self.car.reset()
            self._reset_shared()
            self._update_summary()

    def export_preset(self):
        fn = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Preset", "*.json")])
        if fn:
            c = self.collect()
            with open(fn, "w", encoding="utf-8") as f:
                json.dump({k: c[k] for k in ("truck", "car", "shared", "wheel", "rotation")}, f, indent=2)
            self._log("Preset saved: %s" % fn)

    def import_preset(self):
        fn = filedialog.askopenfilename(filetypes=[("Preset", "*.json")])
        if not fn:
            return
        try:
            with open(fn, encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, "Couldn't read the preset:\n%s" % e)
            return
        self.truck.set(p.get("truck", {}))
        self.car.set(p.get("car", {}))
        if self.data:
            self.truck.load_wheel(self.data, p.get("wheel", {}).get("truck", {}))
            self.car.load_wheel(self.data, p.get("wheel", {}).get("car", {}))
            if self.data.physics_text:
                self._load_shared(core.physics_values(self.data.physics_text), p.get("shared"))
        for cls, var in (("truck", self.rot_truck), ("car", self.rot_car)):
            if cls in p.get("rotation", {}):
                var.set(core.fmt(p["rotation"][cls]))
        self._log("Preset loaded: %s" % fn)
        self._update_summary()


def main():
    try:
        ctypes = __import__("ctypes")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except Exception:  # noqa: BLE001
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
