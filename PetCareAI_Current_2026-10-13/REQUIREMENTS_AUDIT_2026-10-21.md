# ECE4810 requirements audit — 21 October 2026

## Scope and verdict

Reviewed all three pages of `C:\Y4S1\ECE4810\Project.pdf`, the current `PetCareAI_Current_2026-10-13` desktop/Pi/phone code, bundled model and datasets, integration documents, and available test evidence. The separate `smart_pet_digital_twin_v3` documentation was considered as supplementary evidence only; its previous Linux/browser checks do not establish current Pi hardware performance or integration.

**Verdict: substantial implementation exists, but full compliance is not yet demonstrated.** The largest risks are Activity 2 measured ML evidence, Activity 1/2 notification coverage, Activity 1 access behavior across operating modes, and the academic report/demonstration/reflections. No grade can be inferred: the supplied three-page brief refers to a marking rubric but does not include one.

This is an audit, not an instruction to implement everything mentioned in the brief. No application behavior was changed for this review. Existing changes for dataset collection were left intact. The PDF reader and extracted text were local audit aids. No administrator credentials were retrieved, no real Telegram messages were sent, and no remote deployment was performed.

Status definitions:
- Implemented: code exists and appropriate software evidence supports it; physical operation may still need demonstration.
- Partial: part exists but a concrete requirement or mode is incomplete.
- Unverified: cannot confirm without the physical system, external evidence, or your submitted document.
- Not found: absent from accessible reviewed files; not a claim that you have never prepared it elsewhere.

## Activity 1 — IoT sensor/network system (PDF page 1)

| Requirement | Status | Existing evidence | Remaining work/evidence |
|---|---|---|---|
| Detect nearby presence with an ultrasonic sensor | Implemented; hardware unverified today | `raspberry_pi/predict_movement_test.py`: bounded echo acquisition, threshold detection, Sensor 5 gesture trigger. `GUI/pet_monitoring/hub_controller.py`: saved mapping, sleep/toilet thresholds, revision ACK gating and freshness handling. | Demonstrate physical distances, sensor positioning, stable detection, missing echoes and interference. Pi was offline during this audit. |
| 1(a): notify the owner through Telegram when presence/object is detected | Partial | `GUI/pet_monitoring/movement_controller.py`: APPROACH_STAY raises an alert with class/confidence; `services/telegram_service.py` supports real HTTPS delivery. Pi phone hub has entrance alerts too. | PASS_BY is recorded but has no corresponding movement Telegram branch; a raw threshold trigger is not itself a guaranteed Telegram notification. AUTH_GESTURE sends a message in the phone hub when its notification flag is enabled, but not through the desktop movement controller. Agree/document a filtered notification policy and demonstrate that it satisfies the brief; literal every-presence coverage is not currently implemented. Test actual delivery with the deployed bot/chat. |
| 1(b): known username -> generated password/code sent to Telegram -> verify -> access | Implemented in principle; end-to-end access is partial/unverified | Desktop single admin password verification automatically requests a Telegram OTP; successful factor verification creates a session. Pi phone hub `request_otp()` checks the username in users, generates a six-digit code, sets expiry/attempt limit, sends Telegram, and `verify_otp()` records ACCESS_GRANTED. | Desktop adds a stored password before the OTP (a security enhancement, not evidence the flow is missing). Phone entrance flow requires a gesture first. In GUI-managed phone mode, `Hub.door()` returns without forwarding AUTH_OK/OTP_OK to Pi outputs. Verify the intended protected resource: desktop GUI access works in code; a physical entrance feedback/access action is not established in this mode. Phone OTP still displays a demo code when Telegram is unconfigured; that is not a valid demonstration of Telegram authentication. |
| 1(c): minimal sensors with high functionality/data interpretation | Implemented design; justification/evidence incomplete | Four configurable pet sensors plus one entrance sensor; the entrance channel classifies three movement classes and supports presence/feedback behavior. Food/water calibration and sleep/toilet occupancy interpretation reuse the same hardware. | Explain why five sensors are necessary, compare against a simpler design, give measured coverage/limitations and cost/power trade-offs. The brief does not impose a specific maximum sensor count. |

## Activity 2 — Machine learning and sustainability (PDF pages 1–2)

