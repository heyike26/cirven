'use strict';
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const directory=path.join(__dirname,'../../packages/branding');
const product=require('../../packages/branding/product.json');
const DISPLAY_NAME=product.display_name,SHORT_NAME=product.short_name,REPOSITORY=product.repository;
if(typeof DISPLAY_NAME!=='string'||typeof SHORT_NAME!=='string'||!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(REPOSITORY))throw new Error('A canonical local product brand is required');
const digest=value=>createHash('sha256').update(value).digest('hex');
function bounded(name,limit){const file=path.join(directory,name),stat=fs.lstatSync(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.size<1||stat.size>limit)throw Error('A bounded local brand asset is required');return fs.readFileSync(file);}
const manifest=JSON.parse(bounded('icon-assets.json',8192));
const roles=Object.freeze({mark16:['icon-16.png',16],mark24:['icon-24.png',24],mark32:['icon-32.png',32],mark256:['icon-256.png',256],tray32:['tray-32.png',32],app256:['app-256.png',256]});
if(manifest.schema_version!==1||Object.keys(manifest.assets??{}).sort().join()!==Object.keys(roles).sort().join())throw Error('The brand asset manifest is incomplete');
function verified(meta,name,limit){if(meta?.path!==name||!Number.isSafeInteger(meta.bytes)||!/^[a-f0-9]{64}$/.test(meta.sha256??''))throw Error('A bound brand asset is required');const bytes=bounded(name,limit);if(bytes.length!==meta.bytes||digest(bytes)!==meta.sha256)throw Error('Brand asset integrity mismatch');return bytes;}
const svg=verified(manifest.source,'icon.svg',32768),mono=verified(manifest.monochrome,'icon-monochrome.svg',32768);
const ICON_SVG=svg.toString('utf8').trim(),ICON_MONO_SVG=mono.toString('utf8').trim();
if(!ICON_SVG.includes('<svg')||!ICON_MONO_SVG.includes('<svg')||/<(?:script|foreignObject|image|text)\b|<!ENTITY|<!DOCTYPE/i.test(ICON_SVG+ICON_MONO_SVG))throw Error('A local vector brand is required');
const pngs=new Map();
for(const [role,[name,size]]of Object.entries(roles)){
  const meta=manifest.assets[role],bytes=verified(meta,name,512*1024);
  if(meta.width!==size||meta.height!==size||bytes.length<33||!bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))||bytes.readUInt32BE(16)!==size||bytes.readUInt32BE(20)!==size||bytes[24]!==8||bytes[25]!==6)throw Error('A bounded RGBA brand image is required');
  pngs.set(role,bytes);
}
function brandPNG(role='app256'){if(!pngs.has(role))throw Error('Unknown brand image role');return Buffer.from(pngs.get(role));}
function freeze(value){if(value&&typeof value==='object'){for(const item of Object.values(value))freeze(item);Object.freeze(value);}return value;}
const BRAND_ASSETS=freeze(JSON.parse(JSON.stringify(manifest)));
const WINDOW_TITLES=Object.freeze({main:DISPLAY_NAME,orb:SHORT_NAME+' 快捷助手',quick:SHORT_NAME+' 快捷问答',annotation:SHORT_NAME+' 页面批注'});
module.exports={DISPLAY_NAME,SHORT_NAME,REPOSITORY,WINDOW_TITLES,ICON_SVG,ICON_MONO_SVG,BRAND_ASSETS,brandPNG};
