"""Builds "ATS Physics Tuner.exe".

Usage: double-click build_exe.bat (or run: python build_exe.py)

Everything (virtual env, temp files, pip downloads) stays inside the .build
folder next to this script, so your main Python install is not touched.
Output:
    dist/ATS Physics Tuner/ATS Physics Tuner.exe   (keep with its _internal folder)
"""

import os
import shutil
import subprocess
import sys
import venv

SRC = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(SRC, ".build")
VENV = os.path.join(BUILD, "venv")
DIST = os.path.join(SRC, "dist")
APP = "ATS Physics Tuner"
PACKAGES = ["pyinstaller", "sv-ttk", "pillow"]  # sv-ttk: Windows 11 look for Tk; pillow: mod icons


def run(cmd, env):
    print(">", " ".join('"%s"' % c if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True, env=env)


ICON_SCRIPT = r'''
import sys
from PIL import Image, ImageDraw, ImageFont
out = sys.argv[1]
big = 256
im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((8, 8, big - 8, big - 8), radius=52, fill=(96, 205, 255, 255))
glyph = "\uec4a"  # speedometer, Segoe Fluent Icons / MDL2
for font in (r"C:\Windows\Fonts\SegoeIcons.ttf", r"C:\Windows\Fonts\segmdl2.ttf"):
    try:
        f = ImageFont.truetype(font, 150)
        break
    except OSError:
        f = None
if f:
    l, t, r, b = d.textbbox((0, 0), glyph, font=f)
    d.text(((big - (r - l)) / 2 - l, (big - (b - t)) / 2 - t), glyph, font=f, fill=(16, 16, 16, 255))
im.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
'''


def main():
    tmp = os.path.join(BUILD, "tmp")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, TEMP=tmp, TMP=tmp, PIP_NO_CACHE_DIR="1")

    py = os.path.join(VENV, "Scripts", "python.exe")
    if not os.path.isfile(py):
        print("Creating build environment...", flush=True)
        venv.create(VENV, with_pip=True)
        run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], env)
    run([py, "-m", "pip", "install", "-q"] + PACKAGES, env)

    icon = os.path.join(BUILD, "app.ico")
    run([py, "-c", ICON_SCRIPT, icon], env)

    shutil.rmtree(os.path.join(SRC, "__pycache__"), ignore_errors=True)
    sep = os.pathsep  # ';' on Windows
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
         "--name", APP,
         "--icon", icon,
         "--collect-data", "sv_ttk",
         "--add-data", icon + sep + ".",
         "--distpath", DIST,
         "--workpath", os.path.join(BUILD, "work"),
         "--specpath", BUILD,
         os.path.join(SRC, "ats_tuner_gui.py")], env)

    shutil.copy(os.path.join(SRC, "README.txt"), os.path.join(DIST, APP, "README.txt"))
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nDone:")
    print("  " + os.path.join(DIST, APP, APP + ".exe"))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print("\nBuild failed (%s)" % e)
        sys.exit(1)
