'use strict';

// Same-process evidence from the host's validation boundary. Transport errors,
// serialized properties and remote NOT_DISPATCHED claims cannot mint this type.
class QuickNotSubmittedError extends Error {
  constructor(operationId,draftRef,cause) {
    super(cause.message,{cause});
    this.name='QuickNotSubmittedError';
    this.operationId=operationId;
    this.draftRef=Object.freeze({...draftRef});
  }
  matches(operationId,draft) {
    return this.operationId===operationId&&this.draftRef.id===draft.id&&this.draftRef.revision===draft.revision;
  }
}
module.exports={QuickNotSubmittedError};