| Requirement | Status | Existing evidence | Remaining work/evidence |
|---|---|---|---|
| ML classifies movement of a person/object/hand | Implemented code and bundled model; accuracy unverified | `raspberry_pi/movement_knn_model.joblib`; actual service in `predict_movement_test.py` loads the existing bundle once, extracts 14 features from a 60-sample window, and calls predict/predict_proba for PASS_BY, APPROACH_STAY, AUTH_GESTURE. | Supply the training method/data provenance and a reproducible held-out evaluation. Existing trained gesture ML can meet this requirement; a new pet-routine model is not inherently required by the brief. |
| 2(a): accurately display movement and send that information through Telegram | Partial | GUI validates the three class names, displays predictions/confidence, and records them. Suspicious-approach Telegram text includes prediction/confidence. | Accuracy must be measured; Telegram coverage is incomplete across the three classes/operating modes. Demonstrate required messages on a real device. |
| 2(b): fast, accurate inference; thoroughly evaluate computational efficiency with evidence/metrics | Not adequately evidenced | Nonblocking gesture collection, model loaded once, executor-based prediction, finite sample deadlines and GUI responsiveness tests are useful implementation features. | No real held-out confusion matrix, class-wise precision/recall/F1, measured comparison supporting model choice, or Pi inference latency/CPU/memory dataset was found. Separate roughly six-second acquisition time from feature extraction, prediction time, encryption/network time and end-to-end notification delay. Report sample count, test conditions and distributions (e.g. mean/p95), not just one observation. Training notebook/code was not found in the reviewed files. |
| 2(c): environmental/societal sustainability and long-term implementation | Report/evaluation requirement; not found as coursework evidence | Camera-free design and local sensing offer topics to discuss. | Write an evidence-based discussion of energy, privacy, maintenance, affordability/accessibility, false alerts, lifespan and disposal, tied to this prototype and its limitations. A GUI page or new code is not required for this narrative. |

**Do not use the MockMLService KNN/SVM/Decision Tree percentages or mock latency as measured results.** Its source explicitly identifies them as demonstration outputs. Prediction confidence is not test-set accuracy. The new baseline/changed-routine CSVs are marked simulated and do not prove real pet anomaly detection. The live desktop pet-behavior adapter still uses UnavailableMLService outside mock mode; the genuine KNN inference is the separate Pi gesture path.

## Activity 3 — Security (PDF page 2)

| Requirement | Status | Existing evidence | Remaining work/evidence |
|---|---|---|---|
| 3(a): username/password login before use | Implemented for desktop; lifecycle wording needs explanation | Single admin, Argon2id password hash, native OS credential store, Telegram OTP, expiring sessions, failure lockout, recovery codes and password reset. No movement-based administrator bypass. | Normal startup deliberately starts receiver/Pi background monitoring while the login screen is displayed; it gates GUI/admin use rather than all background acquisition. Explain continuous protected monitoring, or obtain agreement if the assessor expects no acquisition before login. Do not claim every process waits for authentication. |
| 3(a): encryption/decryption where necessary | Implemented for relevant paths; deployment hardening remains | Live Pi EDC + HMAC-SHA256; verification before decrypt, schema/nonce/timestamp checks and replay rejection. Normal GUI sender includes --edc. Saved laptop EDC keys use DPAPI. SSH startup/tunnelling and HTTPS Telegram requests provide additional protected paths. Security Inspector makes the cipher steps inspectable. | The phone LAN URL is HTTP and session cookie lacks Secure; its login/OTP web traffic does not inherit the desktop SSH tunnel. The phone still creates a default admin password and supports an OTP demo fallback. Harden or explicitly delimit these paths. EDC is educational, not validated production cryptography; replay memory resets on application restart. Plaintext compatibility mode exists if EDC is not configured. Validate the actual deployed configuration. |
| 3(b): health/safety, whole-life cost, net zero, resources/cultural/societal/environmental factors | Report/evaluation requirement; not found as coursework evidence | Some observational-not-diagnostic messaging exists. | Include applicable discussion and measured/estimated evidence in the report. Do not claim net-zero operation without a defensible lifecycle/carbon assessment. |

The brief does NOT explicitly mandate TLS algorithm negotiation, AES/ChaCha20, multiple administrator accounts, public registration, or a physical lock actuator. It asks for a simple reliable security algorithm and protected premise/system access. Do not remove secure single-admin login just to mimic the example literally. Identify whether your demonstration grants application access or controls a physical entrance; lights/buzzer are access feedback, not proof of a mechanical door unlock.

## Activity 4 — Additional features (PDF page 2)

Implemented candidates for a justified additional-features section:
- Phone web interface and desktop monitoring interface.
- Configurable sensor assignments and threshold acknowledgement/revision handling.
- Food/water calibration, percentage levels and optional volume estimates.
- Sleep/toilet occupancy duration and zone visit history.
- Persistent alerts/history, timestamps, filters and phone-settings change summaries.
- Lost-reading/offline handling rather than presenting stale data as current.
- Stronger administrator authentication, recovery/reset and a security inspection view.
- SSH-managed startup, a desktop phone-app tunnel, and duplicate-process locks.
- Reproducible simulated daily feature datasets, a live recorder and an accelerated dataset clock, explicitly distinguished from real-time sensing/security.
- A digital-twin viewer is available, but validate the actual integrated browser assets and live operation before claiming that separately tested V3 is deployed.

Status: implemented features exist; practical justification and live demonstration still needed. Cosmetic changes such as the mascot are not a substitute for technically justified additional functionality.

Remaining practical issues: phone sensor edits are rejected in GUI-managed mode; phone change history uses GUI observation snapshots and cannot recover every offline intermediate edit; fixed Pi IP/user/paths reduce portability; launcher prevents concurrent GPIO ownership but does not automatically resolve an existing sensor service. These are practical/extra-feature limitations, not separately named mandatory rubric items.

## Activity 5 — Report (PDF page 2)

