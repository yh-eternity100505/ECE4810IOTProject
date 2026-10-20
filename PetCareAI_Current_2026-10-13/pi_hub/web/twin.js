// 3D prototype view for the phone app. Geometry from smart_pet_digital_twin_v3/static/scene.js;
// live state comes from the hub's /api/status instead of the twin's Flask server.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ---------------------------------------------------------------------------
// Sensor positions come from the hub (phone app: Settings -> Sensor locations). Sketch coordinates:
// x 0 (left) to 10 (right), z 15 (back/top) to 0 (front/bottom); facing in degrees, 0 = toward the back.
// DEFAULT_LAYOUT is only used until the first status arrives. The pet is drawn just in front of each sensor.
// ---------------------------------------------------------------------------
const DEFAULT_LAYOUT = {
  1: {x: 10.15, z: 1.15, facing: -90, mount: 'raised'},
  2: {x: 8.5, z: 15.12, facing: 180, mount: 'raised'},
  3: {x: 10.15, z: 4.75, facing: -90, mount: 'raised'},
  4: {x: 0.0, z: 0.2, facing: 37, mount: 'raised'},
  5: {x: 1.15, z: 14.0, facing: 180, mount: 'floor'},
};
const SPOT_DISTANCE = 1.9;
const SPOTS = {};
const FOOD_BOWL = {x: 1.3, z: 12.9};
const WATER_BOTTLE = {x: 8.14, z: 3.75};
const ROLE = {eat: ['Food', '#f4c36a'], sleep: ['Bed', '#43cbb8'], water: ['Water', '#72a9ff'], toilet: ['Litter', '#c9a0ff'], entrance: ['Entrance', '#ff9f6b']};

let built = false, running = false, frameId = 0, observer = null, host = null;
let renderer, scene, camera, orbit, root, pet, bottleWater, bowlFood, foodBowl, petMaterials, kit;
let layoutKey = '';
const sensorGroups = {};
const markers = {}, rings = {};
let petOpacity = 0, petTarget = null, petAlpha = 0, transition = null, entranceActive = false;
const clock = new THREE.Clock();

const mat = (color, opts = {}) => new THREE.MeshStandardMaterial({color, roughness: .87, ...opts});

