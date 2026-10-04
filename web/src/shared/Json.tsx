/** Render structured evidence as text without depending on an inspection feature or executing HTML. */
export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
