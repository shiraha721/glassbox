import { useQuery } from '@tanstack/react-query';
import { request, workspacePath } from '../../api/client';
import type { components } from '../../api/generated';

export type CommandInfo = components['schemas']['CommandInfo'];

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
