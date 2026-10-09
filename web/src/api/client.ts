import type { components } from './generated';

export type Preset = components['schemas']['Preset-Output'];
export type Block = components['schemas']['Block-Output'];
export type Config = components['schemas']['StudioConfig-Output'];
export type PromptInspection = components['schemas']['PromptInspection'];
export type SkillInfo = components['schemas']['SkillInfo'];
export type Run = components['schemas']['RunView'];
export type ContextView = components['schemas']['ContextView'];
export type RunRequest = components['schemas']['PreviewRequest'];
export type Session = components['schemas']['SessionSummary'];
export type Folder = components['schemas']['FolderView'];
export type Project = components['schemas']['ProjectView'];
export type FolderSelection = components['schemas']['FolderSelection'];
export type SessionUpdate = components['schemas']['SessionUpdate'];
export type Bootstrap = components['schemas']['Bootstrap'];
export type Tool = components['schemas']['ToolDefinition'];
export type RunEvent = components['schemas']['RunEvent'];
export type ResumeRequest = components['schemas']['ResumeRequest'];
export type Trace = components['schemas']['TraceView'];
export type WorkspaceView = components['schemas']['WorkspaceView'];
export type MemoryDocument = components['schemas']['MemoryDocument'];
export type SkillDocument = components['schemas']['SkillDocument'];
export type ProviderProfile = components['schemas']['ProviderProfile-Output'];
export type ModelChoice = components['schemas']['ModelChoice-Input'];
export type CredentialStatus = components['schemas']['CredentialStatus'];
export type WorkspaceSkills = components['schemas']['WorkspaceSkills'];
export type Grant = components['schemas']['Grant-Output'];
export type CommandRule = components['schemas']['CommandRule'];
export type ShellHook = components['schemas']['ShellHook-Output'];
export type Attachment = components['schemas']['Attachment'];
export type Eval = components['schemas']['Eval'];
export type EvalCase = components['schemas']['EvalCase-Output'];
export type EvalVariant = components['schemas']['EvalVariant-Output'];
export type EvalCheck = components['schemas']['EvalCheck-Output'];
export type EvalBatch = components['schemas']['EvalBatchView'];
export type WorkspaceAccess = 'read-only' | 'ask' | 'workspace-write';
/** What the composer sends with each Run: the model and the workspace access. */
export type RunChoice = { model: ModelChoice; access: WorkspaceAccess };

/** The pseudo provider ID of the offline simulated model. */
export const SIMULATED = 'simulated';

/** Carry the server error code so conflicts and validation failures remain identifiable. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

/** Call the local API directly; the server validates browser origins. */
export async function request<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  key?: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    signal,
    method,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(key ? { 'Idempotency-Key': key } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return undefined as T;
  return response.json();
}

/** Turn an error response into an ApiError with the server's code and explanation. */
async function failure(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => ({}));
  const message =
    payload.error?.message ||
    (Array.isArray(payload.detail)
      ? payload.detail
          .map((item: { loc: string[]; msg: string }) => `${item.loc.join('.')}: ${item.msg}`)
          .join('; ')
      : `Request failed (${response.status}).`);
  return new ApiError(message, response.status, payload.error?.code || 'request_error');
}

/** Send a file as the raw request body, such as an image to attach to a task. */
export async function upload<T>(path: string, file: Blob): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!response.ok) throw await failure(response);
  return response.json();
}

/** Convert arbitrary caught failures into an actionable display string. */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The operation could not be completed.';
}

/** Address ordinary files in the selected project, retaining any explicit query options. */
export function workspacePath(path: string, projectId: string): string {
  return `/workspace${path}${projectId ? `${path.includes('?') ? '&' : '?'}project_id=${encodeURIComponent(projectId)}` : ''}`;
}

/** Check execution states shared by controls, polling, and event-stream cleanup. */
export function terminal(status: string): boolean {
  return ['succeeded', 'failed', 'cancelled'].includes(status);
}
