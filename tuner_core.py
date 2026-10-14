"""Core logic: read vehicle defs from the ATS archives, patch them, write a mod."""
import glob
import os
import re
import zipfile

from hashfs import HashFS

DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\American Truck Simulator"
DEFAULT_MODS = os.path.join(os.path.expanduser("~"), "Documents", "American Truck Simulator", "mod")
MOD_NAME = "zz_truck_car_physics_tuner.scs"
PHYSICS_PATH = "def/vehicle/physics.sii"
PARTS = ("engine", "transmission", "chassis")

# Per-vehicle parameters. 'mult' scales the value the game already has;
# 'set' writes an absolute value (used when the vehicle does not define it).
#   key, part, label, kind, min, max, default, axle ('all' | 'front' | 'rear' | None)
VEHICLE_PARAMS = [
    # engine
    ("torque", "engine", "Engine torque / power", "mult", 0.25, 5.0, 1.0, None),
    ("engine_brake", "engine", "Engine brake strength", "mult", 0.0, 4.0, 1.0, None),
    ("flywheel_inertia", "engine", "Flywheel inertia (rev response)", "mult", 0.2, 3.0, 1.0, None),
    # transmission
    ("differential_ratio", "transmission", "Final drive ratio (lower = faster top speed)", "mult", 0.5, 2.0, 1.0, None),
    ("shift_time", "transmission", "Gear shift time (s)", "set", 0.05, 2.0, 0.4, None),
    # chassis
    ("kerb_weight", "chassis", "Vehicle weight", "mult", 0.3, 3.0, 1.0, "all"),
    ("max_steering_angle", "chassis", "Max steering angle (deg)", "set", 15.0, 60.0, 35.0, None),
    ("suspension_damping_factor", "chassis", "Suspension damping - front axle", "set", 0.1, 3.0, 0.7, "front"),
    ("suspension_damping_factor#rear", "chassis", "Suspension damping - rear axles", "set", 0.1, 3.0, 0.5, "rear"),
    ("sway_bar_stiffness", "chassis", "Anti-roll (sway) bar stiffness", "set", 0.0, 3.0, 0.5, None),
    ("cog_height", "chassis", "Centre of gravity height (m)", "set", 0.2, 2.5, 0.55, None),
    ("air_resistance", "chassis", "Air resistance", "set", 0.0, 6.0, 0.5, None),
]

# Shared physics.sii values exposed in the GUI (everything numeric in the file
# is editable, these just get friendly labels).
SHARED_LABELS = {
    "brake_torque_factor": "Brake strength",
    "brake_balance": "Brake balance (front bias)",
    "braking_curve_peak_rel": "Extra braking at low speed",
    "steering_speed_coef": "Self-centering vs speed",
    "steering_angle_coef": "Self-centering vs angle",
    "steering_sensitivity_multiplier_0kmh": "Steering sensitivity @0 km/h (kbd/pad)",
    "steering_sensitivity_multiplier_100kmh": "Steering sensitivity @100 km/h (kbd/pad)",
    "damping_scaling_front_factor": "Suspension damping scale - front",
    "damping_scaling_rear_factor": "Suspension damping scale - rear",
    "damping_scaling_trailer_factor": "Suspension damping scale - trailer",
    "sway_bar_stiffness_factor": "Sway bar stiffness scale",
    "cabin_suspension_damping_factor": "Cabin suspension damping",
    "cabin_sway_bar_factor": "Cabin sway",
    "cabin_suspension_travel": "Cabin suspension travel",
    "air_resistance": "Air resistance (global)",
    "thrust_characteristic_viscosity_factor": "Engine drag (off throttle)",
    "wheel_viscosity_factor": "Rolling resistance",
    "bristle_stiffness_lateral_factor": "Tyre grip stiffness - lateral",
    "bristle_stiffness_longitudinal_factor": "Tyre grip stiffness - longitudinal",
    "slip_lateral_factor": "Tyre slip - lateral",
    "slip_longitudinal_factor": "Tyre slip - longitudinal",
}

# Cars already define these per model, so on the Car tab they are multipliers
# (each car keeps its own character). Trucks don't define them -> absolute values.
CAR_MULT = {"max_steering_angle", "suspension_damping_factor", "suspension_damping_factor#rear",
            "sway_bar_stiffness", "cog_height", "air_resistance"}


