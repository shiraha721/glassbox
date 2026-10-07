import { Eye, ShieldAlert, SquarePen } from 'lucide-react';
import {
  SIMULATED,
  type Config,
  type ModelChoice,
  type Tool,
  type WorkspaceAccess,
} from '../../api/client';

export const effortLabels: Record<string, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'Extra high',
  max: 'Max',
};

export const accessOptions = [
  {
    value: 'read-only',
    label: 'Read only',
    description: 'Reads files and runs commands without changing the workspace.',
    icon: Eye,
  },
  {
    value: 'ask',
    label: 'Ask before changes',
    description: 'File edits, commands and MCP tools wait for your approval.',
    icon: ShieldAlert,
  },
  {
    value: 'workspace-write',
    label: 'Workspace write',
    description: 'Tools run as permitted in Settings → Tools.',
    icon: SquarePen,
  },
] as const;

// Mirrors the server: tools that change files or run code. MCP and trusted local tools that
// declare workspace writes are added from the catalog.
const changeTools = new Set([
  'write_text',
  'edit_text',
  'apply_patch',
  'save_memory',
  'save_skill',
  'fetch_url',
  'bash',
  'powershell',
  'start_process',
  'send_process_input',
]);

/** Show a model choice compactly, e.g. "gpt-5 · High". */
export function modelLabel(
  choice: Pick<ModelChoice, 'provider_id' | 'model' | 'reasoning_effort'>,
) {
  if (choice.provider_id === SIMULATED) return 'Simulated model';
  if (!choice.model) return 'Choose a model';
  const effort = choice.reasoning_effort ? ` · ${effortLabels[choice.reasoning_effort]}` : '';
  return choice.model + effort;
}

/** Apply a Run's workspace access to the saved permissions, as the server does. */
export function effectiveConfig(config: Config, tools: Tool[], access: WorkspaceAccess): Config {
  const changes = (name: string) => {
    const tool = tools.find((item) => item.name === name);
    return changeTools.has(name) || !!tool?.server_id || !!tool?.writes_workspace;
  };
  return {
    ...config,
    sandbox_mode: access === 'read-only' ? 'read-only' : 'workspace-write',
    tools:
      access === 'ask'
        ? Object.fromEntries(
            Object.entries(config.tools).map(([name, permission]) => [
              name,
              permission === 'allow' && changes(name) ? 'ask' : permission,
            ]),
          )
        : config.tools,
  };
}
