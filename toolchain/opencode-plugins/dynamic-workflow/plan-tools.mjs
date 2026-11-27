import {z} from './vendor/zod/index.js';

const id=z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/);
const ref=z.object({id,revision:z.number().int().min(1),sha256:z.string().regex(/^[a-f0-9]{64}$/)}).strict();
const source=ref.extend({kind:z.string().min(1).max(100).optional(),project_id:id.optional()}).strict();
const pin=z.discriminatedUnion('mode',[
  z.object({mode:z.literal('AUTO'),value:z.null()}).strict(),z.object({mode:z.literal('MANUAL'),value:z.string().min(1).max(200)}).strict()
]);
const plan=z.object({
  title:z.string().min(1).max(500),goal:z.string().min(1).max(50000),
  nodes:z.array(z.object({id,title:z.string().min(1).max(500),description:z.string().max(100000).optional(),
    depends_on:z.array(id).max(512).optional(),acceptance:z.array(z.object({id,text:z.string().min(1).max(20000),method:z.string().max(20000).optional()}).strict()).max(128).optional(),
    source_refs:z.array(source).max(128).optional(),question_ids:z.array(id).max(512).optional(),
    agent_policy:z.object({model:pin,depth:pin}).strict().optional()}).strict()).max(512),
  questions:z.array(z.object({id,text:z.string().min(1).max(20000),blocking:z.boolean().optional(),source_refs:z.array(source).max(128).optional()}).strict()).max(512).optional(),
  external_refs:z.array(source).max(128).optional()
}).strict();
const args={operation_id:id,expected_ref:ref,plan,reason:z.string().min(1).max(4000)};
const schema=z.object(args).strict();

export function planTools(client){
  return {
    eeas_workflow_replan:{description:'Propose or revise this existing workflow through the authorized versioned MODEL API. Preserve user pins and confirmations. This tool does not answer for the user, verify acceptance or execute tasks. Use a stable operation_id; UNKNOWN must be inspected, never resubmitted under a new id.',args,
      execute:async value=>JSON.stringify(await client.replan(schema.parse(value)))},
    eeas_workflow_plan_operation:{description:'Inspect the original plan-write operation after an uncertain result. Read only; never retries a POST, executes a task or changes user locks.',args:{operation_id:id},
      execute:async value=>JSON.stringify(await client.inspect(z.object({operation_id:id}).strict().parse(value).operation_id))}
  };
}