function build() {
  scene = new THREE.Scene(); scene.background = new THREE.Color('#2b2147');
  camera = new THREE.PerspectiveCamera(43, 1, .1, 140);
  renderer = new THREE.WebGLRenderer({antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  orbit = new OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = true; orbit.maxPolarAngle = Math.PI / 2.08; orbit.minDistance = 11; orbit.maxDistance = 55;
  scene.add(new THREE.HemisphereLight(0xe3f3ff, 0x637085, 2));
  const light = new THREE.DirectionalLight(0xffe6bf, 2.4); light.position.set(1, 20, 10); light.castShadow = true;
  light.shadow.mapSize.set(2048, 2048);
  Object.assign(light.shadow.camera, {left: -19, right: 19, top: 23, bottom: -23});
  scene.add(light);

  // Everything is built in sketch coordinates inside `root`, mirrored so the plan reads like the drawing.
  // Labels stay outside the mirror so their text is not reversed.
  root = new THREE.Group(); root.scale.z = -1; root.position.z = 15; scene.add(root);

  const tray = mat('#121418');
  const corr = document.createElement('canvas'); corr.width = corr.height = 64;
  const cc = corr.getContext('2d'); cc.fillStyle = '#b88e58'; cc.fillRect(0, 0, 64, 64);
  for (let x = 0; x < 64; x += 8) { cc.fillStyle = '#ad804b'; cc.fillRect(x, 0, 1, 64); }
  const corrugation = new THREE.CanvasTexture(corr); corrugation.wrapS = corrugation.wrapT = THREE.RepeatWrapping;
  corrugation.repeat.set(3, 1); corrugation.colorSpace = THREE.SRGBColorSpace;
  const cardboard = mat('#e5c597', {map: corrugation, side: THREE.DoubleSide});
  const edge = mat('#76502c'), chrome = mat('#bbc8cc', {metalness: .55, roughness: .32});

  const add = (mesh, parent = root) => { mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh; };
  const box = (x, y, z, w, h, d, m) => { const o = add(new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m)); o.position.set(x, y, z); return o; };
  const cylinder = (x, y, z, rt, rb, h, m, seg = 32) => { const o = add(new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), m)); o.position.set(x, y, z); return o; };
  const wall = (x1, z1, x2, z2, h = 1.45, width = .10, m = cardboard) => {
    const o = box((x1 + x2) / 2, h / 2, (z1 + z2) / 2, Math.hypot(x2 - x1, z2 - z1), h, width, m);
    o.rotation.y = -Math.atan2(z2 - z1, x2 - x1); return o;
  };
  const curve = (cx, cz, rx, rz, start, end, h = 1.43) => {
    const n = 29;
    for (let i = 0; i < n; i++) {
      const a = start + (end - start) * i / n, b = start + (end - start) * (i + 1) / n;
      wall(cx + rx * Math.cos(a), cz + rz * Math.sin(a), cx + rx * Math.cos(b), cz + rz * Math.sin(b), h, .10);
    }
  };

  // Tray, partitions, curved enclosures and bed: as in the digital twin.
  box(5, -.23, 7.5, 10.4, .46, 15.4, tray);
  wall(0, 0, 10, 0, 1.1, .17, tray); wall(10, 0, 10, 15, 1.1, .17, tray);
  wall(10, 15, 0, 15, 1.1, .17, tray); wall(0, 15, 0, 0, 1.1, .17, tray);
  wall(2.6, 15, 2.6, 12.0); wall(2.62, 10.35, 2.62, 5.85);
  wall(0, 5.85, 3.38, 5.85); wall(4.65, 5.85, 5.67, 5.85); wall(6.56, 5.85, 10, 5.85);
  wall(5.25, 0, 5.25, 5.85);
  curve(0, 0, 3.05, 2.95, 0, Math.PI / 2, 1.05);
  curve(10, 5.84, 2.05, 2.48, Math.PI, Math.PI * 1.5, .9);
  curve(10, 0, 2.40, 2.55, Math.PI / 2, Math.PI, .9);
  const bedX = 8.35, bedZ = 13.25, bedRadius = 1.38;
  curve(bedX, bedZ, bedRadius, bedRadius, -.2, Math.PI * 1.24, 1.08);
  curve(bedX, bedZ, bedRadius, bedRadius, Math.PI * 1.60, Math.PI * 2 - .2, 1.08);
  cylinder(bedX, .055, bedZ, 1.33, 1.33, .10, mat('#c9a574'), 64);
  box(7.23, .045, 11.74, 1.05, .08, 1.50, cardboard).rotation.y = -.65;
  box(3.8, .09, 10.7, 1.05, .17, .75, mat('#17191d')); box(3.8, .19, 10.7, .64, .04, .39, mat('#3c4147'));

  // Water bottle with a live water column; a simple food bowl with a live food layer.
  box(WATER_BOTTLE.x, .17, WATER_BOTTLE.z, 1.45, .25, 1.45, edge);
  cylinder(WATER_BOTTLE.x, .95, WATER_BOTTLE.z, .43, .40, 1.45, mat('#b8e7f4', {transparent: true, opacity: .30, roughness: .15}));
  cylinder(WATER_BOTTLE.x, 1.75, WATER_BOTTLE.z, .22, .22, .16, mat('#569acb'));
  bottleWater = cylinder(WATER_BOTTLE.x, .30, WATER_BOTTLE.z, .37, .37, 1, mat('#3d8fd6', {transparent: true, opacity: .8, roughness: .2}));
  bottleWater.geometry.translate(0, .5, 0);
  // The food bowl is a group so it can sit in front of whichever sensor is assigned to food.
  foodBowl = new THREE.Group(); foodBowl.position.set(FOOD_BOWL.x, 0, FOOD_BOWL.z); root.add(foodBowl);
  add(new THREE.Mesh(new THREE.CylinderGeometry(.62, .44, .28, 32), mat('#d9534f', {roughness: .5})), foodBowl).position.y = .14;
  bowlFood = add(new THREE.Mesh(new THREE.CylinderGeometry(.54, .54, 1, 32), mat('#9b6a3c')), foodBowl);
  bowlFood.position.y = .08;
  bowlFood.geometry.translate(0, .5, 0);

  kit = {add, cardboard, chrome};

  // One ring per pet spot, coloured by what that sensor is assigned to; moved by placeSensors().
  for (const n of Object.keys(DEFAULT_LAYOUT)) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(.73, .82, 48),
      new THREE.MeshBasicMaterial({color: '#ffffff', side: THREE.DoubleSide, transparent: true, opacity: .55, depthWrite: false}));
    ring.rotation.x = -Math.PI / 2; ring.position.y = .04; root.add(ring); rings[n] = ring;
  }
  placeSensors(DEFAULT_LAYOUT);

  // Exactly one pet; all parts share one opacity.
  pet = new THREE.Group(); root.add(pet);
  const fur = mat('#e9ad55', {transparent: true, opacity: 0}), dark = mat('#754b32', {transparent: true, opacity: 0});
  const pink = mat('#e88b91', {transparent: true, opacity: 0}), eye = mat('#251d1b', {transparent: true, opacity: 0});
  petMaterials = [fur, dark, pink, eye];
  const ball = (x, y, z, sx, sy, sz, m) => { const o = new THREE.Mesh(new THREE.SphereGeometry(1, 22, 16), m); o.scale.set(sx, sy, sz); o.position.set(x, y, z); pet.add(o); };
  ball(0, .40, 0, .51, .37, .34, fur); ball(0, .84, -.25, .38, .34, .32, fur);
  for (const sign of [-1, 1]) {
    const ear = new THREE.Mesh(new THREE.ConeGeometry(.18, .36, 3), fur); ear.position.set(sign * .23, 1.21, -.34); ear.rotation.y = Math.PI / 4; pet.add(ear);
    ball(sign * .15, .89, -.53, .045, .055, .035, eye); ball(sign * .31, .20, .13, .16, .18, .15, fur);
  }
  ball(0, .72, -.57, .074, .056, .04, pink); ball(0, .40, .40, .21, .15, .14, dark);
  pet.scale.setScalar(.83); pet.visible = false;

  view('perspective');
  built = true;
}

