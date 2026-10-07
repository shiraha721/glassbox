/** Render unified diff text with added, removed and hunk lines marked. */
export function Diff({ text }: { text: string }) {
  return (
    <pre className="patch-diff">
      {text.split('\n').map((line, index) => (
        <span
          key={index}
          className={
            line.startsWith('+') && !line.startsWith('+++')
              ? 'diff-add'
              : line.startsWith('-') && !line.startsWith('---')
                ? 'diff-remove'
                : line.startsWith('@@')
                  ? 'diff-hunk'
                  : undefined
          }
        >
          {line + '\n'}
        </span>
      ))}
    </pre>
  );
}
