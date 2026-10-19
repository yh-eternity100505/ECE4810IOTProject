# PetCare AI Pi hub

Runs everything on the Raspberry Pi, so the phone works without the laptop:

```
predict_movement script (unchanged) ──TCP localhost:5005──► pi_hub.py ──http :8080──► phone web app
                                                              ├─ SQLite history (data/petcare_hub.db)
                                                              └─ Telegram alerts and access codes
```

The hub plays the laptop GUI's part in the existing protocol (`SET_CONFIG` → `CONFIG_ACK`, readings, visits, gestures).
The sensor script is not modified; it is started with `--gui-host 127.0.0.1`.
Python standard library only: nothing to `pip install`.

## Start from the desktop GUI

Launching `GUI/main.py` now starts the laptop receiver, deploys a GUI-managed copy
of the phone hub to `~/.cache/petcare/gui-hub`, and launches the existing encrypted
Pi sender after the phone hub is ready. Use **Open phone app** in the desktop
sidebar, or open `http://PI_IP:8080` on the phone. Select **Live Pi** in the desktop
Sensor Hub to view the hardware readings there.

Only one sensor process runs. The laptop verifies EDC + HMAC once and forwards
accepted readings to the hub through a localhost SSH tunnel. No cipher keys are
copied to the hub. Phone accounts, history, alerts and other settings still use
`~/Desktop/pi_hub/data`.

If `petcare-hub` is already active, the launcher reuses it without sudo, restarts,
or service changes. Keep sensor assignments and thresholds the same in both apps;
the bridge translates configuration revisions and forwards zone readings only
after the Pi has acknowledged the matching setup. Gesture readings also pass
through. A mismatch appears in the desktop status bar and phone-app tooltip.

Otherwise, the launcher starts a GUI-managed hub whose sensor configuration
follows the desktop automatically. Closing the GUI closes its hub/tunnel and
sender; a pre-existing system hub stays running. Its previously installed source,
settings and boot configuration are left intact. If `petcare-sensors` is active,
startup reports that it already owns GPIO instead of starting a duplicate sender.
A hub started manually must be closed first if it occupies port 8080.

Keep the GUI open to supply readings in this mode. The standalone instructions
below still run the phone hub independently of the laptop.

## Files

| File | Purpose |
|---|---|
| `pi_hub.py` | Hub service: sensor link, history, alert rules, Telegram, access codes, HTTP API |
| `web/` | Phone app (one page, no internet needed) |
| `web/twin.js`, `web/vendor/` | 3D tab: the prototype model from `smart_pet_digital_twin_v3`, driven by live hub data (three.js 0.167.1, MIT licence) |
| `petcare-hub.service`, `petcare-sensors.service` | Optional: start both on boot |
| `data/` | Created on first run: database, `hub_settings.json` (owner-only, holds the Telegram token), `hub.log` |

## First run

1. Copy this folder to the Pi, e.g. `scp -r pi_hub jiating@PI_IP:~/Desktop/`
2. Terminal 1 on the Pi:
   ```bash
   cd ~/Desktop/pi_hub
   python3 pi_hub.py
   ```
3. Terminal 2 on the Pi (your working script and env; nothing else changes):
   ```bash
   cd ~/Desktop
   source env/bin/activate            # however you normally activate it
   python predict_movement_test.py --gui-host 127.0.0.1
   ```
   Terminal 1 should log `Sensor script connected` and `Pi applied configuration revision 1`.
4. On the phone (same Wi-Fi), open `http://PI_IP:8080` and sign in with **admin / pet123**.
   The app asks you to change that password (Settings → Account).
5. iPhone: Share → *Add to Home Screen*. Android Chrome: menu → *Add to Home screen*.

Then, in the app's Settings:
- **Food & water calibration**: with the bowl full tap *Use now as full*, empty it, tap *Use now as empty*, Save.
- **Telegram**: paste the bot token and chat ID, tick *Send Telegram messages*, Save, *Send test*.
- **Pet**: name and usual daily sleep / litter visits (used for daily checks).
- **Sensors**: same assignments as the desktop Sensor Hub; the Pi confirms each change.

Reset a forgotten password on the Pi: stop the hub, run `python3 pi_hub.py --set-password admin`, then start the hub again.

## Start on boot (optional)

Edit the paths in `petcare-sensors.service` to match your env and script, then:

```bash
sudo cp ~/Desktop/pi_hub/petcare-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now petcare-hub petcare-sensors
journalctl -u petcare-hub -f          # watch the logs
```

## What it does

| Area | Behaviour |
|---|---|
| Food / water | Level % from calibration. Alert when below 30% for 60 s; clears once above 40%. |
| Bed / litter box | Current presence, time in bed today, litter visits today. |
| Daily check | At midnight, if ≥ 20 h were observed: a wellness score from sleep and litter visits against your usual values, plus an alert when either is more than 30% off. |
| Entrance | Walked past (logged), waited at the door (alert), unlock gesture (opens a 2-minute window to request a code). |
| Access code | Sent by Telegram. If Telegram is not set up, the app shows a demo code instead. No physical lock is connected. |
| Sensor health | Alert when the sensor script is disconnected for 60 s, or a zone sensor has no valid echo for 2 minutes. |

