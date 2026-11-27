import type {Project} from './contracts';

const internalDescriptions=new Set(['eeas.original-agent-session.internal-project.v1','EEAS_INTERNAL_DESKTOP_RUNTIME_V1']);

export function userProjects<T extends Pick<Project,'id'|'description'>>(projects:readonly T[]):T[]{
 return projects.filter(project=>!internalDescriptions.has(project.description??''));
}

export function startupProject(projects:readonly Project[],savedId:string|null):Project|undefined{
 return savedId?userProjects(projects).find(project=>project.id===savedId):undefined;
}
