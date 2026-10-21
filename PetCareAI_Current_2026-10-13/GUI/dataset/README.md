# Activity datasets (no model training)

Run commands from the GUI folder using its existing Python environment.

## Ready-to-upload files

- `generated/baseline_train.csv`: 90 simulated baseline days.
- `generated/changed_routine_test.csv`: 30 subsequent simulated changed-routine days.
- `generated/feature_schema.json`: explicit model-input allowlist, metadata columns and exact routine assumptions.

Generated using seed 4810 and the saved GUI database configuration. Re-running with the same seed, configuration and assumptions produces identical CSVs. Simulated routines are illustrative assumptions, not actual observations or medical labels. Edit `routines.json` to change stay start hours, duration hours and timing jitter independently for baseline/test. Duration varies uniformly by +/-10%; minute-resolution samples are classified using the GUI's strict distance-below-threshold rule.

```powershell
.\.venv-local\Scripts\python.exe -m pet_monitoring.activity_dataset simulate --database data/pet_monitor.db --assumptions dataset/routines.json --seed 4810 --baseline-days 90 --test-days 30 --output dataset/generated
```

Without `--database`, the generator explicitly uses GUI defaults. Supplying an absent database fails rather than silently substituting a new database. The database is opened read-only and only hub_configuration is read. No credentials are exported.

## Features and missingness

Each area has occupancy seconds, observed visit starts, longest observed stay seconds, first/last activity hour and fraction of occupancy at night (22:00-06:00). Duplicate sensors in the same area are combined as a union. A visit starts on a confirmed absent-to-present transition; a stay already present at recording start or after a gap is left-censored and does not create a false visit. Longest stays are observed within the day and break at missing intervals. This uses the existing GUI occupancy state, rather than counting repeated distance readings or gesture classifications as visits. Sender ZONE_EVENT totals may differ at censored boundaries; occupancy features are derived consistently from threshold states in both sources.

IMPORTANT: The current system treats food and water inputs as container levels, not pet occupancy. Their activity features are blank, even if a container produces valid distance readings. Sensor 5 is an entrance gesture detector and is not an occupancy area. Do not interpret these as zero food/water consumption. The generated datasets contain usable sleep/toilet features; eating/drinking visits require a different detection capability.

Blank CSV cells mean missing (NaN in pandas). A daily area's features are blank when valid coverage is below 95%. With adequate coverage, occupancy/visit counts of zero mean observed absence; timing and night fraction remain blank when there was no activity. Totals are observed totals, not extrapolated to fill gaps. Coverage columns are metadata, not model inputs. First/last times are boundary times (last can be 24.0). Partially collected days are retained with low coverage and missing features.

## Record actual Pi activity through the existing GUI

Normal real-time collection:

```powershell
.\.venv-local\Scripts\python.exe main.py --record-activity dataset/live-normal-001.jsonl
```

Accelerated live collection (1 real second = 1 logical hour; one day = 24 seconds):

```powershell
.\.venv-local\Scripts\python.exe main.py --record-activity dataset/live-fast-001.jsonl --activity-clock-scale 3600 --collection-days 1
```

Use a new filename for each session. Connect the Pi as usual and select **Live Pi** in Sensor Hub. Collection starts when Live Pi mode has a matching configuration ACK. The day timer starts then, rather than during SSH startup. `--collection-days` closes the application automatically. Without it, collection continues until you close the GUI. The recorder never synthesizes live data when the Pi is offline.

On clean exit, the recorder automatically exports a CSV beside the JSONL file. Re-export a saved recording:

```powershell
.\.venv-local\Scripts\python.exe -m pet_monitoring.activity_dataset export dataset/live-fast-001.jsonl --output dataset/live-fast-001.csv
```

The JSONL preserves real UTC observation timestamps, logical interval start/end seconds, session metadata and observed/unknown area states. Normal sessions use the current local time of day; accelerated sessions start at logical midnight. Logical day boundaries split stays. A small trailing partial day may occur due to timer scheduling; its low coverage keeps its features missing.

No changes are made to receiver decoding, EDC/HMAC, replay checks, Pi sampling, real monotonic time, or the GUI's real five-second freshness timeout. Only recorder durations/day boundaries are multiplied. Accelerated data is marked `pi_accelerated`, not ordinary `pi_live`, and is useful for collection demonstrations, not evidence of a natural 24-hour pet routine. At acceleration 3600, a real 5-second stale allowance corresponds to 5 logical hours; it is deliberately not shortened in the sensor/security pipeline.

On disconnect, invalid readings, unacknowledged revisions or unknown areas, activity becomes missing. Configuration is frozen per recording: if mapping/threshold/revision changes, subsequent intervals become missing; start a new recording for the new configuration. Nothing is silently relabelled under a new area. Cleanly close the app to flush/export. For an interrupted file, retain complete JSONL lines and re-export; do not replace missing readings with zero.

## Load into Colab without metadata leakage (no training)

Upload both CSVs and feature_schema.json, then:

```python
import json
import pandas as pd

schema = json.load(open('feature_schema.json'))
train = pd.read_csv('baseline_train.csv')
test = pd.read_csv('changed_routine_test.csv')
columns = schema['model_inputs']
# Use only the explicit feature allowlist. Never include source, scenario, dates,
# seed, clock scale, coverage or configuration as model inputs.
X_train = train[columns].copy()
usable = X_train.columns[X_train.notna().any()].tolist()
X_train = X_train[usable]
X_test = test[usable].copy()
train_metadata = train[schema['metadata_columns']].copy()
test_metadata = test[schema['metadata_columns']].copy()
# Keep missing values; choose any later imputation using training data only.
```

This prepares data only. No model is trained, and simulated results do not establish real-world accuracy.
