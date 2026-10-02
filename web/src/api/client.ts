import type { components } from './generated';

export type Preset = components['schemas']['Preset-Output'];
export type Block = components['schemas']['Block-Output'];
export type Config = components['schemas']['StudioConfig-Output'];
export type Preview = components['schemas']['Preview'];
export type Run = components['schemas']['RunView'];
export type Session = components['schemas']['SessionView'];
export type Bootstrap = components['schemas']['Bootstrap'];
export type Tool = components['schemas']['ToolDefinition'];
export type RunEvent = components['schemas']['RunEvent'];
export type ResumeRequest = components['schemas']['ResumeRequest'];
export type Trace = components['schemas']['TraceView'];
export type WorkspaceView = components['schemas']['WorkspaceView'];
export type MemoryDocument = components['schemas']['MemoryDocument'];
export type SkillDocument = components['schemas']['SkillDocument'];

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
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(key ? { 'Idempotency-Key': key } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message =
      payload.error?.message ||
      (Array.isArray(payload.detail)
        ? payload.detail
            .map((item: { loc: string[]; msg: string }) => `${item.loc.join('.')}: ${item.msg}`)
            .join('; ')
        : `Request failed (${response.status}).`);
    throw new ApiError(message, response.status, payload.error?.code || 'request_error');
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

/** Convert arbitrary caught failures into an actionable display string. */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The operation could not be completed.';
}

/** Check execution states shared by controls, polling, and event-stream cleanup. */
export function terminal(status: string): boolean {
  return ['succeeded', 'failed', 'cancelled'].includes(status);
}
