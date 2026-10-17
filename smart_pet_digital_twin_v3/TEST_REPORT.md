# Verification report — V3

Tested 16 October 2026 in a Linux container, Python 3.12 and headless Chromium
151 with software WebGL. This is not a Raspberry Pi execution or performance test.

## Inspected inputs

- Original `smart_pet_digital_twin_v2.zip` and all nine source files.
- Annotated floor plan `Untitled Notebook-2.jpeg`.
- Annotated prototype photo `Untitled Notebook-3.jpeg`.
- Prototype photos `IMG_5033.jpeg`, `IMG_5034.jpeg`, `IMG_5035.jpeg`.
- Loaded original V2 in Chromium and exercised S2 before comparing it with V3.
  Original CDN URLs were fulfilled with the same bundled library versions for
  that inspection. V3 itself loaded local assets, with external requests blocked.

## Observed passes

| Check | Result / scope |
|---|---|
| Python regressions | 10/10 passed with `python -m unittest -v test_tracker.py` |
| Mapping | S1/S2→A, S3/S4→B, S5→C; alternate backend grouping tested |
| One pet | Scene inspection returned exactly one named pet group and five sensor groups throughout switches |
| Fade | Sampled intermediate alpha values during real animation frames; position changes occurred while opacity was near zero; nominal 250 ms out + 250 ms in |
| Rapid changes | S1→S3→S5→S1 converged on A without another avatar |
| Clear | Browser button faded pet to hidden |
| Live path | Real five-distance HTTP POSTs replaced simulation and selected C |
| Lost live feed | Without further posts, polling expired readings and hid pet |
| Freshness | Missing frames cannot confirm detection; stale partial data cannot retain a previous active sensor forever |
| Ambiguity | Multiple active zones flagged; prior supported location retained, otherwise unknown |
| API handling | Invalid IDs, wrong body types, booleans, NaN, out-of-range values rejected; null readings accepted |
| Token handling | Unauthorized write rejected; matching-token write accepted (Flask test client) |
| Socket.IO | Browser connected; test client received initial state and update broadcasts |
| Mouse controls | Left-drag changed camera, wheel changed camera distance, right-drag changed orbit target |
| UI | Plan/perspective buttons, label/zone toggles, all five simulation buttons exercised |
| Resize | Canvas remained visible at 390×844; desktop inspected at 1440 px width |
| Runtime | No JavaScript page errors; zero external asset requests |
| Resource stability | Geometry and texture counts remained stable over repeated idle refreshes; removed V2's material cloning on every update |
| Tk bridge | Mocked publisher received all five values via daemon queue; submit returned within 50 ms |
| Existing publisher | `publish_readings()` made real HTTP requests to the local Flask server and confirmed B from S3 |

`browser_test.cjs` repeats the principal browser checks against a running server.
Browser screenshots were inspected for internal QA and are not bundled.
Tested direct Python packages: Flask 3.1.3, Flask-SocketIO 5.7.0, requests 2.34.2.

## Fixes and refinements

Retained V2's application architecture and HTTP contract. Added shared JSON
configuration, corrected plan orientation and flat-label direction, moved the
bed to S2, removed duplicate bed geometry, adjusted curved panels, modelled a
low S1 and four raised paired-stick mounts, added PCB/transducer detail,
removed label overlap over the pet, and bundled client dependencies.

Fixed stale active detections and missing-frame confirmation; added locking for
concurrent request threads; validated inputs; made simulation clear immediate
and live/simulation takeover deterministic. Simulation intentionally holds until
changed and does not display misleading stale badges.

## Requires the actual Raspberry Pi / prototype

- GPIO trigger/echo operation, the user's pin assignments and acquisition timing.
- Actual Tkinter GUI integration/layout, button opening on that desktop and
  response under real concurrent sensor acquisition. Only the queue logic and
  network boundary were tested here; the original GUI source was unavailable.
- Pi browser/GPU performance and frame rate, real LAN/firewall reliability.
- Empty-house calibration for all five channels and suitable detection drops.
- Physical sensor ID-to-wire correspondence, height, aim, interference/cross-talk,
  sensor noise and real pet detection reliability.
- Measured tray/partition dimensions, curved panel placement and whether the
  sketch's two right-side curves are present in the current physical prototype.

The geometry is a visual reconstruction in relative units, not dimensionally
verified CAD. Logical grouping is configurable and is not proof of sensing
coverage. The default A avatar position represents the upper region at the bed;
an S1 detection does not claim that the animal is precisely inside that bed.
