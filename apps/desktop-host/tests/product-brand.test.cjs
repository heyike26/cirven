'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{createRequire}=require('node:module'),{createHash}=require('node:crypto');
const filename=path.resolve(__dirname,'../product-brand.cjs'),directory=path.resolve(__dirname,'../../../packages/branding');
function load(transform=(name,data)=>data,statTransform=(_name,value)=>value){
  const module={exports:{}},localRequire=createRequire(filename),required=[];
  const boundary={...fs,readFileSync:(name,...args)=>transform(path.basename(name),fs.readFileSync(name,...args)),lstatSync:(name,...args)=>statTransform(path.basename(name),fs.lstatSync(name,...args))};
  const req=name=>{required.push(name);return name==='node:fs'?boundary:localRequire(name);};
  vm.runInNewContext(`(function(require,module,exports,__dirname){${fs.readFileSync(filename,'utf8')}\n})`,{Buffer})(req,module,module.exports,path.dirname(filename));
  return {brand:module.exports,required};
}
test('runtime loads the complete hash-bound local asset closure without a renderer, subprocess or network',()=>{
  const {brand,required}=load(),product=require('../../../packages/branding/product.json');
  assert.equal(brand.DISPLAY_NAME,product.display_name);assert.equal(brand.REPOSITORY,product.repository);
  for(const [role,record]of Object.entries(brand.BRAND_ASSETS.assets)){
    const image=brand.brandPNG(role);assert.deepEqual(image,fs.readFileSync(path.join(directory,record.path)));
    assert.equal(createHash('sha256').update(image).digest('hex'),record.sha256);
    assert.equal(image.readUInt32BE(16),record.width);assert.equal(image.readUInt32BE(20),record.height);
  }
  assert.deepEqual(required,['node:fs','node:path','node:crypto','../../packages/branding/product.json']);
  const first=brand.brandPNG('tray32');first.fill(0);assert.equal(brand.brandPNG('tray32')[0],137);
  assert.equal(Object.isFrozen(brand.BRAND_ASSETS.assets.tray32),true);assert.throws(()=>brand.brandPNG('../icon.svg'),/Unknown/);
});
test('changed SVG or PNG bytes fail before a tray can use an unbound image',()=>{
  for(const name of ['icon.svg','icon-monochrome.svg','tray-32.png'])assert.throws(()=>load((file,data)=>file===name?Buffer.concat([data,Buffer.from('fixture drift')]):data),/integrity mismatch/);
});
test('rebound malformed PNG dimensions and symbolic local image are refused',()=>{
  const invalid=Buffer.from(fs.readFileSync(path.join(directory,'tray-32.png')));invalid.writeUInt32BE(999,16);
  assert.throws(()=>load((file,data)=>{
    if(file==='tray-32.png')return invalid;
    if(file==='icon-assets.json'){const manifest=JSON.parse(data);manifest.assets.tray32.sha256=createHash('sha256').update(invalid).digest('hex');return Buffer.from(JSON.stringify(manifest));}
    return data;
  }),/RGBA/);
  assert.throws(()=>load(undefined,(name,stat)=>name==='tray-32.png'?{isFile:()=>true,isSymbolicLink:()=>true,size:stat.size}:stat),/bounded local/);
});
