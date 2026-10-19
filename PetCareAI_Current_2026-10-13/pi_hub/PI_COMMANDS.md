# PetCare AI: Pi command cheat sheet

Pi user `jiating` · project files in `~/Desktop` · phone app `http://PI_IP:8080`

| Thing | Where |
|---|---|
| Sensor script | `~/Desktop/predict_movement_test.py` (+ `movement_knn_model.joblib`) |
| Python env | `~/Desktop/env` |
| Hub | `~/Desktop/pi_hub/pi_hub.py` |
| Hub data | `~/Desktop/pi_hub/data/` (database, settings, `hub.log`) |
| Services | `petcare-hub` (hub + phone app), `petcare-sensors` (sensor script) |

---

## 1. Connect to the Pi (from the laptop)

```bash
ssh jiating@PI_IP                 # or jiating@raspberrypi.local
hostname -I                       # on the Pi: show its IP address
exit                              # leave SSH (services keep running)
```
VS Code: `Ctrl+Shift+P` → **Remote-SSH: Connect to Host** → `jiating@PI_IP`.

## 2. Is everything running?

```bash
systemctl is-active petcare-hub petcare-sensors      # expect: active, active
systemctl status petcare-hub petcare-sensors         # details (press q to exit)
```

## 3. Watch the logs

```bash
journalctl -u petcare-sensors -f                     # sensor script output, live (Ctrl+C to stop watching)
journalctl -u petcare-hub -f                         # hub output, live
journalctl -u petcare-hub -u petcare-sensors -f      # both together
journalctl -u petcare-hub -n 50 --no-pager           # last 50 lines
journalctl -u petcare-sensors -b                     # since last boot
```
Healthy hub log contains: `Sensor script connected from 127.0.0.1` → `Pi applied configuration revision N`.

## 4. Start / stop / restart

```bash
sudo systemctl restart petcare-hub petcare-sensors   # after updating code
sudo systemctl stop petcare-hub petcare-sensors      # stop now (restarts at next boot)
sudo systemctl start petcare-hub petcare-sensors
sudo systemctl disable --now petcare-hub petcare-sensors   # stop and turn off auto-start
sudo systemctl enable --now petcare-hub petcare-sensors    # turn auto-start back on
```

## 5. Run the sensor script by hand (see output in the terminal)

Stop the service first: two copies fight over the GPIO pins.
```bash
sudo systemctl stop petcare-sensors
cd ~/Desktop && source env/bin/activate
python predict_movement_test.py --gui-host 127.0.0.1     # Ctrl+C to stop
sudo systemctl start petcare-sensors                     # when finished
```

## 6. Laptop developer dashboard

**Alongside the hub (normal):**
1. Laptop (PowerShell):
   ```powershell
   cd "C:\Desktop\ECE4810 IoT\Project\PetCareAI_Current_2026-10-13\GUI"
   .\.venv-local\Scripts\python.exe main.py
   ```
   Sign in → **Sensor Hub → Live Pi**. The receiver starts on port 5005 by itself.
2. Make sure this laptop's IP is in the hub's dashboard list (below). Its line should turn **Live**.
3. The hub sends the pet details and sensor setup to the dashboard automatically (sync), so assignments always match.

The list stays saved: next time just open the dashboard. Laptop IPs change when the Wi-Fi changes, so check `ipconfig`.

### Dashboard IP list (which laptops the Pi sends to)

**Easiest:** phone app → **Settings → Developer dashboard** → edit the comma-separated list → Save (applies at once, no restart).

**On the Pi.** The hub keeps the list in memory, so stop it first, change the list, then start it again.

```bash
cd ~/Desktop/pi_hub
SETTINGS=data/hub_settings.json

# See the current list
python3 -c "import json; print(json.load(open('$SETTINGS'))['mirror']['hosts'])"

# See what the hub is doing with each laptop right now
journalctl -u petcare-hub --no-pager | grep "Dashboard mirror" | tail -10
```

Add one IP (keeps the others; change `NEW_IP`):
```bash
sudo systemctl stop petcare-hub
NEW_IP=10.156.150.22
python3 pi_hub.py --mirror "$(python3 -c "import json; h=json.load(open('$SETTINGS'))['mirror']['hosts']; print(','.join(h + ['$NEW_IP']))")"
sudo systemctl start petcare-hub
```

Remove one IP (keeps the others; change `OLD_IP`):
```bash
sudo systemctl stop petcare-hub
OLD_IP=10.156.152.229
python3 pi_hub.py --mirror "$(python3 -c "import json; h=json.load(open('$SETTINGS'))['mirror']['hosts']; print(','.join(x for x in h if x != '$OLD_IP'))")"
sudo systemctl start petcare-hub
```

Replace the whole list, or turn sending off:
```bash
sudo systemctl stop petcare-hub
python3 pi_hub.py --mirror "10.156.144.168,10.156.152.229"   # exactly these laptops (up to 5)
python3 pi_hub.py --mirror ""                                 # nobody
sudo systemctl start petcare-hub
```

