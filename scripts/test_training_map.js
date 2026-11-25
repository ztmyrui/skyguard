
/* Draw one mixed training frame against a deterministic browser adapter. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const operations = [];
const markers = [];
const ctx = new Proxy({}, {get(target, key) {
  if (key === 'measureText') return text => ({width: text.length*6});
  return target[key] || ((...args) => operations.push([key, ...args]));
}});
function node(tag) {
  const documentNode = {
    tag, style: {}, children: [], appendChild(child) { this.append(child); },
    append(...children) { this.children.push(...children); },
    getBoundingClientRect() { return {width: 620, height: 420}; },
    getContext() { return ctx; },
    addEventListener(kind, fn) { this[kind]=fn; },
    setAttribute() {}, remove() {}, matches() { return false; }
  };
  if(tag==='button')markers.push(documentNode);
  return documentNode;
}
const env = {
  window: {}, document: {createElement: node}, performance: {now: () => 450},
  Image: class {}, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  getComputedStyle: () => ({getPropertyValue: () => ''})
};
vm.runInNewContext(fs.readFileSync('skyguard/assets/training-map.js','utf8'),env);
const svg=fs.readFileSync('skyguard/assets/aircraft.svg','utf8');
assert.equal((svg.match(/<use href="#rotor"/g)||[]).length,4);
let selected;
const base={lon:100,lat:120,heading:80,speed:5,level:4,tail:[[95,120],[100,120]],prediction:[[100,120],[130,120]]};
env.window.TrainingMap.update([
  {...base,id:'T04',selected:true,cpa:{entering:true,seconds:22,point:[129,120]}},
  {...base,id:'T05',lon:102,level:3,state:'失联'}
],node('root'), (point)=>({x:point[0],y:point[1]}),id=>selected=id,'aircraft.svg',null,{
  formations:[{id:'G01',level:4,hull:[[70,90],[130,90],[130,150],[70,90]],points:[[100,120],[102,120]]}],
  links:[{start:[25,35],end:[100,120],label:'自动提醒'}],
  posts:[{point:[35,55],name:'观察岗'}],
  zones:[{kind:'zone-s1',name:'核心活动区',rings:[[[50,50],[70,50],[70,80],[50,50]]]}]
});
assert.ok(operations.some(([kind,text])=>kind==='fillText'&&text==='协同编队 G01 · 2架'));
assert.ok(operations.some(([kind,text])=>kind==='fillText'&&text==='+22s 入区'));
assert.ok(operations.some(([kind])=>kind==='quadraticCurveTo'));
assert.ok(operations.some(([kind,text])=>kind==='fillText'&&text==='核心活动区'));
assert.ok(operations.some(([kind,x,y,r])=>kind==='arc'&&r===17));
assert.ok(operations.some(([kind,x,y,r])=>kind==='arc'&&r===2.5));
assert.ok(operations.some(([kind,text])=>kind==='strokeText'&&text==='核心活动区'));
assert.equal(operations.filter(([kind])=>kind==='fillRect').length,2);
assert.equal(operations.filter(([kind])=>kind==='roundRect').length,2);
assert.match(markers[0].children[0].style.cssText,/width:18px;height:18px/);
assert.equal(markers[0].style.left,'91px');
assert.equal(markers[0].style.top,'111px');
assert.notEqual(markers[0].children[1].style.top,markers[1].children[1].style.top);
markers[0].click({stopPropagation() {}});
assert.equal(selected,'T04');
operations.length=0;
env.window.TrainingMap.update([
  {...base,id:'T04',lon:-350,lat:-180,instant:true,selected:true,
   cpa:{entering:true,seconds:22,point:[-1,-1]}}
],node('root'),point=>({x:point[0],y:point[1]}),()=>{},'aircraft.svg',null,{
  posts:[{point:[-100,-100],name:'离屏观察岗'}],
  zones:[{kind:'zone-s1',name:'离屏空域',rings:[[[-140,-120],[-130,-120],[-130,-110],[-140,-120]]]}]
});
assert.equal(markers[0].style.display,'none');
assert.ok(!operations.some(([kind])=>['fillText','strokeText','fillRect','arc'].includes(kind)));
env.window.TrainingMap.update([],node('root'),point=>({x:point[0],y:point[1]}),()=>{},'aircraft.svg');
assert.equal(operations.at(-1)[0],'clearRect');
const project=point=>({x:point[0],y:point[1]});
const civicAircraft={...base,id:'civic-a',label:'偏航提醒',instant:true,civic:true,level:2,tail:[],prediction:[]};
const civicLayers={civic:true,zones:[{name:'常驻区域'}],formations:[{id:'legacy'}],
  staff:[{point:[75,58],name:'巡查员·周明',status:'已改派',eta_s:13,route:[[75,58],[65,62],[57,60]]}]};
const drawCivic=(rows,layers)=>{
  operations.length=0;
  env.window.TrainingMap.update(rows,node('root'),project,()=>{},'aircraft.svg',null,layers);
};
drawCivic([civicAircraft],civicLayers);
assert.equal(markers.at(-1).children[0].style.background,'#d99820');
assert.match(markers.at(-1).children[0].style.mask,/aircraft\.svg/);
assert.ok(operations.some(([kind,x,y])=>kind==='lineTo'&&x===65&&y===62));
assert.ok(operations.some(([kind,text])=>kind==='fillText'&&text==='巡查员·周明 · 已改派 13s'));
assert.ok(!operations.some(([kind])=>kind==='strokeText'));
assert.ok(!operations.some(([kind,x,y,r])=>kind==='arc'&&r===8));
drawCivic([civicAircraft],{...civicLayers,highlights:[{name:'展馆市集区',point:[200,180],radius:16}]});
assert.ok(operations.some(([kind,text])=>kind==='fillText'&&text==='展馆市集区'));
assert.ok(operations.some(([kind,x,y,r])=>kind==='arc'&&x===200&&y===180&&r===8));
drawCivic([{...civicAircraft,lon:-1}],{...civicLayers,
  staff:[{...civicLayers.staff[0],point:[-1,58]}],highlights:[{name:'离屏',point:[-1,58],radius:16}]});
assert.equal(markers.at(-1).style.display,'none');
assert.deepEqual(operations.map(([kind])=>kind),['setTransform','clearRect']);
console.log('Training map: legacy renderer plus calm civic map, highlights, patrol route, and strict viewport clipping passed');

const roles=['foot','vehicle','guard','recovery'];
assert.equal(new Set(roles.map(role=>env.window.TrainingMap.civicRole(role).color)).size,4);
drawCivic([], {...civicLayers,staff:roles.map((role,i)=>({
  role,point:[60+i*90,90],name:role,status:'待命',eta_s:0,route:[]
}))});
for(const [i,role] of roles.entries()){
  const glyph=env.window.TrainingMap.civicRole(role).glyph;
  assert.ok(operations.some(([kind,text,x])=>kind==='fillText'&&text===glyph&&x===60+i*90));
}
