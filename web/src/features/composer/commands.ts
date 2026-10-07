import { useQuery } from '@tanstack/react-query';
import { request, workspacePath } from '../../api/client';
import type { components } from '../../api/generated';

export type CommandInfo = components['schemas']['CommandInfo'];

/** Commands whose name starts with what was typed after a leading slash, before any space. */
export function matchingCommands(input: string, commands: CommandInfo[]): CommandInfo[] {
  const typed = /^\/([a-z0-9-]*)$/.exec(input);
  if (!typed) return [];
  return commands.filter((command) => command.name.startsWith(typed[1])).slice(0, 8);
}

/** Load the selected workspace's custom slash commands once the input starts with a slash. */
export function useCommands(projectId: string, enabled: boolean): CommandInfo[] {
  const query = useQuery({
    queryKey: ['commands', projectId],
    queryFn: () => request<CommandInfo[]>(workspacePath('/commands', projectId)),
    enabled,
    staleTime: 10_000,
  });
  return query.data ?? [];
}
