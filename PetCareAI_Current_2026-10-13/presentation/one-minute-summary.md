# One-minute report summary

Our project, PetCare AI, combines camera-free pet monitoring with entrance security. Five ultrasonic sensors measure food and water levels, estimate presence in sleeping and toilet areas, and capture entrance movements.

The Raspberry Pi applies configurable thresholds and uses a KNN model to classify entrance movements as pass-by, approach-and-stay, or an authentication gesture.

Sensor messages are encrypted using our educational EDC algorithm and authenticated with HMAC before the desktop accepts them. The desktop displays live status and saves timestamped history, while the phone interface supports remote viewing. Selected alerts and login codes are delivered through Telegram. Administrator access requires a password and OTP; a gesture alone cannot bypass it.

The design reuses inexpensive sensors across several functions. Our remaining evaluation focuses on real-world accuracy, response time, cost and sustainability.

## Presentation notes

Aim for approximately 60 seconds at a steady pace. Follow the slide in this order: sensors, Pi processing, protected transfer, desktop/history, phone and Telegram, access security, evaluation. This description reflects implemented architecture, not verified live performance. Do not claim all movements send alerts, a mechanical door unlock is complete, the phone LAN link uses HTTPS, or simulated model metrics are measured results.
