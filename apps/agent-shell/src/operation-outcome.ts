export type OperationState={status:string;execution?:{execution_status:string}};

/** Domain terminal states outrank a stale RUNNING observation. A native
 * uncertain outcome also corrects legacy parent FAILED views, without writes. */
export function operationStatus(value:OperationState):string{
  if(value.status==='COMPLETED')return value.status;
  const native=value.execution?.execution_status;
  if(['UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED'].includes(native??''))return 'UNKNOWN_OUTCOME';
  if(['FAILED','CANCELLED','UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED'].includes(value.status))return value.status;
  return native??value.status;
}

export function operationRunning(value:OperationState):boolean{
  return ['REQUESTING','PROCESSING','QUEUED','RUNNING','CANCEL_REQUESTED'].includes(operationStatus(value));
}
