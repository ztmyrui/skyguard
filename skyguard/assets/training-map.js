/* Shared aircraft layer. Each provider supplies a coordinate-to-pixel adapter. */
(function () {
  let markers = new Map(), rows = [], canvas, container, adapter, svg, frame = 0;
  let tactics = {};
  let hoverId=null, pointer=null;
  let control = false, inputTarget, inputBridge, busy = false, position = null;
  const colors = ['#25aa87','#d99820','#ee722e','#ec405f'];
  const eligible = p => p && Number.isFinite(p.x) && Number.isFinite(p.y);
  const inViewport = (p,size,margin=28) => eligible(p) &&
    p.x>=-margin && p.x<=size.width+margin && p.y>=-margin && p.y<=size.height+margin;
  function create(root) {
    if (container) return;
    container = document.createElement('div');
    container.style.cssText='position:absolute;inset:0;z-index:650;pointer-events:none;overflow:hidden';
    canvas = document.createElement('canvas');
    canvas.style.cssText='position:absolute;inset:0;pointer-events:none;width:100%;height:100%';
    container.appendChild(canvas); root.appendChild(container);
    root.addEventListener('pointermove',event=>{
      const rect=root.getBoundingClientRect();
      pointer={x:event.clientX-rect.left,y:event.clientY-rect.top};
      paint();
    });
    root.addEventListener('pointerleave',()=>{pointer=null;hoverId=null;paint()});
  }
  function color(row) {
    if(row.state==='失联')return '#8992a3';
    if(row.public)return '#2458d8';
    if(row.civic)return row.level>=3?colors[3]:row.level===2||row.filed===false?colors[1]:colors[0];
    return colors[(row.level||1)-1];
  }
  function update(list, root, project, select, source, followMap, layers = {}) {
    create(root);
    if(!list.length){hoverId=null;pointer=null}
    tactics=layers;
    if (!svg) {
      svg=new Image();
      svg.src=source;
      svg.onload=paint;
    }
    adapter=project;
    const latest=new Map(rows.map(row=>[row.id,row]));
    rows=list.map(row=>{
      const old=latest.get(row.id);
      return {...row, before:old ? [old.lon,old.lat] : [row.lon,row.lat], seenAt:performance.now()};
    });
    const visible=new Set(rows.length<=50?rows.map(row=>row.id):[]);
    for(const [id,marker] of markers)if(!visible.has(id)){marker.remove();markers.delete(id)}
    for(const row of rows) if(visible.has(row.id)&&!markers.has(row.id)){
      const marker=document.createElement('button');
      marker.type='button';
      marker.style.cssText='position:absolute;pointer-events:auto;border:0;background:none;padding:0;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:4px';
      const plane=document.createElement('span');
      plane.setAttribute('aria-hidden','true');
      plane.style.cssText='width:18px;height:18px;filter:drop-shadow(0 1px 2px #10243c80)';
      const caption=document.createElement('span');
      caption.style.cssText='position:absolute;font:600 11px/14px sans-serif;padding:2px 6px;background:color-mix(in srgb,var(--surface) 86%,transparent);color:var(--ink);border-left:2px solid;border-radius:8px;white-space:pre;overflow:hidden;text-overflow:ellipsis;max-width:176px;pointer-events:none';
      marker.append(plane,caption);
      marker.addEventListener('click',ev=>{ev.stopPropagation();select(row.id)});
      marker.addEventListener('pointerenter',()=>{hoverId=row.id;paint()});
      marker.addEventListener('pointerleave',()=>{if(hoverId===row.id)hoverId=null;paint()});
      container.append(marker);
      markers.set(row.id,marker);
    }
    if (followMap) {
      const pilot = rows.find(row => row.public && !row.public_plan);
      const point = pilot && project([pilot.lon,pilot.lat]);
      const box=root.getBoundingClientRect();
      if (eligible(point) && (point.x<box.width*.22||point.x>box.width*.78||
                              point.y<box.height*.22||point.y>box.height*.78)) {
        followMap([pilot.lon,pilot.lat]);
      }
    }
    cancelAnimationFrame(frame);
    paint();
  }
  const pixel = point => point && adapter(point);
  function diamond(ctx, p, radius, color) {
    ctx.setLineDash([]);ctx.fillStyle=color;ctx.strokeStyle='#e5f3ff';ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(p.x,p.y-radius);ctx.lineTo(p.x+radius,p.y);
    ctx.lineTo(p.x,p.y+radius);ctx.lineTo(p.x-radius,p.y);ctx.closePath();ctx.fill();ctx.stroke();
  }
  function labelSlot(placed,size,x,y,width,height) {
    if(x<4||x+width>size.width-4)return null;
    for(let i=0;i<5;i++){
      const candidate=y+Math.ceil(i/2)*(height+4)*(i%2?1:-1);
      if(candidate<4||candidate+height>size.height-8)continue;
      if(placed.some(p=>x<p.x+p.width+4 && x+width+4>p.x && candidate<p.y+p.height+3 && candidate+height+3>p.y))continue;
      const box={x,y:candidate,width,height};
      placed.push(box);
      return box;
    }
    return null;
  }
  function tag(ctx, text, x, y, tint, fill, ink, placed, size) {
    if(!inViewport({x,y},size,0))return;
    ctx.font='600 11px sans-serif';
    const width=ctx.measureText(text).width+12;
    const box=labelSlot(placed,size,x,y-13,width,17);
    if(!box)return;
    if(Math.abs(box.y-y+13)>4){
      ctx.setLineDash([]);ctx.strokeStyle=tint;ctx.lineWidth=.8;
      ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(box.x,box.y+9);ctx.stroke();
    }
    ctx.fillStyle=fill;ctx.beginPath();ctx.roundRect(box.x,box.y,width,17,5);ctx.fill();
    ctx.fillStyle=tint;ctx.fillRect(box.x,box.y,2,17);
    ctx.fillStyle=ink;ctx.fillText(text,box.x+6,box.y+13);
  }
  function drawTactics(ctx, timestamp, size, addTag) {
    const zones={'zone-s1':['#ec405f',[]],'zone-school':['#d99820',[6,4]],
      'zone-road':['#748ca4',[]],'zone-recovery':['#25aa87',[7,5]],'zone-buffer':['#b78a38',[6,4]]};
    for(const zone of tactics.zones||[]){
      const [tint,dash]=zones[zone.kind]||['#4e91be',[]];
      const rings=zone.rings.map(ring=>ring.map(pixel).filter(eligible));
      const outer=rings[0];
      if(!outer||outer.length<3)continue;
      ctx.fillStyle=tint+'21';ctx.strokeStyle=tint;ctx.lineWidth=zone.kind==='zone-road'?2.5:1.5;
      ctx.setLineDash(dash);ctx.beginPath();
      for(const ring of rings){
        ring.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));
        ctx.closePath();
      }
      ctx.fill('evenodd');ctx.stroke();
      let area=0,x=0,y=0;
      outer.forEach((p,i)=>{
        const q=outer[(i+1)%outer.length], cross=p.x*q.y-q.x*p.y;
        area+=cross;x+=(p.x+q.x)*cross;y+=(p.y+q.y)*cross;
      });
      if(area){
        const center={x:x/(3*area),y:y/(3*area)};
        if(inViewport(center,size,0)){
          ctx.save();ctx.font='600 12px sans-serif';ctx.textAlign='center';ctx.setLineDash([]);
          ctx.lineWidth=1;ctx.strokeStyle='#ffffff88';ctx.fillStyle=tint+'b3';
          ctx.strokeText(zone.name,center.x,center.y);ctx.fillText(zone.name,center.x,center.y);ctx.restore();
        }
      }
    }
    for(const group of tactics.formations||[]){
      const hull=group.hull.map(pixel).filter(eligible), points=group.points.map(pixel).filter(eligible);
      if(hull.length<3||points.length<2)continue;
      const tint=group.level>=3?'#f28d56':'#a77cf4';
      ctx.strokeStyle=tint;ctx.fillStyle=group.level>=3?'#f28d561a':'#a77cf420';
      ctx.lineWidth=1.5;ctx.setLineDash([6,4]);ctx.beginPath();
      hull.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));
      ctx.closePath();ctx.fill();ctx.stroke();
      ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.stroke();
      addTag('协同编队 '+group.id+' · '+points.length+'架',
             Math.min(...hull.map(p=>p.x)),Math.min(...hull.map(p=>p.y))-10,tint);
    }
    const labelled=new Set(), activePosts=new Set();
    const onFoot=new Set((tactics.links||[]).filter(link=>link.walking).map(link=>link.target));
    for(const link of tactics.links||[]){
      const target=rows.find(row=>row.id===link.target);
      if(target&&!target.selected&&(target.level<3||target.state==='已降落'))continue;
      const a=pixel(link.start),b=pixel(link.end);
      if(!eligible(a)||!inViewport(b,size))continue;
      const mid={x:(a.x+b.x)/2,y:(a.y+b.y)/2}, lift=Math.min(55,Math.hypot(a.x-b.x,a.y-b.y)*.28);
      const tint=link.label==='引导回收'?'#26b991':'#54a0fa';
      ctx.strokeStyle=tint+'bb';ctx.lineWidth=1.2;ctx.setLineDash([5,5]);ctx.lineDashOffset=-timestamp/135;
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(mid.x,mid.y-lift,b.x,b.y);ctx.stroke();
      if(link.walking){
        activePosts.add(link.post_id);
        if(inViewport(a,size,0)){
          const post=pixel(tactics.posts?.find(post=>post.id===link.post_id)?.point);
          if(eligible(post)){
            ctx.strokeStyle='#3265bd99';ctx.setLineDash([2,4]);ctx.lineDashOffset=0;
            ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(post.x,post.y);ctx.stroke();
          }
          ctx.setLineDash([]);ctx.fillStyle='#256ac1';ctx.strokeStyle='#ffffff';ctx.lineWidth=1.5;
          ctx.beginPath();ctx.arc(a.x,a.y,4,0,2*Math.PI);ctx.fill();ctx.stroke();
        }
      }
      if((link.walking||target?.selected&&!onFoot.has(link.target))&&!labelled.has(link.label)){
        labelled.add(link.label);
        addTag(link.label,mid.x,mid.y-lift/2,tint);
      }
    }
    ctx.lineDashOffset=0;
    for(const post of tactics.posts||[]){
      const p=pixel(post.point);
      if(inViewport(p,size,0)){
        diamond(ctx,p,6,'#387eb5');
        if(activePosts.has(post.id)||(pointer&&Math.hypot(pointer.x-p.x,pointer.y-p.y)<=9))
          addTag(post.name.replace('观察岗','岗'),p.x+8,p.y+5,'#387eb5');
      }
    }
  }
  function statusArt(ctx, p, row, tint, timestamp) {
    ctx.strokeStyle=tint;ctx.lineWidth=1.25;ctx.setLineDash(row.state==='失联'?[4,4]:[]);
    ctx.beginPath();ctx.arc(p.x,p.y,row.state==='失联'?17:11,0,2*Math.PI);ctx.stroke();
    ctx.setLineDash([]);
    if(row.level>=3 && row.state!=='失联'){
      for(let i=0;i<(row.level===4?2:1);i++){
        const phase=(timestamp/1500+i*.5)%1;
        ctx.globalAlpha=.65*(1-phase);
        ctx.beginPath();ctx.arc(p.x,p.y,12+10*phase,0,2*Math.PI);ctx.stroke();
      }
      ctx.globalAlpha=1;
    }
    if(row.selected){
      ctx.lineWidth=2;
      for(const x of [-1,1])for(const y of [-1,1]){
        ctx.beginPath();ctx.moveTo(p.x+x*8,p.y+y*13);ctx.lineTo(p.x+x*13,p.y+y*13);
        ctx.lineTo(p.x+x*13,p.y+y*8);ctx.stroke();
      }
    }
    if(row.state==='失联'||row.state==='已降落')return;
    ctx.save();ctx.translate(p.x,p.y);ctx.rotate((row.heading||0)*Math.PI/180);
    ctx.strokeStyle='#d8f7ff';ctx.globalAlpha=.64;ctx.lineWidth=1.2;
    for(const [i,[x,y]] of [[-5,-5],[5,-5],[-5,5],[5,5]].entries()){
      const angle=timestamp/(row.speed<1?170:75)*(i%2?1:-1)+i;
      ctx.beginPath();ctx.arc(x,y,2.5,angle,angle+1.9);ctx.stroke();
    }
    ctx.restore();
  }
  const roleStyles={
    foot:{color:'#3975be',glyph:'巡'},vehicle:{color:'#087f91',glyph:'机'},
    guard:{color:'#5549a2',glyph:'岗'},recovery:{color:'#228365',glyph:'备'}
  };
  function civicRole(role){return roleStyles[role]||roleStyles.foot}
  function drawCivic(ctx, size, addTag) {
    const visible = p => inViewport(p, size, 0);
    function line(points, tint) {
      if(points.length<2||!points.every(eligible))return;
      ctx.strokeStyle=tint;
      ctx.lineWidth=1.3;
      ctx.setLineDash([4,4]);
      ctx.beginPath();
      points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));
      ctx.stroke();
    }
    for(const area of tactics.highlights||[]){
      const p=pixel(area.point);
      if(!visible(p))continue;
      const edge=pixel([area.point[0],area.point[1]+area.radius/111195]);
      const radius=eligible(edge)? Math.max(8,Math.abs(edge.y-p.y)):12;
      const tint=area.recovery?'#46a883':'#cf795a';
      ctx.fillStyle=tint+'1f';
      ctx.strokeStyle=tint+'aa';
      ctx.lineWidth=1;
      ctx.setLineDash([4,4]);
      ctx.beginPath();
      ctx.arc(p.x,p.y,radius,0,2*Math.PI);
      ctx.fill();
      ctx.stroke();
      addTag(area.name,p.x+10,p.y+radius+15,tint);
    }
    for(const link of tactics.civicLinks||[]){
      const a=pixel(link.start), b=pixel(link.end);
      if(!visible(a)||!visible(b))continue;
      const tint=link.recovery?'#45a982':'#5295d0';
      ctx.globalAlpha=link.opacity??1;
      line([a,b],tint+'aa');
      ctx.globalAlpha=1;
      addTag(link.label,(a.x+b.x)/2,(a.y+b.y)/2,tint);
    }
    for(const patrol of tactics.staff||[]){
      const p=pixel(patrol.point);
      if(!visible(p))continue;
      const role=civicRole(patrol.role);
      const style=patrol.status.startsWith('临时受阻')?{...role,color:'#a05a00'}:role;
      if(patrol.route.length)line(patrol.route.map(pixel),style.color+'a0');
      if(patrol.status==='现场核验'&&visible(pixel(patrol.target_point))){
        line([p,pixel(patrol.target_point)],'#c77739b0');
      }
      ctx.setLineDash([]);
      ctx.fillStyle=style.color;
      ctx.beginPath();
      ctx.roundRect(p.x-9,p.y-9,18,18,5);
      ctx.fill();
      ctx.fillStyle='#fff';
      ctx.font='bold 11px sans-serif';
      ctx.textAlign='center';
      ctx.textBaseline='middle';
      ctx.fillText(style.glyph,p.x,p.y+.5);
      const hovered=pointer&&Math.hypot(pointer.x-p.x,pointer.y-p.y)<12;
      if(patrol.status!=='待命'||hovered){
        const status=patrol.status==='现场核验'?'现场劝导中':patrol.status;
        addTag(patrol.name+' · '+status+(patrol.eta_s?' '+patrol.eta_s+'s':''),p.x+12,p.y,style.color);
      }
    }
  }
  function paint() {
    if (!container||!adapter) return;
    cancelAnimationFrame(frame);
    const size=container.getBoundingClientRect(), density=window.devicePixelRatio||1;
    if(canvas.width!==Math.round(size.width*density)||canvas.height!==Math.round(size.height*density)){
      canvas.width=Math.round(size.width*density);canvas.height=Math.round(size.height*density);
    }
    const ctx=canvas.getContext('2d');ctx.setTransform(density,0,0,density,0,0);
    ctx.clearRect(0,0,size.width,size.height);
    const timestamp=performance.now(), style=getComputedStyle(container);
    const fill=(style.getPropertyValue('--surface').trim()||'#f4f7fc')+'d6';
    const ink=style.getPropertyValue('--ink').trim()||'#172337';
    const tags=[];
    if(tactics.civic)drawCivic(ctx,size,(...args)=>tags.push(args));
    else drawTactics(ctx,timestamp,size,(...args)=>tags.push(args));
    let animated=Boolean(tactics.links?.length);
    const placed=[];
    const priority=row=>row.selected?10:row.id===hoverId?9:row.level||1;
    for(const row of [...rows].sort((a,b)=>priority(b)-priority(a))){
      const ratio=row.instant?1:Math.min(1,(performance.now()-row.seenAt)/900);
      const follow=[row.before[0]+(row.lon-row.before[0])*ratio,row.before[1]+(row.lat-row.before[1])*ratio];
      const p=adapter(follow), c=color(row);
      const marker=markers.get(row.id);
      const visible=inViewport(p,size,row.civic?0:28);
      if(marker)marker.style.display=visible?'flex':'none';
      animated ||= ratio<1;
      if(!visible){if(hoverId===row.id)hoverId=null;continue}
      if (row.connector) {
        const to=adapter(row.connector);
        if (eligible(to)) {
          ctx.setLineDash([]);
          ctx.strokeStyle='#737d92';
          ctx.lineWidth=1.5;
          ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(to.x,to.y);ctx.stroke();
        }
      }
      const forecast=row.selected||(row.level>=3&&row.state!=='已降落');
      for(const [type,points] of [['tail',row.tail||[]],['prediction',forecast?(row.prediction||[]):[]]]){
        ctx.strokeStyle=c;
        ctx.lineWidth=type==='prediction'?1.5:2;
        ctx.setLineDash(type==='prediction'?[5,5]:[]);
        for(let i=1;i<points.length;i++){
          const a=pixel(points[i-1]), b=pixel(points[i]);
          if(!eligible(a)||!eligible(b))continue;
          ctx.globalAlpha=type==='prediction'?.7:.12+.55*i/points.length;
          ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();
        }
      }
      ctx.globalAlpha=1;
      if(row.cpa?.entering&&forecast){
        const point=pixel(row.cpa.point);
        if(inViewport(point,size,0)){
          diamond(ctx,point,4,c);
          tags.unshift(['+'+row.cpa.seconds+'s 入区',point.x+8,point.y-5,c]);
        }
      }
      if(marker){
        const radius=row.public?11:9;
        marker.style.left=(p.x-radius)+'px';marker.style.top=(p.y-radius)+'px';
        marker.setAttribute('aria-label',row.label||row.id);
        const [plane, caption]=marker.children;
        plane.style.width=plane.style.height=2*radius+'px';
        plane.style.transform='rotate('+(row.heading||0)+'deg)'+(row.selected&&!row.public?' scale(1.1)':'');
        const icon='url('+svg.src+') center / contain no-repeat';
        plane.style.mask=row.civic?icon:'';
        plane.style.background=row.civic?c:icon;
        caption.style.borderColor=row.selected?'var(--pri)':'var(--edge)';
        caption.style.borderLeftColor=c;
        caption.style.boxShadow=row.selected?'0 0 0 2px var(--sel), 0 2px 8px var(--line)':'';
        marker.style.zIndex=row.selected?'1':'';
        plane.style.opacity=row.state==='失联'?String(.72+.2*Math.sin(timestamp/520)):'1';
        const expanded=row.selected||hoverId===row.id||marker.matches(':focus-visible');
        const description=[row.speed===undefined?'':row.speed.toFixed(1)+' m/s',row.area,row.action].filter(Boolean).join(' · ');
        const captionText=[row.public||row.civic?(row.label||row.id):row.id+(row.level>1||expanded?' · '+(row.level||1)+'级':''),
          expanded&&row.speed!==undefined?row.speed.toFixed(1)+'m/s':'',
          expanded&&row.altitude!==undefined?'相对真高 '+Math.round(row.altitude)+' m':''].filter(Boolean).join(' · ')
          +(expanded&& (row.action||row.area)?'\n'+(row.action||row.area):'');
        if(caption.textContent!==captionText)caption.textContent=captionText;
        marker.title=description;
        caption.style.display='';
        caption.style.opacity=!expanded&&row.level===1?'.72':'1';
        const width=caption.offsetWidth||96, height=caption.offsetHeight||18;
        const left=p.x+14+width>size.width-4?-width-14:14;
        const box=inViewport(p,size,0)&&labelSlot(placed,size,p.x+left,p.y-height/2,width,height);
        caption.style.display=box&&(!row.civic||expanded||row.level>1)?'':'none';
        if(box){
          caption.style.left=(box.x-p.x+radius)+'px';caption.style.top=(box.y-p.y+radius)+'px';
        }
        if(box&&Math.abs(box.y-p.y+height/2)>1){
          ctx.setLineDash([]);ctx.strokeStyle=c;ctx.lineWidth=1;
          ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(left<0?box.x+width:box.x,box.y+height/2);ctx.stroke();
        }
      } else {
        ctx.save();ctx.translate(p.x,p.y);ctx.rotate((row.heading||0)*Math.PI/180);
        ctx.fillStyle=c;ctx.beginPath();ctx.arc(0,0,9,0,2*Math.PI);ctx.fill();
        if(svg?.complete&&svg.naturalWidth)ctx.drawImage(svg,-9,-9,18,18);
        ctx.restore();
      }
      if(!row.civic)statusArt(ctx,p,row,c,timestamp);
      else if(row.state==='失联'||row.selected){
        ctx.strokeStyle=row.state==='失联'?'#9099a8a0':c;
        ctx.lineWidth=1.2;
        ctx.setLineDash(row.state==='失联'?[3,4]:[]);
        ctx.beginPath();
        ctx.arc(p.x,p.y,row.state==='失联'?14+2*Math.sin(timestamp/600):11,0,2*Math.PI);
        ctx.stroke();
      }
      animated ||= ratio<1||(row.civic?row.state==='失联':row.state!=='已降落');
    }
    for(const [text,x,y,tint] of tags)tag(ctx,text,x,y,tint,fill,ink,placed,size);
    if(animated)frame=requestAnimationFrame(paint);
  }
  function fly(root, enabled, toGeo, bridge, panBy) {
    control=enabled;
    position=enabled?position:null;
    inputTarget=toGeo;inputBridge=bridge;root.flightPanBy=panBy;
    if(root.dataset.flightListening)return;
    root.dataset.flightListening='1';
    let pressed=false, right=null;
    function send() {
      if(busy||!position||!control)return;
      const point=position;
      position=null;busy=true;
      Promise.resolve(inputTarget(point)).then(value=>{
        if(control&&value)inputBridge.simInput('drag',value[0],value[1]);
      }).catch(()=>{}).finally(()=>{busy=false;if(position)setTimeout(send,75)});
    }
    const put=e=>{
      if(!control||!pressed||e.target.closest('button,a,.leaflet-control,[role=button]'))return;
      e.preventDefault();e.stopImmediatePropagation();
      const box=root.getBoundingClientRect();
      position={x:e.clientX-box.left,y:e.clientY-box.top};
      send();
    };
    root.addEventListener('pointerdown',e=>{
      if(!control)return;
      if(e.button===2) {
        right=[e.clientX,e.clientY];e.preventDefault();e.stopImmediatePropagation();
      } else if(e.button===0) {
        pressed=true;put(e);
      }
    },true);
    root.addEventListener('pointermove',e=>{
      if(control&&right&&root.flightPanBy){
        root.flightPanBy(e.clientX-right[0],e.clientY-right[1]);
        right=[e.clientX,e.clientY];e.preventDefault();e.stopImmediatePropagation();
      } else put(e);
    },true);
    root.addEventListener('contextmenu',e=>{if(control){e.preventDefault();e.stopImmediatePropagation()}},true);
    window.addEventListener('pointerup',()=>{pressed=false;right=null},true);
    root.addEventListener('wheel',e=>{
      if(!control)return;
      e.preventDefault();e.stopImmediatePropagation();
      inputBridge.simInput('height',e.deltaY<0?5:-5,0);
    },{capture:true,passive:false});
    document.addEventListener('keydown',e=>{
      if(!control||!['w','s','W','S',' '].includes(e.key))return;
      e.preventDefault();inputBridge.simInput(e.key===' '?'pause':'height',e.key.toLowerCase()==='w'?5:-5,0);
    });
  }
  window.TrainingMap={update,paint,fly,civicRole};
})();