## Away from home

The app uses plain HTTP on the local network. To reach it from anywhere, install
[Tailscale](https://tailscale.com/download) on the Pi and the phone and open `http://<pi-tailscale-name>:8080`.
Tailscale encrypts the connection. Do not port-forward 8080 on a router.

## Laptop developer dashboard at the same time

The hub can send a live copy of everything it receives to the desktop GUI, while it keeps recording.

1. Laptop: start the GUI, sign in, **Sensor Hub → Live Pi → Connect Pi / Gesture access → Start receiver** (port 5005).
2. Phone app: **Settings → Developer dashboard**, enter the laptop IPv4 addresses (`ipconfig`), separated by commas
   (up to 5, e.g. `10.156.144.168, 10.192.104.4`), Save. Every laptop running its receiver gets live data at the same time.
   Or on the Pi: `python3 pi_hub.py --mirror "10.156.144.168,10.192.104.4"` then `sudo systemctl restart petcare-hub`.
3. The status line in that section should read *dashboard receiving live data*.

Sensors 1-4 must be assigned the same way in the dashboard and in the phone app. If they differ, the dashboard
still gets entrance/gesture data but no zone data, and the status line says so. The hub stays in charge of the
sensor setup; changing assignments in the dashboard does not change the Pi.
The dashboard only sees live data from when it connects; history stays in the hub (phone app).
Clear the list to turn the copy off. A laptop that is off or unreachable is simply retried every few seconds.

Alternative: stop the sensor service and run the script with `--gui-host LAPTOP_IP` so only the laptop receives data.

### Encryption of the dashboard copy (EDC + HMAC)

If the Pi has `~/.config/petcare/edc.env` (the same `EDC_KEY` / `EDC_HMAC_KEY_HEX` file the encrypted sender
uses), the hub encrypts everything it sends to the laptops with the EDC cipher and signs it with HMAC-SHA256,
exactly like the encrypted sender. It verifies the dashboard's encrypted setup messages the same way.
Laptops that are not set up for EDC are refused rather than sent plaintext. Another key file: `--edc-keys PATH`.

On each laptop, once: `GUI\.venv-local\Scripts\python.exe GUI\main.py --setup-edc` and enter the same two keys.
The phone app's Settings → Developer dashboard shows the encryption state and each laptop's status.

| Link | Protection |
|---|---|
| Sensor script → hub | Plaintext, but only inside the Pi (127.0.0.1) |
| Hub → laptop dashboards | EDC + HMAC (when the key file exists) |
| Hub → phone | Tailscale (WireGuard) when used remotely; plain HTTP on the local Wi-Fi |

EDC is an educational cipher; the HMAC is what stops forged or altered packets.

### Desktop dashboard sync (hub → desktop)

The hub is the master copy of the pet and the hardware setup. Every connected desktop dashboard receives a
`HUB_SYNC` message (EDC + HMAC encrypted like the rest) when it connects and whenever any of these change:
the monitored pet's name, animal, breed, sex, birthday (→ age), weight and usual sleep/litter visits; each sensor's
role, threshold, food/water calibration, wiring pins and **location** (Settings → Sensor locations: note, position on
the tray, facing, floor or raised). The desktop adopts them within a second, so its sensor assignments always match
the Pi. Local desktop edits to these are replaced while it is connected; laptop-only options (container litres) are kept.
The 3D tab draws each sensor at its saved location.

### Pets and care reminders

Settings → **My pets** keeps up to 12 profiles (avatar, breed, birthday → age, sex, weight, notes, usual sleep and
litter visits). The sensors cannot tell pets apart, so live tracking, the wellness score and routine alerts follow the
one **monitored** pet. Settings → **Care reminders** holds vaccinations, vet visits, grooming, medicine and so on, with
optional repeats. Due and overdue reminders appear on Home; from 09:00 each one raises one alert per day (and a
Telegram message if enabled). **Done** moves a repeating reminder to its next date and removes a one-off one.
Existing installs are upgraded automatically: the previous single pet becomes the first, monitored pet.

### 3D tab

The phone app's **3D** tab shows the prototype (tray, partitions, bed, bottle, five sensors) with one pet:
solid at the bed or litter box while that station is occupied, faded at the last place it was seen, hidden otherwise.
The water bottle and food bowl fill with the calibrated levels; sensor lights are green (pet detected),
teal (reading OK) or red (no reading); labels follow the current sensor assignments.
Sensor mount positions and pet spots are estimates: edit `SENSORS` and `SPOTS` at the top of `web/twin.js`
(x 0-10 left to right, z 15 back to 0 front), then refresh the page.

### Entrance lights and buzzer

The hub drives the sensor script's door feedback: an unlock gesture sends `AUTH_OK` (blinking green, chime),
a code verified in the phone app sends `OTP_OK` (solid green, success tune), and a failed or expired code sends `RESET`.

## Limits

Presence sensors estimate where the pet is; they do not confirm eating, drinking or sleep. The wellness score is a
behaviour indicator, not a veterinary diagnosis. A pet's head in a bowl reads as a fuller bowl for a moment.
