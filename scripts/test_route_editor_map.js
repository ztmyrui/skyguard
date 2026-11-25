const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const moveCalls = [];
const batches = [];
const markers = [];
const removed = [];
const dom = { textContent:'', style:{} };
class Marker {
  constructor(options) { this.config=options; this.events={}; markers.push(this); }
  on(name, callback) { this.events[name]=callback; }
  setPosition(point) { this.config.position=point; moveCalls.push(point); }
  setContent(content) { this.config.content=content; }
}
const environment = {
  document:{
    getElementById:()=>dom,documentElement:{dataset:{}},
    createElement:()=>({events:{},setAttribute(){},addEventListener(name,callback){this.events[name]=callback}})
  },
  getComputedStyle:()=>({getPropertyValue:()=> '#1f3fd6'}),
  qt:{webChannelTransport:{}},QWebChannel:function(){},
  clearTimeout,setTimeout,console,TrainingMap:{update(){},fly(){}},
  AMap:{Marker,Pixel:class {},Polyline:class { on(){} setPath(){} }},
  mapStub:{add(){},remove(marker){removed.push(marker)},setZoomAndCenter(){}},
  bridgeStub:{routePending(){},routeBatch:(...args)=>batches.push(args),routeAction(){}},
};
environment.window=environment;
const context=vm.createContext(environment);
for(const asset of ['map-core.js','amap.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../skyguard/assets',asset),'utf8'),context);
}
(async()=>{
  vm.runInContext(`
   ready=true;
   running=true;
   map=mapStub;
   bridge=bridgeStub;
   convert=async points=>points.map(([lon,lat])=>[lon+.004,lat-.003]);
   updateScene({public:true,city:'changsha',center:[112.95,28.18],route_step:0,route_epoch:1,
                route_ack:0,route_editing:true});
   for(const lng of [112.95,112.951,112.952,112.953])routeEdit('add',-1,{lng,lat:28.18});
  `,context);
  assert.equal(markers.length,4,'takeoff and three waypoints render before conversion');
  assert.equal(batches.length,0);
  assert(markers.every(marker=>marker.config.anchor==='center'));
  assert.equal(markers[2].config.position[0],112.952);
  await vm.runInContext('clearTimeout(editor.timer);flushEditor()',context);
  assert.equal(batches.length,1);
  const coordinates=JSON.parse(batches[0][3]);
  assert.deepEqual(coordinates.map(row=>row[0]),['takeoff','waypoint','waypoint','waypoint']);
  assert(Math.abs(coordinates[0][2]-112.946)<1e-8);
  const accepted={public:true,city:'changsha',center:[112.95,28.18],route_step:1,route_epoch:1,
                  route_ack:1,route_editing:true,routes:[{style:'edit',takeoff:coordinates[0].slice(2),
                    itinerary:[{}], shots:[{id:'shot-1'}],
                    waypoints:coordinates.slice(1).map(row=>[...row.slice(2),70])}]};
  context.confirmed=accepted;
  vm.runInContext("updateScene(confirmed)",context);
  const inserts = markers.filter(marker=>marker.config.content.className==='edit-insert');
  assert.equal(inserts.length,2,'there is a plus at each waypoint midpoint');
  assert(inserts.every(marker=>marker.config.zIndex===78 && !marker.config.draggable));
  assert.equal(vm.runInContext('pending.routes[0].shots.length',context),0,'editing drops overlapping shot labels');
  assert.equal(vm.runInContext('editor.markers.get("w1").draggable',context),true);
  const waypoint = markers[2];
  waypoint.events.dragstart();
  assert(inserts.every(marker=>removed.includes(marker)),'midpoints hide during drag');
  waypoint.events.dragend({lnglat:{lng:112.9525,lat:28.181}});
  assert.equal(moveCalls.length,1,'only the moved waypoint is repositioned');
  assert.equal(markers.filter(marker=>typeof marker.config.content==='string' && marker.config.content.includes('edit-anchor')).length,4,
               'drag preserves waypoint markers');
  await vm.runInContext('clearTimeout(editor.timer);flushEditor()',context);
  assert.equal(batches.length,2);
  vm.runInContext("updateScene({...confirmed,route_ack:2,photo_preview:true})",context);
  assert.equal(vm.runInContext('pending.routes[0].shots.length',context),1,'rehearsal restores shot labels');
  const plus = markers.findLast(marker=>marker.config.content.className==='edit-insert');
  const [midLng, midLat] = plus.config.position;
  plus.config.content.events.click({stopPropagation(){}});
  await vm.runInContext('clearTimeout(editor.timer);flushEditor()',context);
  const insert = JSON.parse(batches[2][3])[0];
  assert.deepEqual(insert.slice(0,2),['insert',2]);
  assert(Math.abs(insert[2]-(midLng-.004))<1e-8);
  assert(Math.abs(insert[3]-(midLat+.003))<1e-8);
  const projection=vm.runInContext(`
    SkyMapCore.viewportWarp([0,0,10,10],[[1,2],[11,2],[1,12],[11,12]])([5,5])
  `,context);
  assert.deepEqual([...projection],[6,7]);
  console.log('P13 map editor: immediate anchored markers, ordered batch, and incremental drag verified');
})().catch(error=>{console.error(error);process.exitCode=1});