Notes:
- `--mirror` prints the saved list and exits; it does not start a second hub.
- A different port for one laptop: `10.156.150.22:5006`. Tailscale addresses (`100.x.x.x`) work too and never change.
- Each laptop must run its receiver on port 5005 and have the EDC keys (`main.py --setup-edc`), or it shows **Refused**.

**Encryption (EDC + HMAC):** the hub encrypts the dashboard copy when `~/.config/petcare/edc.env` exists on the Pi.
Each laptop needs the same keys once: `.\.venv-local\Scripts\python.exe main.py --setup-edc` (from `GUI`).
```bash
journalctl -u petcare-hub | grep "Laptop dashboard copy"    # "EDC + HMAC encrypted" or "UNENCRYPTED"
chmod 600 ~/.config/petcare/edc.env                         # keys readable only by you
```
A laptop without keys shows **Refused · not encrypted** in the phone app (retried every 30 s).

**Laptop only (hub gets nothing meanwhile):**
```bash
sudo systemctl stop petcare-sensors
cd ~/Desktop && source env/bin/activate
python predict_movement_test.py --gui-host LAPTOP_IP     # laptop: GUI → Sensor Hub → Live Pi → Start receiver
# done:
sudo systemctl start petcare-sensors
```
Laptop IP: `ipconfig` in PowerShell (Wi-Fi IPv4).

## 7. Update files from the laptop (PowerShell)

```powershell
cd "C:\Desktop\ECE4810 IoT\Project\PetCareAI_Current_2026-10-13"
scp -r pi_hub jiating@PI_IP:~/Desktop/                  # whole hub folder (keeps data/)
scp pi_hub\pi_hub.py jiating@PI_IP:~/Desktop/pi_hub/     # one file
```
Then on the Pi: `sudo systemctl restart petcare-hub`.
If you edit a `.service` file: `sudo cp ~/Desktop/pi_hub/petcare-*.service /etc/systemd/system/ && sudo systemctl daemon-reload`.

## 8. Accounts and data

```bash
cd ~/Desktop/pi_hub
sudo systemctl stop petcare-hub              # stop first, or the running hub overwrites the change
python3 pi_hub.py --set-password admin       # reset/create a password
sudo systemctl start petcare-hub
cp -r data ~/petcare_backup_$(date +%F)      # back up history and settings
```
Fresh start (deletes all history, settings and Telegram details):
```bash
sudo systemctl stop petcare-hub && rm -rf ~/Desktop/pi_hub/data && sudo systemctl start petcare-hub
```

## 9. Power, Wi-Fi, time

```bash
sudo shutdown now             # always do this before unplugging
sudo reboot
sudo nmtui                    # add/change Wi-Fi (Pi only rejoins saved networks)
nmcli device wifi list        # visible networks
date                          # check the clock (daily totals depend on it)
```

## 10. Check processes and ports

```bash
ps aux | grep -E "pi_hub|predict_movement" | grep -v grep   # TTY "?" = service, "pts/0" = started by hand
ss -ltnp | grep -E ":8080|:5005"                            # who is using the hub ports
kill PID                                                    # stop a process started by hand
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Hub log: `Address already in use` | A hand-started copy is still running. Section 10: find it, `kill PID`, then `sudo systemctl restart petcare-hub` |
| Sensor log: `GPIO not allocated` / `GPIO busy` | Two sensor scripts running, or the `initial=GPIO.LOW` fix is missing. Stop extras, restart service |
| Sensor log: `Connection unavailable: timed out` | Hub not running (`systemctl is-active petcare-hub`) or wrong `--gui-host` |
| Phone can't open the app | Same Wi-Fi? Correct IP (`hostname -I`)? `systemctl is-active petcare-hub`. Campus Wi-Fi may block device-to-device: use a phone hotspot or Tailscale |
| App says "Sensors offline" | `systemctl status petcare-sensors`, then `journalctl -u petcare-sensors -n 50` |
| App "Connecting…" for long | Hub waiting for `CONFIG_ACK`: restart both services |
| A zone shows "No reading" | Check that sensor's wiring/aim (pins in `raspberry_pi/PI_SETUP.md`) |
| Food/water shows "Set up" | Calibrate in app → Settings → Food & water calibration |
| No Telegram messages | App → Settings → Telegram: token, chat ID, *Send Telegram messages* ticked → *Send test* |
| Dashboard shows nothing | Receiver started on the laptop? Its IP in the list in app Settings → Developer dashboard (IPs change: check `ipconfig`)? Read the status line there. "Assignments differ" → set Sensors 1-4 the same in both |
| Dashboard "Refused · not encrypted" | On that laptop: `main.py --setup-edc` with the Pi's keys, restart the GUI |
| Dashboard "Refused · failed verification" | Keys differ between Pi and laptop, or clocks differ by > 2 min (`date` on the Pi) |
| Forgot app password | `sudo systemctl stop petcare-hub`, `python3 ~/Desktop/pi_hub/pi_hub.py --set-password admin`, `sudo systemctl start petcare-hub` |
