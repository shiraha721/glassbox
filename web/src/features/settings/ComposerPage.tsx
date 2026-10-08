import { setSendKey, useSendKey, type SendKey } from '../composer/sendKey';

const choices: { value: SendKey; label: string; detail: string }[] = [
  {
    value: 'enter',
    label: 'Enter runs the task',
    detail: 'Shift+Enter starts a new line. Ctrl+Enter also runs.',
  },
  {
    value: 'mod-enter',
    label: 'Ctrl+Enter runs the task',
    detail: 'Enter starts a new line, which suits long multi-line tasks. Cmd+Enter on macOS.',
  },
];

/** Per-browser composer preferences; they apply at once and are not part of saved settings. */
export function ComposerPage() {
  const sendKey = useSendKey();
  return (
    <>
      <p className="page-intro">
        How the message box behaves in this browser. Changes apply immediately; they are not saved
        with the studio settings.
      </p>
      <fieldset className="choice-group">
        <legend>Send key</legend>
        {choices.map((choice) => (
          <label key={choice.value} className="choice">
            <input
              type="radio"
              name="send-key"
              value={choice.value}
              checked={sendKey === choice.value}
              onChange={() => setSendKey(choice.value)}
            />
            <span>
              <strong>{choice.label}</strong>
              <small>{choice.detail}</small>
            </span>
          </label>
        ))}
      </fieldset>
    </>
  );
}
