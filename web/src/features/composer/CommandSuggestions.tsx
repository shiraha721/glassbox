import { SquareSlash } from 'lucide-react';
import type { CommandInfo } from './commands';

/** Offer matching commands under the task input; Tab or a click completes the name. */
export function CommandSuggestions({
  matches,
  onPick,
}: {
  matches: CommandInfo[];
  onPick: (command: CommandInfo) => void;
}) {
  if (!matches.length) return null;
  return (
    <ul className="command-suggestions" aria-label="Commands">
      {matches.map((command) => (
        <li key={command.name}>
          <button type="button" onClick={() => onPick(command)}>
            <SquareSlash size={13} />
            <code>/{command.name}</code>
            {command.argument_hint && <span className="subtle">{command.argument_hint}</span>}
            {command.description && <span className="subtle">— {command.description}</span>}
            {command.scope === 'global' && <span className="badge">global</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}
