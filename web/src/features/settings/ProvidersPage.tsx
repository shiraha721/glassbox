import { useQuery } from '@tanstack/react-query';
import { Plus, Trash2 } from 'lucide-react';
import {
  request,
  SIMULATED,
  type Config,
  type CredentialStatus,
  type ProviderProfile,
} from '../../api/client';
import { newProvider, templates } from './providers';
import { KeyField } from './KeyField';

/** Configure model endpoints and their keys. Keys are saved on the server immediately. */
export function ProvidersPage({
  draft,
  onChange,
}: {
  draft: Config;
  onChange: (config: Config) => void;
}) {
  const names = draft.providers.map((profile) => profile.credential_ref).filter(Boolean);
  const statuses = useQuery({
    queryKey: ['credentials', names],
    queryFn: () =>
      request<CredentialStatus[]>(
        `/credentials?${names.map((name) => `name=${encodeURIComponent(name)}`).join('&')}`,
      ),
  });
  const update = (id: string, patch: Partial<ProviderProfile>) =>
    onChange({
      ...draft,
      providers: draft.providers.map((profile) =>
        profile.id === id ? { ...profile, ...patch } : profile,
      ),
    });
  const remove = (id: string) =>
    onChange({
      ...draft,
      providers: draft.providers.filter((profile) => profile.id !== id),
      // The default model cannot point at a removed provider.
      provider:
        draft.provider.profile_id === id
          ? { ...draft.provider, profile_id: SIMULATED, adapter: 'fake', model: 'simulated' }
          : draft.provider,
    });
  const add = (adapter: 'openai' | 'anthropic') =>
    onChange({
      ...draft,
      providers: [
        ...draft.providers,
        newProvider(
          adapter,
          draft.providers.map((profile) => profile.id),
        ),
      ],
    });
  return (
    <>
      <p className="page-intro">
        Model endpoints the studio can call. Keys stay on this computer and are never sent to the
        browser. Choose models on the Models page or below the message box.
      </p>
      {draft.providers.map((profile) => (
        <section className="settings-card" key={profile.id} aria-label={`Provider ${profile.name}`}>
          <div className="row-between">
            <h3>{profile.name}</h3>
            <div className="row">
              <span className="badge">
                {profile.adapter === 'anthropic' ? 'Messages API' : 'Chat Completions'}
              </span>
              <button
                className="icon-button"
                aria-label={`Remove ${profile.name}`}
                onClick={() => remove(profile.id)}
              >
                <Trash2 size={15} />
              </button>
            </div>
          </div>
          <div className="form-grid">
            <label>
              Name
              <input
                value={profile.name}
                onChange={(event) => update(profile.id, { name: event.target.value })}
              />
            </label>
            <label>
              Base URL
              <input
                value={profile.base_url}
                onChange={(event) => update(profile.id, { base_url: event.target.value })}
                placeholder={templates[profile.adapter].base_url}
              />
            </label>
          </div>
          <KeyField
            name={profile.credential_ref}
            status={statuses.data?.find((item) => item.name === profile.credential_ref)}
            onChanged={() => statuses.refetch()}
          />
          <details className="advanced">
            <summary>Key variable name</summary>
            <label>
              Environment variable
              <input
                value={profile.credential_ref}
                spellCheck={false}
                onChange={(event) => update(profile.id, { credential_ref: event.target.value })}
                placeholder="Leave empty for endpoints without a key"
              />
            </label>
            <p className="subtle">
              A key saved here is stored under this name; without one, the variable of this name
              from the environment or .env is used.
            </p>
          </details>
        </section>
      ))}
      <div className="row">
        <button className="button" onClick={() => add('openai')}>
          <Plus size={14} /> Add OpenAI-compatible
        </button>
        <button className="button" onClick={() => add('anthropic')}>
          <Plus size={14} /> Add Anthropic
        </button>
      </div>
    </>
  );
}