/** (Re)build the five sensor stands at their configured positions and move each pet spot in front of them. */
function placeSensors(layout) {
  const {add, cardboard, chrome} = kit;
  for (const [n, place] of Object.entries(layout)) {
    if (sensorGroups[n]) { root.remove(sensorGroups[n]); scene.remove(markers[n].label); }
    const yaw = place.facing * Math.PI / 180, floor = place.mount === 'floor';
    const s = {x: place.x, z: place.z, yaw, height: floor ? .55 : n === '5' ? 3.0 : 3.3, tilt: floor ? 0 : .65};
    const g = new THREE.Group(); g.position.set(s.x, 0, s.z); g.rotation.y = s.yaw; root.add(g); sensorGroups[n] = g;
    const part = (geometry, material, x, y, z, parent = g) => { const o = add(new THREE.Mesh(geometry, material), parent); o.position.set(x, y, z); return o; };
    if (!floor) {
      for (const x of [-.58, .58]) part(new THREE.CylinderGeometry(.035, .035, s.height, 8), mat('#cbb286'), x, s.height / 2, 0);
      part(new THREE.BoxGeometry(1.62, .94, .07), cardboard, 0, s.height, -.11);
    } else part(new THREE.BoxGeometry(1.60, .90, .08), cardboard, 0, .55, -.12);
    const head = new THREE.Group(); head.position.y = s.height; head.rotation.x = s.tilt; g.add(head);
    part(new THREE.BoxGeometry(.88, .40, .075), mat('#2169a2'), 0, 0, 0, head);
    part(new THREE.BoxGeometry(1.40, .72, .10), mat('#e6e2cc'), 0, 0, -.09, head);
    for (const x of [-.26, .26]) {
      part(new THREE.CylinderGeometry(.17, .17, .19, 24), chrome, x, 0, .13, head).rotation.x = Math.PI / 2;
      part(new THREE.CircleGeometry(.125, 24), mat('#303a41'), x, 0, .231, head);
    }
    const led = mat('#66807a', {emissive: '#000000'});
    part(new THREE.SphereGeometry(.07, 12, 10), led, .6, .24, .01, head);
    markers[n] = {led, label: textLabel('S' + n, s.x, s.z, '#ffffff', s.height + .72), text: ''};
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    SPOTS[n] = {x: clamp(s.x + Math.sin(yaw) * SPOT_DISTANCE, .7, 9.3), z: clamp(s.z + Math.cos(yaw) * SPOT_DISTANCE, .7, 14.3)};
    rings[n].position.x = SPOTS[n].x; rings[n].position.z = SPOTS[n].z;
  }
  petTarget = null;  // re-place the pet at its (possibly moved) spot on the next update
}

