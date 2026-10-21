# Flowchart aligned to the supplied one-minute script

Slide: script-flowchart.svg or script-flowchart.png.

Presentation order:
1. Problem: owners lack timely information while away.
2. Top and three branches: five ultrasonic sensors; distinguish levels, presence and entrance KNN.
3. Middle: EDC/HMAC protects sensor transport; desktop validates and stores timestamped history.
4. Dashed side branch: password plus Telegram OTP gates administrator access, independently of background sensing.
5. Bottom branches: remote phone viewing and selected Telegram alerts help owners respond.
6. Closing line: camera-free monitoring, movement recognition and cybersecurity in one low-cost design.

Accuracy notes: food and water channels measure container levels, not eating/drinking visits. Sleep/toilet presence is a proximity estimate, not clinical proof of sleep or toileting. ML classifies entrance gestures; it is not a trained pet-routine anomaly detector. Notifications depend on configured alert paths and successful delivery. The phone LAN web interface currently uses HTTP; do not claim all connections are encrypted. Cost advantage requires a bill of materials/cost-benefit analysis. The chart summarises processing distributed across Pi and GUI; food/water calibration is performed in the GUI/hub, while gesture KNN runs on the Pi. Sensors 1?4 can be reassigned, so branch roles are configurable rather than permanently fixed sensor IDs.
