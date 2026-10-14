@echo off
cd /d "%~dp0"
start "" pyw ats_physics_tuner.pyw || start "" pythonw ats_physics_tuner.pyw
