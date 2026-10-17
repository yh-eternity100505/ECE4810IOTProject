import os
import math
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from tracker import Tracker

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins=None, async_mode='threading')
tracker = Tracker()
TOKEN = os.getenv('PET_API_TOKEN', '')

@app.get('/')
def index():
    return render_template('index.html')

@app.get('/api/config')
def configuration():
    return jsonify({key: tracker.config[key] for key in ('zones', 'sensors', 'groups')})

@app.get('/api/state')
def state():
    return jsonify(tracker.snapshot())

@app.post('/api/sensors')
def sensor_data():
    if TOKEN and request.headers.get('X-Pet-Token') != TOKEN:
        return jsonify(error='unauthorized'), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict): return jsonify(error='JSON object required'), 400
    raw = data.get('distances', {})
    if not isinstance(raw, dict) or not raw: return jsonify(error='distances object required'), 400
    try:
        if any(str(k) not in ('1', '2', '3', '4', '5') for k in raw):
            raise ValueError('sensor id')
        readings = {}
        for k, v in raw.items():
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 500:
                raise ValueError('distance')
            readings[int(k)] = float(v)
    except (TypeError, ValueError):
        return jsonify(error='sensor IDs must be 1–5; distances must be finite numbers 0–500 cm or null'), 400
    snapshot = tracker.update(readings)
    socketio.emit('pet_update', snapshot)
    return jsonify(snapshot)

@app.post('/api/simulate')
def simulate():
    if TOKEN and request.headers.get('X-Pet-Token') != TOKEN:
        return jsonify(error='unauthorized'), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('sensor')) is not int or data['sensor'] not in range(6):
        return jsonify(error='sensor must be integer 0–5'), 400
    snapshot = tracker.simulate(data['sensor'])
    socketio.emit('pet_update', snapshot)
    return jsonify(snapshot)

@socketio.on('connect')
def on_connect():
    socketio.emit('pet_update', tracker.snapshot(), to=request.sid)

if __name__ == '__main__':
    host = os.getenv('PET_HOST', '127.0.0.1')
    socketio.run(app, host=host, port=int(os.getenv('PET_PORT', '5000')), allow_unsafe_werkzeug=True)
