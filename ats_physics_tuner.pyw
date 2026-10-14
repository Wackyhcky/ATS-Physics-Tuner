# Double-click to start ATS Physics Tuner (no console window).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ats_tuner_gui  # noqa: E402

ats_tuner_gui.main()