# Anti-flip presets for the Car tab (multipliers on each car's stock values).
# Rollovers come from a high centre of gravity plus too much body roll, so these
# lower the CoG and stiffen the anti-roll bar and dampers.
ANTI_FLIP = {
    "Mild": {"cog_height": 0.75, "sway_bar_stiffness": 1.5,
             "suspension_damping_factor": 1.2, "suspension_damping_factor#rear": 1.2},
    "Strong": {"cog_height": 0.55, "sway_bar_stiffness": 2.2,
               "suspension_damping_factor": 1.5, "suspension_damping_factor#rear": 1.5},
    "Max": {"cog_height": 0.35, "sway_bar_stiffness": 3.0,
            "suspension_damping_factor": 2.0, "suspension_damping_factor#rear": 2.0},
}


TRUCK_DEFAULTS = {"cog_height": 1.1, "air_resistance": 3.0, "suspension_damping_factor": 1.0,
                  "suspension_damping_factor#rear": 0.5, "sway_bar_stiffness": 1.0}


def param_spec(cls, p):
    """Return (kind, min, max, default) of a VEHICLE_PARAMS entry for a vehicle class."""
    key, _, _, kind, lo, hi, default, _ = p
    if cls == "car" and key in CAR_MULT:
        return "mult", (0.1 if key == "cog_height" else 0.2), 3.0, 1.0
    if cls == "truck":  # starting points sized for trucks (only used once ticked)
        default = TRUCK_DEFAULTS.get(key, default)
    return kind, lo, hi, default


NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class GameData:
    """Index of every truck/car engine, transmission and chassis def file."""

    def __init__(self, game_dir, progress=None):
        self.game_dir = game_dir
        self.archives = {}
        # vehicles[cls][model] = {part: [path, ...]}
        self.vehicles = {"truck": {}, "car": {}}
        self.where = {}   # path -> archive filename
        self.physics_text = None
        files = sorted(glob.glob(os.path.join(game_dir, "*.scs")))
        if not files:
            raise FileNotFoundError(f"No .scs archives found in {game_dir}")
        for i, fn in enumerate(files):
            name = os.path.basename(fn)
            if progress:
                progress(f"Reading {name} ({i + 1}/{len(files)})")
            try:
                fs = HashFS(fn)
            except Exception:
                continue  # not a HashFS v2 archive (or unreadable) - skip
            self.archives[name] = fs
            if self.physics_text is None:
                d = fs.read(PHYSICS_PATH)
                if d:
                    self.physics_text = d.decode("utf-8", "replace")
            for cls in ("truck", "car"):
                for model in fs.listdir(f"def/vehicle/{cls}") or []:
                    if not model.startswith("/"):
                        continue
                    model = model[1:]
                    for part in PARTS:
                        base = f"def/vehicle/{cls}/{model}/{part}"
                        for f in fs.listdir(base) or []:
                            if f.endswith(".sii") and not f.startswith("/"):
                                p = f"{base}/{f}"
                                self.where[p] = name
                                self.vehicles[cls].setdefault(model, {}).setdefault(part, []).append(p)

    def stock_steering(self, cls, model):
        """Max steering angles (deg) the model's chassis files define, or [] if none."""
        vals = set()
        for p in self.vehicles[cls].get(model, {}).get("chassis", []):
            m = re.search(r"^\s*max_steering_angle\s*:\s*(" + NUM + ")", self.read(p), re.M)
            if m:
                vals.add(float(m.group(1)))
        return sorted(vals)

    def read(self, path):
        return self.archives[self.where[path]].read(path).decode("utf-8", "replace")


# ---------------------------------------------------------------- SII patching
def _unit_span(text, unit_type):
    """(start, end) of the body of the first unit of the given type."""
    m = re.search(r"^\s*" + unit_type + r"\s*:\s*[\w.]+\s*\{", text, re.M)
    if not m:
        return None
    end = text.index("}", m.end())
    return m.end(), end


def _patch_unit(text, unit_type, fn):
    span = _unit_span(text, unit_type)
    if not span:
        return text
    a, b = span
    return text[:a] + fn(text[a:b]) + text[b:]


def _scale_scalar(body, key, mult):
    pat = re.compile(r"^(\s*" + key + r"\s*:\s*)(" + NUM + r")", re.M)
    return pat.sub(lambda m: m.group(1) + fmt(float(m.group(2)) * mult), body)


def _set_scalar(body, key, value):
    pat = re.compile(r"^(\s*" + key + r"\s*:\s*)(" + NUM + r")", re.M)
    if pat.search(body):
        return pat.sub(lambda m: m.group(1) + fmt(value), body)
    return _insert(body, f"\t{key}: {fmt(value)}\n")


def _insert(body, line):
    i = body.find("@include")
    i = body.rfind("\n", 0, i) + 1 if i >= 0 else len(body.rstrip()) + 1
    return body[:i] + "\n" + line + body[i:]


