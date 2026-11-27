// Zero-model actual plugin/bridge fixture. The Python test supplies original SDK proof.
import {pathToFileURL} from 'node:url'
import readline from 'node:readline'
const {default:plugin}=await import(pathToFileURL(process.argv[2]).href)
const tools=await plugin({directory:process.argv[3]})
process.stdout.write('PLUGIN_READY\n')
const input=readline.createInterface({input:process.stdin,crlfDelay:Infinity})
for await(const line of input){
  const value=JSON.parse(line)
  try { const result=await tools.tool.eeas_native_message_call.execute(value.args,value.ctx);process.stdout.write(JSON.stringify({ok:true,result})+'\n') }
  catch(error){process.stdout.write(JSON.stringify({ok:false,error:error.message})+'\n')}
  break
}
input.close()
