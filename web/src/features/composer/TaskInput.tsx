import { useRef, useState, type ClipboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, SquareSlash } from 'lucide-react';
import { request, workspacePath, type WorkspaceView } from '../../api/client';
import { useCommands } from './commands';
import { sendsTask, type SendKey } from './sendKey';
import { applySuggestion, suggestionsFor, triggerAt } from './suggestions';

/** The workspace's Skill catalog, loaded once a `$` mention starts. */
function useSkillCatalog(projectId: string, enabled: boolean) {
  const query = useQuery({
    queryKey: ['workspace', projectId],
    queryFn: () => request<WorkspaceView>(workspacePath('', projectId)),
    enabled,
    refetchOnWindowFocus: false,
  });
  return query.data?.skills ?? [];
}

/** The task box: runs on the chosen send key and completes `/command` and `$skill` names.
 *
 * While the suggestion list is open, arrows move through it, Enter or Tab inserts the
 * highlighted name and Escape closes it; otherwise the send key runs the task. */
export function TaskInput({
  value,
  onChange,
  projectId,
  sendKey,
  disabled,
  placeholder,
  onSend,
  onPaste,
}: {
  value: string;
  onChange: (value: string) => void;
  projectId: string;
  sendKey: SendKey;
  disabled: boolean;
  placeholder: string;
  onSend: () => void;
  onPaste: (event: ClipboardEvent<HTMLTextAreaElement>) => void;
}) {
  const box = useRef<HTMLTextAreaElement>(null);
  const [caret, setCaret] = useState(0);
  const [highlight, setHighlight] = useState(0);
  const [dismissed, setDismissed] = useState('');
  const trigger = triggerAt(value, Math.min(caret, value.length));
  const commands = useCommands(projectId, trigger?.kind === 'command');
  const skills = useSkillCatalog(projectId, trigger?.kind === 'skill');
  const key = trigger ? `${trigger.kind}:${trigger.start}:${trigger.query}` : '';
  const options = key && key !== dismissed ? suggestionsFor(trigger, commands, skills) : [];
  const selected = Math.min(highlight, Math.max(options.length - 1, 0));
  const listId = 'task-suggestions';

  const track = () => setCaret(box.current?.selectionStart ?? value.length);
  const pick = (index: number) => {
    if (!trigger || !options[index]) return;
    const next = applySuggestion(value, trigger, options[index].name);
    onChange(next.text);
    setCaret(next.caret);
    setHighlight(0);
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(next.caret, next.caret);
    });
  };

  return (
    <>
      <textarea
        ref={box}
        id="task-input"
        value={value}
        placeholder={placeholder}
        rows={2}
        disabled={disabled}
        aria-autocomplete="list"
        aria-controls={options.length ? listId : undefined}
        aria-activedescendant={options.length ? `${listId}-${selected}` : undefined}
        onChange={(event) => {
          onChange(event.target.value);
          setCaret(event.target.selectionStart);
          setHighlight(0);
        }}
        onSelect={track}
        onClick={track}
        onPaste={onPaste}
        onKeyDown={(event) => {
          const composing = event.nativeEvent.isComposing || event.keyCode === 229;
          if (options.length && !composing) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              const step = event.key === 'ArrowDown' ? 1 : -1;
              setHighlight((selected + step + options.length) % options.length);
              return;
            }
            if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
              event.preventDefault();
              pick(selected);
              return;
            }
            if (event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
              setDismissed(key);
              return;
            }
          }
          const { key: pressed, shiftKey, altKey, ctrlKey, metaKey } = event;
          const press = {
            key: pressed,
            shiftKey,
            altKey,
            ctrlKey,
            metaKey,
            isComposing: composing,
          };
          if (sendsTask(press, sendKey)) {
            event.preventDefault();
            onSend();
          }
        }}
      />
      {options.length > 0 && (
        <ul
          id={listId}
          className="task-suggestions"
          role="listbox"
          aria-label={trigger?.kind === 'command' ? 'Commands' : 'Skills'}
        >
          {options.map((option, index) => (
            <li
              key={option.name}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === selected}
              // Keep focus in the task box so typing continues after a click.
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setHighlight(index)}
              onClick={() => pick(index)}
            >
              {option.kind === 'command' ? <SquareSlash size={13} /> : <BookOpen size={13} />}
              <code>
                {option.kind === 'command' ? '/' : '$'}
                {option.name}
              </code>
              {option.hint && <span className="subtle">{option.hint}</span>}
              {option.description && <span className="subtle">{option.description}</span>}
              {option.scope !== 'project' && <span className="badge">{option.scope}</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
