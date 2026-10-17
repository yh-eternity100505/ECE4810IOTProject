"""Exercise the real HTTP distance endpoint without GPIO. Ctrl+C to stop."""
import time
from tracker import load_config
from publish_sensor_readings import publish_readings

if __name__ == '__main__':
    cfg = load_config()
    try:
        while True:
            for sensor in (1, 2, 3, 4, 5, 0):
                values = [cfg['baselines_cm'][str(i)] for i in range(1, 6)]
                if sensor:
                    values[sensor-1] = max(2, values[sensor-1] - cfg['drop_cm'][str(sensor)] - 5)
                print('Simulated distance frames; active sensor:', sensor)
                for _ in range(15):
                    result = publish_readings(*values)
                    print(result['zone'], result['status'])
                    time.sleep(.2)
    except KeyboardInterrupt:
        print('Stopped. Live data will expire automatically.')
