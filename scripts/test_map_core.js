const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(require('node:path').join(__dirname,'../skyguard/assets/map-core.js'),'utf8'));
(async()=>{
 const calls=[],cache=new Map();const convert=async p=>{calls.push(p.length);return p.map(([x,y])=>[x+.005,y-.003])};
 const points=Array.from({length:85},(_,i)=>[112+i*.001,28]);
 await SkyMapCore.convertBatch([...points,points[0]],convert,cache);
 assert.deepEqual(calls,[40,40,5]);
 await SkyMapCore.convertBatch(points,convert,cache);assert.equal(calls.length,3);
 const original=await SkyMapCore.inverse([112.955,28.177],convert);
 assert.ok(Math.abs(original[0]-112.95)<1e-7&&Math.abs(original[1]-28.18)<1e-7);
 calls.length=0;
 const targets=points.slice(0,54).map(([x,y])=>[x+.005,y-.003]);
 const originals=await SkyMapCore.inverseBatch(targets,convert);
 assert.deepEqual(calls,[40,14,40,14]);
 assert.ok(originals.every(([x,y],i)=>Math.abs(x-points[i][0])<1e-6&&Math.abs(y-points[i][1])<1e-6));
 await assert.rejects(()=>SkyMapCore.convertBatch([[1,1]],async()=>[],new Map()));
 console.log('Map core: batching, cache, inverse and failed conversion checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
