'use strict';
const fs=require('node:fs');
const net=require('node:net');
const path=require('node:path');

function savedPort(file) {
  if(!fs.existsSync(file))return null;
  const stat=fs.lstatSync(file);
  if(!stat.isFile()||stat.isSymbolicLink()||stat.size>1024)throw new Error('本地服务端口记录无效；工作数据已保留。');
  const value=JSON.parse(fs.readFileSync(file,'utf8'));
  if(!value||Object.keys(value).sort().join(',')!=='port,schema'||value.schema!=='eeas.controller-port.v1'||
      !Number.isSafeInteger(value.port)||value.port<1024||value.port>65535)throw new Error('本地服务端口记录无效；工作数据已保留。');
  return value.port;
}

async function controllerPort(dataDir) {
  const file=path.join(dataDir,'controller-port.json'),preferred=savedPort(file);
  const port=await new Promise((resolve,reject)=>{
    const probe=net.createServer();
    probe.once('error',error=>reject(new Error(error.code==='EADDRINUSE'
      ? 'EEAS 的本地服务端口正在被占用。请关闭旧实例或占用程序后重试；工作数据已保留。'
      : '无法使用 EEAS 的本地服务端口；工作数据已保留。')));
    probe.listen(preferred??0,'127.0.0.1',()=>{
      const actual=probe.address().port;
      probe.close(error=>error?reject(error):resolve(actual));
    });
  });
  if(preferred===null)fs.writeFileSync(file,JSON.stringify({schema:'eeas.controller-port.v1',port})+'\n',{flag:'wx',mode:0o600});
  return port;
}
module.exports={controllerPort,savedPort};