function textLabel(text, x, z, color, y) {
  const canvas = document.createElement('canvas'); canvas.width = 420; canvas.height = 96;
  const texture = new THREE.CanvasTexture(canvas);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2.9, .66), new THREE.MeshBasicMaterial({map: texture, transparent: true, depthWrite: false, side: THREE.DoubleSide}));
  mesh.position.set(x, y, 15 - z); mesh.rotation.x = -Math.PI / 2; scene.add(mesh);
  mesh.userData.draw = (value, colour = color) => {
    const ctx = canvas.getContext('2d'); ctx.clearRect(0, 0, 420, 96);
    ctx.fillStyle = '#111e2de0'; ctx.fillRect(0, 0, 420, 96); ctx.fillStyle = colour;
    let size = 36; ctx.font = `bold ${size}px system-ui`;
    while (ctx.measureText(value).width > 395 && size > 16) { size--; ctx.font = `bold ${size}px system-ui`; }
    ctx.textAlign = 'center'; ctx.fillText(value, 210, 62); texture.needsUpdate = true;
  };
  mesh.userData.draw(text);
  return mesh;
}

export function view(kind) {
  orbit.target.set(5, 0, 7.5);
  const top = kind === 'top';
  camera.up.set(...(top ? [0, 0, -1] : [0, 1, 0]));
  camera.position.set(...(top ? [5, 31, 7.5] : [16, 19, 26]));
  orbit.update();
}

export function setLabels(visible) {
  scene.traverse((o) => { if (o.userData.draw) o.visible = visible; });
}

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.fov = w < h ? Math.min(66, 43 / camera.aspect * .67) : 43;  // portrait phones: widen just enough to fit the tray
  camera.updateProjectionMatrix(); renderer.setSize(w, h, false);
}

function frame() {
  if (!running) return;
  frameId = requestAnimationFrame(frame);
  const t = clock.getElapsedTime();
  if (transition) {
    const k = Math.min(1, (performance.now() - transition.start) / 250), s = k * k * (3 - 2 * k);
    setOpacity(transition.from + (transition.to - transition.from) * s);
    if (k >= 1) {
      if (transition.phase === 'out' && transition.spot) {
        pet.position.set(transition.spot.x, .03, transition.spot.z);
        transition = {phase: 'in', start: performance.now(), from: 0, to: transition.alpha};
      } else transition = null;
    }
  }
  if (pet.visible) pet.rotation.y = Math.sin(t * 1.2) * .09;
  for (const [n, ring] of Object.entries(rings)) {
    const pulsing = ring.userData.active || (n === '5' && entranceActive);
    ring.material.opacity = pulsing ? .45 + .4 * Math.abs(Math.sin(t * 3)) : .35;
  }
  orbit.update(); renderer.render(scene, camera);
}

