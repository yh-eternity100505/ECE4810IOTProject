// Optional developer check: npm install --no-save playwright; npx playwright install chromium
// Run while python server.py is running: node browser_test.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async()=>{
 const args=['--no-sandbox','--enable-unsafe-swiftshader'];
 if(process.env.PET_SINGLE_PROCESS==='1')args.push('--no-zygote','--single-process','--use-gl=angle','--use-angle=swiftshader');
 const browser=await chromium.launch({headless:true,args,...(process.env.PET_CHROME?{executablePath:process.env.PET_CHROME}:{})});
 const context=await browser.newContext({baseURL:process.env.PET_TEST_URL||'http://127.0.0.1:5000',viewport:{width:1440,height:1050}});
 const errors=[];const external=[];
 await context.route('**/*',r=>{if(!r.request().url().startsWith(process.env.PET_TEST_URL||'http://127.0.0.1:5000')){external.push(r.request().url());return r.abort();}return r.continue();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.PET_TEST_URL||'http://127.0.0.1:5000');
 await page.waitForFunction(()=>window.petTwin?.inspect().drawCalls>0);
 const inspect=()=>page.evaluate(()=>window.petTwin.inspect());
 assert.equal((await inspect()).petCount,1);assert.equal((await inspect()).sensorCount,5);
 await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('Live connection'));
 const frames=[];
 await page.evaluate(()=>{window.fadeFrames=[];window.fadeTimer=setInterval(()=>window.fadeFrames.push(window.petTwin.inspect()),16);});
 for(const [sensor,zone] of [[1,'A'],[2,'A'],[3,'B'],[4,'B'],[5,'C']]){
  await page.getByRole('button',{name:'Sensor '+sensor,exact:true}).click();
  await page.waitForFunction(z=>window.petTwin.inspect().targetZone===z&&window.petTwin.inspect().opacity>.99,zone);
  assert.match(await page.locator('#petstate').innerText(),new RegExp('Zone '+zone));
  assert.equal((await inspect()).petCount,1);
 }
 frames.push(...await page.evaluate(()=>{clearInterval(window.fadeTimer);return window.fadeFrames;}));
 assert.ok(frames.some(f=>f.opacity>0.05&&f.opacity<.95),'fade has intermediate alpha values');
 for(let i=1;i<frames.length;i++)if(JSON.stringify(frames[i].position)!==JSON.stringify(frames[i-1].position))assert.ok(frames[i].opacity<.15,'relocation only while hidden');
 await page.getByRole('button',{name:'Clear detections',exact:true}).click();
 await page.waitForFunction(()=>!window.petTwin.inspect().petVisible);
 // Rapid changes should converge on final sensor with no duplicate avatar.
 for(const sensor of [1,3,5,1])await page.getByRole('button',{name:'Sensor '+sensor,exact:true}).click();
 await page.waitForFunction(()=>window.petTwin.inspect().targetZone==='A'&&window.petTwin.inspect().opacity>.99);
 const canvas=page.locator('#scene canvas');const bounds=await canvas.boundingBox();
 const center={x:bounds.x+bounds.width*.5,y:bounds.y+bounds.height*.5};
 let before=await inspect();await page.mouse.move(center.x,center.y);await page.mouse.down();await page.mouse.move(center.x+120,center.y+50,{steps:15});await page.mouse.up();await page.waitForTimeout(300);
 assert.notDeepEqual((await inspect()).camera,before.camera,'orbit changes camera');
 before=await inspect();await page.mouse.wheel(0,-250);await page.waitForTimeout(350);assert.notDeepEqual((await inspect()).camera,before.camera,'zoom changes camera');
 before=await inspect();await page.mouse.down({button:'right'});await page.mouse.move(center.x+70,center.y+100,{steps:10});await page.mouse.up({button:'right'});await page.waitForTimeout(300);assert.notDeepEqual((await inspect()).target,before.target,'pan changes orbit target');
 await page.getByRole('checkbox',{name:'Show labels',exact:true}).uncheck();await page.getByRole('checkbox',{name:'Show labels',exact:true}).check();
 await page.getByRole('checkbox',{name:'Show zone markers',exact:true}).uncheck();await page.getByRole('checkbox',{name:'Show zone markers',exact:true}).check();
 await page.getByRole('button',{name:'Plan view',exact:true}).click();await page.waitForTimeout(400);
 await page.screenshot({path:process.env.PET_SCREENSHOT_DIR?process.env.PET_SCREENSHOT_DIR+'/plan.png':'/tmp/pet-plan.png',fullPage:true});
 await page.getByRole('button',{name:'Perspective',exact:true}).click();await page.waitForTimeout(400);
 await page.screenshot({path:process.env.PET_SCREENSHOT_DIR?process.env.PET_SCREENSHOT_DIR+'/perspective.png':'/tmp/pet-perspective.png',fullPage:true});
 // Live coherent distance frames take over and disappear after feed is lost.
 for(let i=0;i<3;i++)await page.request.post('/api/sensors',{data:{distances:{1:100,2:100,3:100,4:100,5:30}}});
 await page.waitForFunction(()=>window.petTwin.inspect().targetZone==='C'&&window.petTwin.inspect().opacity>.99);
 await page.waitForFunction(()=>!window.petTwin.inspect().petVisible,null,{timeout:7000});
 // Repeated refreshes must not keep allocating materials/textures.
 let memBefore=await inspect();await page.waitForTimeout(1500);let memAfter=await inspect();assert.equal(memBefore.geometries,memAfter.geometries);assert.equal(memBefore.textures,memAfter.textures);
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);assert.ok(await canvas.isVisible());
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log(JSON.stringify({result:'PASS',checks:['WebGL render','five sensors','single pet','S1–S5 mapping','fade alpha and hidden relocation','rapid switching','clear','Socket.IO connected','orbit','zoom','pan','labels/zones controls','plan/perspective','live HTTP takeover and stale expiry','stable geometry/texture counts','mobile resize','zero page errors','no external requests'],fadeSamples:frames.length},null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