def _axle_count(body):
    return max(len(re.findall(r"^\s*kerb_weight\[", body, re.M)), 1)


def _array_values(body, key):
    return [float(v) for v in re.findall(r"^\s*" + key + r"\[\d*\]\s*:\s*(" + NUM + ")", body, re.M)]


def _replace_array(body, key, values):
    pat = re.compile(r"^\s*" + key + r"\[\d*\]\s*:.*\n?", re.M)
    lines = "".join(f"\t{key}[]: {fmt(v)}\n" for v in values)
    if pat.search(body):
        first = pat.search(body).start()
        body = pat.sub("", body)
        return body[:first] + lines + body[first:]
    return _insert(body, lines)


def _scale_array(body, key, mult):
    pat = re.compile(r"^(\s*" + key + r"\[\d*\]\s*:\s*)(" + NUM + r")", re.M)
    return pat.sub(lambda m: m.group(1) + fmt(float(m.group(2)) * mult), body)


def patch_part(text, part, settings, cls="truck"):
    """Apply one vehicle class's settings to a single engine/transmission/chassis file.

    settings: {param_key: {"on": bool, "value": float}}
    """
    unit = f"accessory_{part}_data"
    params = [p for p in VEHICLE_PARAMS if p[1] == part]

    def body_fn(body):
        damping = None
        for p in params:
            key, axle = p[0], p[7]
            kind = param_spec(cls, p)[0]
            s = settings.get(key)
            if not s or not s["on"]:
                continue
            v = float(s["value"])
            real_key = key.split("#")[0]
            if real_key == "suspension_damping_factor":
                if damping is None:
                    n = _axle_count(body)
                    cur = _array_values(body, real_key)
                    damping = cur + [None] * (n - len(cur)) if len(cur) < n else cur[:n]
                idx = [0] if axle == "front" else range(1, len(damping))
                for i in idx:
                    if kind == "mult":
                        if damping[i] is not None:
                            damping[i] *= v
                    else:
                        damping[i] = v
                continue
            if kind == "mult":
                if axle == "all":
                    body = _scale_array(body, real_key, v)
                else:
                    body = _scale_scalar(body, real_key, v)
            else:
                body = _set_scalar(body, real_key, v)
        if damping is not None:
            # axles the file didn't define and nobody set: use the slider's starting value
            spec = {q[0]: param_spec(cls, q)[3] for q in params}
            fill = [spec["suspension_damping_factor"]] + [spec["suspension_damping_factor#rear"]] * (len(damping) - 1)
            damping = [d if d is not None else fill[i] for i, d in enumerate(damping)]
            body = _replace_array(body, "suspension_damping_factor", damping)
        return body

    return _patch_unit(text, unit, body_fn)


def patch_physics(text, values):
    for key, v in values.items():
        text = re.sub(r"^(\s*" + key + r"\s*:\s*)(" + NUM + r")",
                      lambda m: m.group(1) + fmt(float(v)), text, flags=re.M)
    return text


def physics_values(text):
    body = text[text.index("{", text.index("physics_data")) + 1:]
    return {k: float(v) for k, v in re.findall(r"^\s*([a-z_0-9]+)\s*:\s*(" + NUM + r")\s*$", body, re.M)
            if k != "version"}


# ------------------------------------------------------------------ mod output
DEFAULT_INFO = {
    "name": "Truck & Car Physics Tuner",
    "author": "",
    "version": "1.0",
    "description": "",
    "icon": "",
    "summary": True,
}


def _sii_str(v):
    return '"' + str(v).replace("\\", "/").replace('"', "'").replace("\n", " ").strip() + '"'


def manifest(info, has_icon):
    lines = ["SiiNunit", "{", "mod_package : .package_name", "{",
             f"\tpackage_version: {_sii_str(info.get('version') or '1.0')}",
             f"\tdisplay_name: {_sii_str(info.get('name') or DEFAULT_INFO['name'])}",
             f"\tauthor: {_sii_str(info.get('author') or 'Unknown')}",
             '\tcategory[]: "physics"',
             '\tdescription_file: "description.txt"']
    if has_icon:
        lines.append('\ticon: "mod_icon.jpg"')
    return "\n".join(lines + ["}", "}", ""])


