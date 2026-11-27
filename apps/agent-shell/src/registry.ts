import type { ActionDescriptor, ArtifactEnvelope, RendererRegistration, Scope } from './contracts';

const rendererKey = (type: string, version: number) => `${type}@${version}`;
/** Small owned Solid registry. Design reference: DSH SlotCore (MIT); no React runtime or new agent loop. */
export class ModuleRegistry {
  private renderers = new Map<string, RendererRegistration>();
  private actions = new Map<string, { descriptor: ActionDescriptor; invoke: (target: ArtifactEnvelope, input: unknown) => Promise<unknown> }>();
  private owners = new Map<string, Set<() => void>>();
  private listeners = new Set<() => void>();
  private revision = 0;
  readonly diagnostics:string[]=[];
  subscribe(listener: () => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  snapshot = () => this.revision;
  private changed() { this.revision++; for (const listener of [...this.listeners])try{listener();}catch(error){this.diagnostics.push(error instanceof Error?error.message:String(error));} }
  private owned(moduleId: string, release: () => void) {
    const disposers = this.owners.get(moduleId) ?? new Set<() => void>(); let active = true;
    const dispose = () => { if (!active) return; active = false; release(); disposers.delete(dispose); if (!disposers.size) this.owners.delete(moduleId); this.changed(); };
    disposers.add(dispose); this.owners.set(moduleId, disposers); return dispose;
  }
  registerRenderer(registration: RendererRegistration) {
    const { descriptor } = registration; const key = rendererKey(descriptor.type, descriptor.schemaVersion);
    if (!descriptor.moduleId || !/^[a-f0-9]{64}$/.test(descriptor.sourceHash) || !descriptor.version) throw new Error('模块需要固定来源和版本');
    if (this.renderers.has(key)) throw new Error(`Renderer ${key} 已有所有者`);
    this.renderers.set(key, registration);
    const dispose=this.owned(descriptor.moduleId, () => { if (this.renderers.get(key) === registration) this.renderers.delete(key); });this.changed();return dispose;
  }
  registerAction(descriptor: ActionDescriptor, invoke: (target: ArtifactEnvelope, input: unknown) => Promise<unknown>) {
    if (this.actions.has(descriptor.actionId)) throw new Error(`Action ${descriptor.actionId} 已有所有者`);
    const registration = { descriptor, invoke }; this.actions.set(descriptor.actionId, registration);
    const dispose=this.owned(descriptor.moduleId, () => { if (this.actions.get(descriptor.actionId) === registration) this.actions.delete(descriptor.actionId); });this.changed();return dispose;
  }
  renderer(artifact: ArtifactEnvelope) { return this.renderers.get(rendererKey(artifact.type, artifact.schemaVersion)); }
  inventory() { return [...this.renderers.values()].map(value => value.descriptor); }
  async dispatch(actionId: string, target: ArtifactEnvelope, input: unknown, activeScope: Scope) {
    const registration = this.actions.get(actionId); if (!registration) throw new Error('操作已卸载');
    const descriptor = registration.descriptor;
    if (!['IMPLEMENTED', 'QUALIFIED', 'ACTIVE'].includes(descriptor.availability)) throw new Error('此能力尚不可执行');
    if (descriptor.scope!=='global'&&target.scope.projectId !== activeScope.projectId) throw new Error('对象不属于当前项目');
    const identity = descriptor.scope === 'workspace' ? 'workspaceId' : descriptor.scope === 'session' ? 'sessionId' : descriptor.scope === 'run' ? 'runId' : null;
    if (identity && (!activeScope[identity] || target.scope[identity] !== activeScope[identity])) throw new Error('对象作用域已变化');
    return registration.invoke(target, input);
  }
  unload(moduleId: string) { for (const dispose of [...this.owners.get(moduleId) ?? []]) dispose(); }
}
export class NavigationOwner {
  private controller = new AbortController(); private epoch = 0;
  capture() { const epoch=this.epoch; return () => epoch===this.epoch&&!this.controller.signal.aborted; }
  begin() { this.controller.abort(); this.controller = new AbortController(); const epoch = ++this.epoch; return { signal: this.controller.signal, current: () => epoch === this.epoch && !this.controller.signal.aborted }; }
  dispose() { ++this.epoch; this.controller.abort(); }
}
