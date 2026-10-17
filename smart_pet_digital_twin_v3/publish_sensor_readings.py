"""Call from the worker that already receives five HC-SR04 measurements."""
import os
import requests

def publish_readings(d1, d2, d3, d4, d5):
    server = os.getenv('PET_SERVER_URL', 'http://127.0.0.1:5000')
    headers = {}
    if os.getenv('PET_API_TOKEN'):
        headers['X-Pet-Token'] = os.getenv('PET_API_TOKEN')
    response = requests.post(server.rstrip('/') + '/api/sensors',
        json={'distances': {'1': d1, '2': d2, '3': d3, '4': d4, '5': d5}},
        headers=headers, timeout=1.5)
    response.raise_for_status()
    return response.json()
