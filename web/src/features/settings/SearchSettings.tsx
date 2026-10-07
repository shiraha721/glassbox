import { useQuery } from '@tanstack/react-query';
import { request, type Config, type CredentialStatus } from '../../api/client';
import type { components } from '../../api/generated';
import { KeyField } from './KeyField';

type Search = components['schemas']['SearchSettings-Output'];

const services: { value: Search['provider']; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'brave', label: 'Brave Search API' },
  { value: 'tavily', label: 'Tavily' },
  { value: 'searxng', label: 'SearXNG instance' },
];

/** Choose the service behind the web_search tool; its key is saved like provider keys. */
export function SearchSettings({
  draft,
  onChange,
}: {
  draft: Config;
  onChange: (config: Config) => void;
}) {
  const search: Search = draft.search ?? { provider: 'none', base_url: '', credential_ref: '' };
  const set = (changes: Partial<Search>) =>
    onChange({ ...draft, search: { ...search, ...changes } });
  const needsKey = search.provider === 'brave' || search.provider === 'tavily';
  const name = search.credential_ref.trim();
  const status = useQuery({
    queryKey: ['credentials', [name]],
    queryFn: () => request<CredentialStatus[]>(`/credentials?name=${encodeURIComponent(name)}`),
    enabled: needsKey && !!name,
  });
  return (
    <section className="settings-card" aria-label="Web search">
      <h3>Web search</h3>
      <p className="subtle">
        The web_search tool asks this service; read_url reads any public HTTPS page as text. Both
        send what the model writes off this computer, so they ask before each call unless you allow
        them above.
      </p>
      <div className="form-grid">
        <label>
          Search service
          <select
            value={search.provider}
            onChange={(event) => {
              const provider = event.target.value as Search['provider'];
              set({
                provider,
                credential_ref:
                  search.credential_ref ||
                  (provider === 'brave'
                    ? 'BRAVE_API_KEY'
                    : provider === 'tavily'
                      ? 'TAVILY_API_KEY'
                      : ''),
              });
            }}
          >
            {services.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {search.provider === 'searxng' && (
          <label>
            Instance URL
            <input
              value={search.base_url}
              placeholder="http://127.0.0.1:8888"
              onChange={(event) => set({ base_url: event.target.value })}
            />
          </label>
        )}
        {needsKey && (
          <label>
            Key variable name
            <input
              value={search.credential_ref}
              onChange={(event) => set({ credential_ref: event.target.value })}
            />
          </label>
        )}
      </div>
      {needsKey && name && (
        <KeyField
          name={name}
          status={status.data?.find((item) => item.name === name)}
          onChanged={() => status.refetch()}
        />
      )}
    </section>
  );
}
