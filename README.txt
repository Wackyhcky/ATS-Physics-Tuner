ATS Physics Tuner
=================
Tune trucks and cars separately in American Truck Simulator, packed as a normal .scs mod.
Run "ATS Physics Tuner.exe" (keep it next to its _internal folder).

Pages
  Trucks          engine / gearbox / chassis of the trucks only   (def/vehicle/truck/...)
  Cars            the same for the drivable Ford cars only        (def/vehicle/car/...)
                  + Anti-flip (Mild / Strong / Max) against rollovers at speed
  Shared physics  def/vehicle/physics.sii - ATS has ONE file for every vehicle,
                  so these change trucks AND cars
  Steering        steering wheel rotation for trucks and cars, Auto-switch, MOZA wheelbase
  Mod info        name, author, version, description, icon for the Mod Manager
  Build           build the mod, presets, log
  Settings        ATS folder and mod folder

Switch a setting on to use it; settings that are off stay stock.
Build mod writes  Documents\American Truck Simulator\mod\zz_truck_car_physics_tuner.scs
In game: Mod Manager -> enable the mod, move it to the top. Rebuild + restart the game
(or reload the profile) after every change, and after game updates.

Auto-switch: watches game.log.txt for truck/car changes and, while ATS is in front, types
  g_steer_anim_range <deg>  into the console (~). Needs the console on (g_console 1).
  Don't type in chat at the moment you change vehicle. Keep the app open while you play.
MOZA: switches the wheelbase's rotation at the same time over USB. Close MOZA Pit House
  while you drive - it locks the port. FFB settings are stored on the base.

Notes
  * Engine torque changes real power; the hp text in the shop isn't updated.
  * Truck handling values are written as exact values into every selected truck - start small.
  * Single-player. In Convoy every player needs the same mod; TruckersMP ignores it.
  * Settings are saved in %APPDATA%\ATSPhysicsTuner\settings.json

Made by wackyhacky.