def make_icon(path):
    """Mod Manager icon: JPEG, 276x162. Converts any image if Pillow is installed."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(path).convert("RGB")
        # fill 276x162 without stretching (centre crop)
        tw, th = 276, 162
        scale = max(tw / im.width, th / im.height)
        im = im.resize((max(tw, round(im.width * scale)), max(th, round(im.height * scale))))
        left, top = (im.width - tw) // 2, (im.height - th) // 2
        out = BytesIO()
        im.crop((left, top, left + tw, top + th)).save(out, "JPEG", quality=90)
        return out.getvalue()
    except ImportError:
        if path.lower().endswith((".jpg", ".jpeg")):
            with open(path, "rb") as f:
                return f.read()
        raise ValueError("Icon must be a .jpg (276x162) - or install Pillow to use other images.")


def build_mod(data, cfg, out_path):
    """cfg = {"truck": {...}, "car": {...}, "shared": {key: value}, "models": {cls: [model, ...]}}"""
    files = {}
    for cls in ("truck", "car"):
        settings = cfg[cls]
        wheel = {m: float(v) for m, v in cfg.get("wheel", {}).get(cls, {}).items()}
        targets = set(cfg["models"][cls]) if any(s["on"] for s in settings.values()) else set()
        for model in sorted(targets | set(wheel)):
            for part, paths in data.vehicles[cls].get(model, {}).items():
                for p in paths:
                    orig = data.read(p)
                    new = patch_part(orig, part, settings, cls) if model in targets else orig
                    if part == "chassis" and model in wheel:
                        # per-model wheel angle beats the tab-wide steering setting
                        new = _patch_unit(new, "accessory_chassis_data",
                                          lambda b: _set_scalar(b, "max_steering_angle", wheel[model]))
                    if new != orig:
                        files[p] = new
    stock = physics_values(data.physics_text)
    changed = {k: v for k, v in cfg["shared"].items() if k in stock and abs(stock[k] - v) > 1e-9}
    if changed:
        files[PHYSICS_PATH] = patch_physics(data.physics_text, changed)
    if not files:
        return 0
    info = {**DEFAULT_INFO, **cfg.get("info", {})}
    user_desc = (info.get("description") or "").strip()
    desc = [info["name"], f"Version {info['version']}" + (f" - by {info['author']}" if info["author"] else ""), ""]
    if user_desc:
        desc += [user_desc, ""]
    desc_settings_start = len(desc)
    desc += ["Made with ATS Truck & Car Physics Tuner.", ""]
    for cls in ("truck", "car"):
        on = [f"  {k}: {v['value']}" for k, v in cfg[cls].items() if v["on"]]
        if on:
            desc += [f"{cls.title()} settings:"] + on + [""]
    for cls in ("truck", "car"):
        w = cfg.get("wheel", {}).get(cls, {})
        if w:
            desc += [f"{cls.title()} wheel angle per model:"] + [f"  {m}: {v} deg" for m, v in sorted(w.items())] + [""]
    if changed:
        desc += ["Shared physics.sii:"] + [f"  {k}: {v}" for k, v in changed.items()]
    if not info.get("summary", True):
        desc = desc[:desc_settings_start]
    icon = make_icon(info.get("icon"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.sii", manifest(info, icon is not None))
        if icon:
            z.writestr("mod_icon.jpg", icon)
        z.writestr("description.txt", "\n".join(desc))
        for p, t in sorted(files.items()):
            z.writestr(p, t)
    os.replace(tmp, out_path)
    return len(files)


# ------------------------------------------------- steering wheel rotation
# The in-cab steering wheel rotation ("Steering rotation" in Options > Controls)
# is the profile cvar g_steer_anim_range. The game has one value per profile, not
# per vehicle, so the app switches it for you when you change vehicle type.
STEER_CVAR = "g_steer_anim_range"


def profile_configs(docs_dir):
    """Every profile's config_local.cfg under Documents/American Truck Simulator."""
    out = []
    for sub in ("steam_profiles", "profiles"):
        out += glob.glob(os.path.join(docs_dir, sub, "*", "config_local.cfg"))
    return sorted(out)


def read_steer_range(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = re.search(r'^uset ' + STEER_CVAR + r' "(' + NUM + ')"', f.read(), re.M)
        return float(m.group(1)) if m else None
    except OSError:
        return None


def write_steer_range(path, degrees):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    line = f'uset {STEER_CVAR} "{fmt(degrees)}"'
    pat = re.compile(r'^uset ' + STEER_CVAR + r' ".*"$', re.M)
    if pat.search(text):
        text = pat.sub(line, text)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    if not os.path.exists(path + ".tuner_backup"):
        with open(path, encoding="utf-8", errors="replace") as f, \
                open(path + ".tuner_backup", "w", encoding="utf-8") as b:
            b.write(f.read())
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def game_running():
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq amtrucks.exe", "/NH"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return "amtrucks.exe" in out.lower()
    except OSError:
        return False