function setOpacity(v) {
  petOpacity = Math.max(0, Math.min(1, v));
  for (const m of petMaterials) { m.opacity = petOpacity; m.depthWrite = petOpacity > .99; }
  pet.visible = petOpacity > .005;
}

function placePet(spot, alpha) {
  const key = spot ? `${spot.x},${spot.z}` : null;
  if (key === petTarget && alpha === petAlpha) return;
  if (key === petTarget && spot) { transition = {phase: 'in', start: performance.now(), from: petOpacity, to: alpha}; petAlpha = alpha; return; }
  petTarget = key; petAlpha = alpha;
  transition = {phase: 'out', start: performance.now(), from: petOpacity, to: 0, spot, alpha};
}

/** Apply one /api/status snapshot. Returns a short description of what the view shows. */
export function update(status) {
  if (!built) return '';
  if (status.layout && JSON.stringify(status.layout) !== layoutKey) {
    layoutKey = JSON.stringify(status.layout);
    placeSensors(status.layout);
  }
  const zones = Object.fromEntries(status.zones.map((z) => [z.sensor_id, z]));
  for (const n of ['1', '2', '3', '4']) {
    const z = zones[n], [name, colour] = ROLE[z.mode];
    const text = `S${n} · ${name}` + ((z.mode === 'eat' || z.mode === 'water') && z.level_percent != null ? ` ${z.level_percent}%` : '');
    if (markers[n].text !== text) { markers[n].label.userData.draw(text, colour); markers[n].text = text; }
    rings[n].material.color.set(colour);
    rings[n].userData.active = z.presence;
    markers[n].led.color.set(!z.online ? '#7a2f2f' : z.presence ? '#43ffb8' : '#2f9c8a');
    markers[n].led.emissive.set(z.presence ? '#1f8f63' : '#000000');
  }
  const g = status.gesture;
  entranceActive = g.online && g.distance != null && g.distance < 20;
  if (markers[5].text !== 'S5 · Entrance') { markers[5].label.userData.draw('S5 · Entrance', ROLE.entrance[1]); markers[5].text = 'S5 · Entrance'; }
  rings[5].material.color.set(ROLE.entrance[1]);
  markers[5].led.color.set(!g.online ? '#7a2f2f' : entranceActive ? '#ffc04d' : '#2f9c8a');
  markers[5].led.emissive.set(entranceActive ? '#a06a10' : '#000000');

  const food = status.zones.find((z) => z.mode === 'eat');
  if (food && SPOTS[food.sensor_id]) foodBowl.position.set(SPOTS[food.sensor_id].x, 0, SPOTS[food.sensor_id].z);
  // Bowl levels: water column in the bottle, food layer in the bowl (empty when unknown).
  const level = (mode) => { const z = status.zones.find((x) => x.mode === mode && x.level_percent != null); return z ? z.level_percent / 100 : 0; };
  bottleWater.scale.y = Math.max(.001, 1.25 * level('water'));
  bowlFood.scale.y = Math.max(.001, .16 * level('eat'));

  // Pet: occupied bed/litter now; otherwise faded at the last place it was seen; otherwise hidden.
  const here = status.zones.find((z) => z.presence && (z.mode === 'sleep' || z.mode === 'toilet'));
  if (here) { placePet(SPOTS[here.sensor_id], 1); return 'live'; }
  const last = status.last_visit && status.zones.find((z) => z.mode === status.last_visit.mode);
  if (last) { placePet(SPOTS[last.sensor_id], .35); return 'last'; }
  placePet(null, 0);
  return 'none';
}

export function mount(container) {
  if (!built) build();
  host = container; container.appendChild(renderer.domElement);
  observer = new ResizeObserver(resize); observer.observe(container); resize();
  if (!running) { running = true; frame(); }
}

export function unmount() {
  running = false; cancelAnimationFrame(frameId);
  if (observer) observer.disconnect();
  observer = null;
}
