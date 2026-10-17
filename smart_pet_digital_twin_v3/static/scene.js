import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const config = await fetch('/api/config').then(r=>{if(!r.ok)throw new Error('Configuration failed');return r.json();});
const mount = document.querySelector('#scene');
const scene = new THREE.Scene(); scene.background = new THREE.Color('#152333');
const camera = new THREE.PerspectiveCamera(43, 1, .1, 120);
camera.position.set(16, 19, 26);
const renderer = new THREE.WebGLRenderer({antialias:true}); renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled=true; renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.outputColorSpace=THREE.SRGBColorSpace; mount.appendChild(renderer.domElement);
const orbit = new OrbitControls(camera, renderer.domElement); orbit.target.set(5,0,7.5); orbit.enableDamping=true; orbit.maxPolarAngle=Math.PI/2.08; orbit.minDistance=11; orbit.maxDistance=49; orbit.update();
scene.add(new THREE.HemisphereLight(0xe3f3ff,0x637085,2));
const light=new THREE.DirectionalLight(0xffe6bf,2.4); light.position.set(1,20,10); light.castShadow=true; light.shadow.mapSize.set(2048,2048); light.shadow.camera.left=-19; light.shadow.camera.right=19; light.shadow.camera.top=23; light.shadow.camera.bottom=-23; scene.add(light);
const mat=(color, opts={})=>new THREE.MeshStandardMaterial({color,roughness:.87,...opts});
const tray=mat('#121418');
// Procedural corrugation: local and deterministic; no external texture dependency.
const corr=document.createElement('canvas');corr.width=64;corr.height=64;
const cc=corr.getContext('2d');cc.fillStyle='#b88e58';cc.fillRect(0,0,64,64);
for(let x=0;x<64;x+=8){cc.fillStyle='#ad804b';cc.fillRect(x,0,1,64);}
const corrugation=new THREE.CanvasTexture(corr);corrugation.wrapS=corrugation.wrapT=THREE.RepeatWrapping;corrugation.repeat.set(3,1);corrugation.colorSpace=THREE.SRGBColorSpace;
const cardboard=mat('#e5c597',{map:corrugation,side:THREE.DoubleSide}); const edge=mat('#76502c');
const chrome=mat('#bbc8cc',{metalness:.55,roughness:.32});
function box(x,y,z,w,h,d,material,cast=true){const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),material);m.position.set(x,y,z);m.castShadow=cast;m.receiveShadow=true;scene.add(m);return m;}
function cylinder(x,y,z,rt,rb,h,material,segments=32){const m=new THREE.Mesh(new THREE.CylinderGeometry(rt,rb,h,segments),material);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;scene.add(m);return m;}
function wall(x1,z1,x2,z2,h=1.45,width=.10,material=cardboard){const dx=x2-x1,dz=z2-z1;const m=box((x1+x2)/2,h/2,(z1+z2)/2,Math.hypot(dx,dz),h,width,material);m.rotation.y=-Math.atan2(dz,dx);return m;}
function curve(cx,cz,rx,rz,start,end,h=1.43,material=cardboard){const n=29;for(let i=0;i<n;i++){const a=start+(end-start)*i/n,b=start+(end-start)*(i+1)/n;wall(cx+rx*Math.cos(a),cz+rz*Math.sin(a),cx+rx*Math.cos(b),cz+rz*Math.sin(b),h,.10,material);}}
function textLabel(text,x,z,color='#e5f1ff',y=1.9){const c=document.createElement('canvas');c.width=420;c.height=96;const ctx=c.getContext('2d');ctx.fillStyle='#111e2de0';ctx.fillRect(0,0,420,96);ctx.fillStyle=color;let fontSize=33;ctx.font=`bold ${fontSize}px system-ui`;while(ctx.measureText(text).width>395&&fontSize>16){fontSize--;ctx.font=`bold ${fontSize}px system-ui`;}ctx.textAlign='center';ctx.fillText(text,210,61);const tex=new THREE.CanvasTexture(c);const m=new THREE.Mesh(new THREE.PlaneGeometry(2.35,.54),new THREE.MeshBasicMaterial({map:tex,transparent:true,depthWrite:false,side:THREE.DoubleSide}));m.position.set(x,y,z);m.rotation.x=-Math.PI/2;scene.add(m);m.userData.isLabel=true;return m;}
// Reference plan: top/back = Z 15; bottom/front = Z 0; left = X 0.
box(5,-.23,7.5,10.4,.46,15.4,tray);
wall(0,0,10,0,1.1,.17,tray);wall(10,0,10,15,1.1,.17,tray);
wall(10,15,0,15,1.1,.17,tray);wall(0,15,0,0,1.1,.17,tray);
// Partitions traced approximately from the annotated top-down sketch.
wall(2.6,15,2.6,12.0); wall(2.62,10.35,2.62,5.85);
wall(0,5.85,3.38,5.85);wall(4.65,5.85,5.67,5.85);wall(6.56,5.85,10,5.85);
wall(5.25,0,5.25,5.85);
// Sketch's curved lower corner enclosure and two right-side wings.
curve(0,0,3.05,2.95,0,Math.PI/2,1.05);
curve(10,5.84,2.05,2.48,Math.PI,Math.PI*1.5,.9);
curve(10,0,2.40,2.55,Math.PI/2,Math.PI,.9);
// S2 bed: one nearly circular corrugated enclosure with southwest entrance.
const bedX=8.35,bedZ=13.25,bedRadius=1.38;
curve(bedX,bedZ,bedRadius,bedRadius,-.2,Math.PI*1.24,1.08);
curve(bedX,bedZ,bedRadius,bedRadius,Math.PI*1.60,Math.PI*2-.2,1.08);
cylinder(bedX,.055,bedZ,1.33,1.33,.10,mat('#c9a574'),64);
box(7.23,.045,11.74,1.05,.08,1.50,cardboard).rotation.y=-.65;
// Water station: translucent simplified bottle on a cardboard platform.
box(8.14,.17,3.75,1.45,.25,1.45,edge);cylinder(8.14,.95,3.75,.43,.40,1.45,mat('#b8e7f4',{transparent:true,opacity:.43,roughness:.15}));cylinder(8.14,1.75,3.75,.22,.22,.16,mat('#569acb'));
// Simplified electronics console; minimize wiring clutter.
box(3.8,.09,10.7,1.05,.17,.75,mat('#17191d'));box(3.8,.19,10.7,.64,.04,.39,mat('#3c4147'));
const zones=config.zones;
const sensors=config.sensors;
const zoneGraphics=[];
for(const [id,v] of Object.entries(zones)){const ring=new THREE.Mesh(new THREE.RingGeometry(.73,.80,48),new THREE.MeshBasicMaterial({color:v.color,side:THREE.DoubleSide,transparent:true,opacity:.68,depthWrite:false}));ring.rotation.x=-Math.PI/2;ring.position.set(v.x,.035,v.z);scene.add(ring);zoneGraphics.push(ring);textLabel(`ZONE ${id} · ${v.name}`,v.x,v.z-.95,v.color,.12);}
const markers=[];
for(const s of sensors){
 const g=new THREE.Group();g.name='sensor-'+s.n;g.position.set(s.x,0,s.z);g.rotation.y=s.yaw;scene.add(g);
 const add=(geometry,material,x,y,z)=>{const m=new THREE.Mesh(geometry,material);m.position.set(x,y,z);m.castShadow=true;g.add(m);return m;};
 if(s.mount==='raised'){
   for(const x of [-.58,.58]) add(new THREE.CylinderGeometry(.035,.035,s.height,8),mat('#cbb286'),x,s.height/2,0);
   add(new THREE.BoxGeometry(1.62,.94,.07),cardboard,0,s.height,-.11);
 } else add(new THREE.BoxGeometry(1.60,.90,.08),cardboard,0,.55,-.12);
 const head=new THREE.Group();head.position.y=s.height;head.rotation.x=s.tilt;g.add(head);
 const pcb=new THREE.Mesh(new THREE.BoxGeometry(.88,.40,.075),mat('#2169a2'));head.add(pcb);
 const breadboard=new THREE.Mesh(new THREE.BoxGeometry(1.40,.72,.10),mat('#e6e2cc'));breadboard.position.z=-.09;head.add(breadboard);
 const ledMaterial=mat('#66807a');
 for(const x of [-.26,.26]){
   const trans=new THREE.Mesh(new THREE.CylinderGeometry(.17,.17,.19,24),chrome);trans.rotation.x=Math.PI/2;trans.position.set(x,0,.13);head.add(trans);
   const face=new THREE.Mesh(new THREE.CircleGeometry(.125,24),mat('#303a41'));face.position.set(x,0,.231);head.add(face);
 }
 const led=new THREE.Mesh(new THREE.SphereGeometry(.045,10,8),ledMaterial);led.position.set(.6,.24,.01);head.add(led);
 // Direction arrow is illustrative: physical beam angle requires measurement.
 const dir=new THREE.Vector3(0,-Math.sin(s.tilt),Math.cos(s.tilt));
 const arrow=new THREE.ArrowHelper(dir,new THREE.Vector3(0,s.height,.26),.85,0x64cab9,.20,.12);g.add(arrow);
 const lbl=textLabel('S'+s.n,s.x,s.z,'#ffffff',s.height+.72);
 markers.push({sensor:s,group:g,label:lbl,ledMaterial});
}
// Exactly one pet group in the scene. All its pieces share an opacity controller.
const pet=new THREE.Group();scene.add(pet);pet.name='single-pet';
const fur=mat('#e9ad55',{transparent:true,opacity:0,depthWrite:false});
const dark=mat('#754b32',{transparent:true,opacity:0,depthWrite:false});
const pink=mat('#e88b91',{transparent:true,opacity:0,depthWrite:false});
const eye=mat('#251d1b',{transparent:true,opacity:0,depthWrite:false});
const allPetMaterials=[fur,dark,pink,eye];
function petBall(x,y,z,sx,sy,sz,m){const o=new THREE.Mesh(new THREE.SphereGeometry(1,22,16),m);o.scale.set(sx,sy,sz);o.position.set(x,y,z);o.castShadow=false;pet.add(o);return o;}
petBall(0,.40,0,.51,.37,.34,fur);petBall(0,.84,-.25,.38,.34,.32,fur);
for(const sign of [-1,1]){const ear=new THREE.Mesh(new THREE.ConeGeometry(.18,.36,3),fur);ear.position.set(sign*.23,1.21,-.34);ear.rotation.y=Math.PI/4;pet.add(ear);petBall(sign*.15,.89,-.53,.045,.055,.035,eye);petBall(sign*.31,.20,.13,.16,.18,.15,fur);}
petBall(0,.72,-.57,.074,.056,.04,pink);
petBall(0,.40,.40,.21,.15,.14,dark);
pet.scale.setScalar(.83);pet.visible=false;
let petOpacity=0, targetZone=null, transition=null;
function setPetZone(zone){if(zone===targetZone)return;targetZone=zone;transition={phase:'out',start:performance.now(),from:petOpacity,to:0,zone};}
function applyOpacity(v){petOpacity=Math.max(0,Math.min(1,v));for(const m of allPetMaterials)m.opacity=petOpacity;pet.visible=petOpacity>.005;}
function setState(state){const current=state.zone;const status=state.status;
 document.body.dataset.mode=state.mode;
 document.querySelector('#petstate').textContent=current?`Zone ${current} · ${zones[current].name}`:'Location unknown';
 document.querySelector('#detail').textContent=status==='detected'?(state.mode==='simulation'?'Simulated stable detection':'Confirmed stable sensor detection'):status==='last_known'?'Holding last known position briefly':status==='ambiguous'?'Multiple regions active; position ambiguous':'No confirmed active pet location';
 if(status==='unknown' || (status==='ambiguous' && !current))setPetZone(null);else if(current)setPetZone(current);
 const list=document.querySelector('#sensorlist');list.innerHTML='';for(let i=1;i<=5;i++){const s=state.sensors?.[i]||{};const el=document.createElement('div');el.className='sensor'+(s.active?' active':'');el.innerHTML=`<strong>Sensor ${i}</strong><span>${s.distance_cm==null?'—':Number(s.distance_cm).toFixed(1)+' cm'} · ${s.stale?'Stale':s.active?'Active':'Idle'}</span>`;list.appendChild(el);const mk=markers.find(m=>m.sensor.n===i);mk.ledMaterial.color.set(s.active?'#43ffb8':'#66807a');}
}
const btns=document.querySelector('#buttons');for(let i=1;i<=5;i++){const b=document.createElement('button');b.textContent='Sensor '+i;b.onclick=()=>simulate(i);btns.append(b);}const clear=document.createElement('button');clear.textContent='Clear detections';clear.className='clear';clear.onclick=()=>simulate(0);btns.append(clear);
async function simulate(i){try{const r=await fetch('/api/simulate',{method:'POST',headers:{'Content-Type':'application/json',...(sessionStorage.petToken?{'X-Pet-Token':sessionStorage.petToken}:{})},body:JSON.stringify({sensor:i})});if(r.status===401){const token=prompt('Enter the PET_API_TOKEN configured on your server (stored for this tab session)');if(token){sessionStorage.petToken=token;return simulate(i);}}if(!r.ok)throw new Error(await r.text());setState(await r.json());}catch(e){alert('Simulation request failed: '+e.message);}}
document.querySelector('#showzones').addEventListener('change',e=>{zoneGraphics.forEach(g=>g.visible=e.target.checked);});
document.querySelector('#showlabels').addEventListener('change',e=>{scene.traverse(o=>{if(o.userData.isLabel)o.visible=e.target.checked;});});
if(typeof io==='function'){const socket=io();socket.on('connect',()=>{document.querySelector('#connection').textContent='● Live connection';});socket.on('disconnect',()=>{document.querySelector('#connection').textContent='● Disconnected';});socket.on('pet_update',setState);}else{document.querySelector('#connection').textContent='Socket.IO unavailable';}
fetch('/api/state').then(r=>r.json()).then(setState).catch(()=>{});
// Convert sketch coordinates (back = z15) into a readable plan view.
// Keep labels outside the reflected model so text remains upright.
const prototype=new THREE.Group();prototype.name='prototype';
for(const object of [...scene.children]){
 if(object.isLight)continue;
 if(object.userData.isLabel){object.position.z=15-object.position.z;continue;}
 prototype.add(object);
}
prototype.scale.z=-1;prototype.position.z=15;scene.add(prototype);
const clock=new THREE.Clock();
function view(top=false){orbit.target.set(5,0,7.5);camera.up.set(...(top?[0,0,-1]:[0,1,0]));camera.position.set(...(top?[5,28,7.5]:[16,19,26]));orbit.update();}
document.querySelector('#topview').onclick=()=>view(true);
document.querySelector('#homeview').onclick=()=>view(false);
const legend=document.querySelector('#legend');legend.replaceChildren();
for(const [zone,ids] of Object.entries(config.groups)){const label=document.createElement('span');label.textContent=`Zone ${zone} · ${ids.map(i=>'S'+i).join(' + ')}`;label.style.color=zones[zone].color;legend.append(label);}
// Polling also ages missing live frames even when no socket message arrives.
let polling=false;setInterval(async()=>{if(polling)return;polling=true;try{const r=await fetch('/api/state');if(r.ok)setState(await r.json());}catch(e){document.querySelector('#connection').textContent='● Server unavailable';}finally{polling=false;}},500);
// Read-only diagnostics used by the bundled browser checks.
window.petTwin={inspect:()=>({petCount:prototype.children.filter(o=>o.name==='single-pet').length,sensorCount:markers.length,petVisible:pet.visible,opacity:petOpacity,targetZone,position:pet.position.toArray(),camera:camera.position.toArray(),target:orbit.target.toArray(),drawCalls:renderer.info.render.calls,geometries:renderer.info.memory.geometries,textures:renderer.info.memory.textures})};

function frame(){requestAnimationFrame(frame);if(transition){const t=Math.min(1,(performance.now()-transition.start)/250);applyOpacity(transition.from+(transition.to-transition.from)*(t*t*(3-2*t)));if(t>=1){if(transition.phase==='out'){if(transition.zone){const v=zones[transition.zone];pet.position.set(v.x,.03,v.z);transition={phase:'in',start:performance.now(),from:0,to:1,zone:transition.zone};}else transition=null;}else transition=null;}}
 clock.getDelta();if(pet.visible)pet.rotation.y=Math.sin(clock.elapsedTime*1.2)*.09;
 orbit.update();renderer.render(scene,camera);}
function resize(){const w=mount.clientWidth,h=mount.clientHeight;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h,false);}new ResizeObserver(resize).observe(mount);resize();frame();
