'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),{inflateSync}=require('node:zlib');
const {BackgroundPresence,trayIconPNG}=require('../background-presence.cjs');
test('recovery icon is the deterministic antialiased local PNG with readable light/dark contrast',()=>{
  const png=trayIconPNG();assert.deepEqual(png,trayIconPNG());assert.deepEqual([...png.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  assert.equal(png.readUInt32BE(16),32);assert.equal(png.readUInt32BE(20),32);
  let offset=8,rows;while(offset<png.length){const length=png.readUInt32BE(offset),type=png.toString('ascii',offset+4,offset+8);if(type==='IDAT')rows=inflateSync(png.subarray(offset+8,offset+8+length));offset+=12+length;}
  assert.equal(rows.length,32*129);let opaque=0,clear=0,dark=0,light=0,coolSilver=0;
  for(let y=0;y<32;y++)for(let x=0;x<32;x++){const n=y*129+1+x*4;rows[n+3]?opaque++:clear++;if(rows[n+3]>240){if(rows[n]<90)dark++;if(rows[n]>220)light++;if(rows[n]>=100&&rows[n]<=200&&rows[n+2]-rows[n]>=10)coolSilver++;}}
  assert.ok(opaque>500&&clear>50);assert.ok(dark>100&&light>200&&coolSilver>80,'graphite mark, cool-silver sweep and silver plate each remain visible');assert.equal(rows[4],0);
});
test('empty native image fails before creating a tray or a hidden unrecoverable window lifecycle',()=>{
  let created=0;assert.throws(()=>new BackgroundPresence({nativeImage:{createFromBuffer:()=>({isEmpty:()=>true})},Tray:class{constructor(){created++;}}}),/托盘图标/);
  assert.equal(created,0);
});

test('canonical asset hashes and brand drive the actual tray image and reachable restore/exit menu',async()=>{
  const fs=require('node:fs'),path=require('node:path'),{EventEmitter}=require('node:events');
  const brand=require('../product-brand.cjs'),product=require('../../../packages/branding/product.json');
  assert.equal(brand.DISPLAY_NAME,product.display_name);assert.equal(brand.SHORT_NAME,product.short_name);assert.equal(brand.REPOSITORY,product.repository);
  assert.equal(brand.ICON_SVG,fs.readFileSync(path.resolve(__dirname,'../../../packages/branding/icon.svg'),'utf8').trim());
  const png=trayIconPNG(),manifest=brand.BRAND_ASSETS;
  assert.deepEqual(png,fs.readFileSync(path.resolve(__dirname,'../../../packages/branding',manifest.assets.tray32.path)));
  assert.equal(require('node:crypto').createHash('sha256').update(png).digest('hex'),manifest.assets.tray32.sha256);
  const window=new EventEmitter();window.isDestroyed=()=>false;window.isVisible=()=>true;window.isMinimized=()=>false;
  let tray,restores=0,exits=0;
  class Tray extends EventEmitter{constructor(image){super();this.image=image;tray=this;}setToolTip(value){this.tooltip=value;}setContextMenu(value){this.menu=value;}destroy(){this.destroyed=true;}}
  const presence=new BackgroundPresence({Tray,Menu:{buildFromTemplate:items=>items},nativeImage:{createFromBuffer:value=>({isEmpty:()=>false,png:value})},getWindow:()=>window,getOrb:()=>null,reveal:()=>restores++,onExit:async()=>exits++});
  assert.match(tray.tooltip,new RegExp(product.display_name));assert.equal(tray.menu[0].label,'打开 '+product.display_name);assert.equal(tray.menu[2].label,'退出 '+product.display_name);
  tray.menu[0].click();tray.menu[2].click();await Promise.resolve();await Promise.resolve();assert.equal(restores,1);assert.equal(exits,1);presence.dispose();assert.equal(tray.destroyed,true);
});

test('tray language changes the same native recovery menu without restoring, quitting or changing background state',()=>{
  const {EventEmitter}=require('node:events');let tray,restores=0,exits=0;
  const own=new EventEmitter();own.isDestroyed=()=>false;own.isVisible=()=>false;own.isMinimized=()=>false;
  class Tray extends EventEmitter{constructor(){super();tray=this;}setToolTip(value){this.tooltip=value;}setContextMenu(menu){this.menu=menu;}destroy(){}}
  const presence=new BackgroundPresence({Tray,Menu:{buildFromTemplate:x=>x},nativeImage:{createFromBuffer:()=>({isEmpty:()=>false})},getWindow:()=>own,getOrb:()=>null,reveal:()=>restores++,onExit:()=>exits++});
  presence.userBackground=true;presence.setLocale('en');assert.equal(tray.menu[0].label,'Open Cirven');assert.equal(tray.menu[2].label,'Exit Cirven');assert.match(tray.tooltip,/Right-click/);
  assert.equal(presence.userBackground,true);assert.equal(restores,0);assert.equal(exits,0);presence.setLocale('zh-CN');assert.match(tray.menu[0].label,/打开/);presence.dispose();
});