No final coursework report was found among the accessible project files. Setup guides, READMEs, SECURITY.md and VERIFICATION.md are supporting documentation, not automatically the required report. I cannot assess a report kept elsewhere or inaccessible `laptop_data` content.

Required checklist:
- [ ] 1.0 Introduction: problem statement and objectives, linking pet monitoring/entrance security to the brief.
- [ ] 2.0 Methodology: architecture, wiring/network, flowcharts for the activities, ML method/features/data split, security process, and a section for justified additional features.
- [ ] 3.0 Results and Analysis: real sensor/Telegram/access evidence, model evaluation, efficiency metrics, security rejection tests, and limitations. Clearly separate measured, mocked and simulated outputs.
- [ ] 4.0 Feasibility Evaluation with Cost Benefit Analysis: itemised prototype cost, running/maintenance/replacement costs, quantified or justified benefits, practical feasibility and alternatives.
- [ ] Long-term sustainability, public health/safety, whole-life cost, net-zero/carbon, resource/cultural/societal/environmental considerations where applicable.
- [ ] 5.0 Conclusion; Future Work; References.
- [ ] At most 20 report pages; brief specifies approximately 12,000 words excluding appendices/references/diagrams. Do not exceed the page cap to chase the approximate word count; seek lecturer clarification if needed.
- [ ] Put mark-bearing results/analysis in the report body: the brief says appendices do not receive marks and content beyond 20 pages is not marked.

These items need writing and supporting evidence; they do not all require code changes.

## Activity 6 — Demonstration (PDF pages 2–3)

Status: unverified/not assessable from source files.
- [ ] Group demonstration/pitch in week 12.
- [ ] Five-minute allocation: approximately one-minute report summary and four-minute working prototype demonstration.
- [ ] Cover prototype, methodology, results, feasibility and cost–benefit; prepare for Q&A.
- [ ] Demonstrate physical presence -> movement result -> actual Telegram notification, known-user OTP -> granted system access, incorrect/expired OTP rejection, encrypted/tampered packet handling, and one useful extra feature.
- [ ] Rehearse with the exact deployed Pi script/environment and a working network/bot. A local mock test or screenshot is not equivalent to this demonstration.

## Activity 7 — Individual reflections (PDF page 3)

Status: not found in reviewed files.
- [ ] One page per group member.
- [ ] State individual contribution, team-discussion communication and commitment.
- [ ] Combine reflections with the report in one PDF, as instructed.

The supplied first page also includes group number and member names/IDs; check these in the submission. The brief's listed submission date is 21 October 2026. No separate detailed scoring rubric was supplied, so this audit does not estimate a mark.

## Verification performed in this audit

- Main GUI suite: `GUI/.venv-local/Scripts/python.exe -m unittest discover -s tests -q` from GUI: **163 tests ran, OK, one skipped**. The suite includes authentication, reset, Telegram mocked delivery, EDC transport/replay, hub behavior, phone startup, GUI and dataset checks. A Windows DPAPI round-trip can skip when the execution profile is unavailable; a skipped platform check is not a live device verification.
- Separate Pi regression suite: `GUI/.venv-local/Scripts/python.exe -m unittest discover -s raspberry_pi -p test_*.py -q` from the package root: **6 tests ran, failed with 6 errors**. Several tests import the current `predict_movement.py`, which is now an EDC test harness and does not export the old feature/ZoneTracker/GestureRecorder service API. The integration test still uses removed demo administrator authentication; a Windows temporary-DB cleanup error also occurred. These are real test-suite maintenance issues; they do not by themselves prove the current `predict_movement_test.py` runtime detector is broken.
- `GUI/.venv-local/Scripts/python.exe raspberry_pi/predict_movement.py`: standalone EDC security checks completed successfully. The printed keys/tags in this harness are temporary test material, not device credentials.
- No physical Pi, deployed remote file comparison, real Telegram delivery, current model accuracy benchmark, electrical safety measurement, energy measurement, or final report inspection was possible/available in this audit.
- Existing START_HERE.md and VERIFICATION.md contain obsolete demo-login/page/test information. MOVEMENT_INTEGRATION.md still mentions removed movement navigation; some EDC integration notes also describe historical ports/modes. Reconcile documentation with the final build before submission.

## Recommended completion order

1. Choose and demonstrate the exact end-to-end access path (desktop system access vs physical entrance) and remove any Telegram/demo-mode ambiguity from the assessed run.
2. Complete required movement notification coverage or justify an assessor-approved filtering policy; capture real phone delivery evidence.
3. Collect measured gesture-ML accuracy and Pi timing/resource results. Preserve training/test provenance. Do not present illustrative tables or the new simulated routine CSVs as real validation.
4. Repair stale Pi regression imports/old-auth assumptions and update deployment instructions; rerun with the actual service script.
5. Finish/report the cost–benefit, feasibility and sustainability sections, flowcharts, references and limitations.
6. Add each member's reflection, check the 20-page report limit, prepare the five-minute demonstration/Q&A and validate the final combined PDF.

A new pet-routine model, more UI redesign, multi-account registration, production cloud deployment and mandatory TLS cipher negotiation are not explicitly required by this brief. Prioritise the actual activities above.
