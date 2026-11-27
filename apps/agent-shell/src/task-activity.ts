import type {SupervisorTaskKey} from './global-supervisor';

// JSON property insertion order is not an identity boundary.
export function sameSupervisedTask(a:unknown,b:SupervisorTaskKey):boolean{
  if(!a||typeof a!=='object'||Array.isArray(a))return false;
  const key=a as Partial<SupervisorTaskKey>;
  return key.kind===b.kind&&key.object_id===b.object_id&&key.generation===b.generation&&
    (key.project_id??null)===(b.project_id??null)&&(key.operation_id??null)===(b.operation_id??null);
}
