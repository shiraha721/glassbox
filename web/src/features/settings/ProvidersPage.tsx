import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Plus, Trash2 } from 'lucide-react';
import {
  request,
  SIMULATED,
  type Config,
  type CredentialStatus,
  type ProviderProfile,
} from '../../api/client';
import {
  customEndpoints,
  formatLabels,
  formatPaths,
  formats,
  newProvider,
  serviceOf,
  serviceProvider,
  services,
  setDefaultFormat,
  setFormatUrl,
  type Format,
} from './providers';
import { ChatGPTField } from './ChatGPTField';
import { KeyField } from './KeyField';

/** The base URL of each request format; a provider offers the formats that have one. */
function FormatTable({
  profile,
  onChange,
}: {
  profile: ProviderProfile;
  onChange: (profile: ProviderProfile) => void;
}) {
  return (
    <div className="format-table">
      <table>
        <thead>
          <tr>
            <th>Request format</th>
            <th>Base URL</th>
            <th>Default</th>
          </tr>
        </thead>
        <tbody>
          {formats.map((format) => {
            const url =
              format === profile.adapter ? profile.base_url : (profile.endpoints?.[format] ?? '');
            return (
              <tr key={format}>
                <th scope="row">{formatLabels[format]}</th>
                <td>
                  <input
                    aria-label={`${formatLabels[format]} base URL`}
                    value={url}
                    spellCheck={false}
                    placeholder={format === profile.adapter ? 'Required' : 'Not offered'}
                    onChange={(event) =>
                      onChange(setFormatUrl(profile, format, event.target.value.trim()))
                    }
                  />
                </td>
                <td>
                  <input
                    type="radio"
                    name={`default-format-${profile.id}`}
                    aria-label={`Use ${formatLabels[format]} by default`}
                    checked={format === profile.adapter}
                    disabled={!url}
                    onChange={() => onChange(setDefaultFormat(profile, format))}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Configure model services and their keys. Keys are saved on the server immediately. */
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
  const replace = (next: ProviderProfile) =>
    onChange({
      ...draft,
      providers: draft.providers.map((profile) => (profile.id === next.id ? next : profile)),
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
  const add = (profile: ProviderProfile) =>
    onChange({ ...draft, providers: [...draft.providers, profile] });
  const ids = draft.providers.map((profile) => profile.id);
  return (
    <>
      <p className="page-intro">
        Model services Glassbox can call. A service may accept several request formats, each at a
        base URL that requests extend with{' '}
        {formats.map((format, index) => (
          <span key={format}>
            {index ? (index === formats.length - 1 ? ' or ' : ', ') : ''}
            <code>{formatPaths[format]}</code>
          </span>
        ))}
        ; leave a format empty when the service does not offer it. Models use the default format
        unless Settings → Models assigns another. Keys stay on this computer and are never sent to
        the browser.
      </p>
      {draft.providers.map((profile) => {
        const service = serviceOf(profile);
        return (
          <section
            className="settings-card"
            key={profile.id}
            aria-label={`Provider ${profile.name}`}
          >
            <div className="row-between">
              <h3>{profile.name}</h3>
              <button
                className="icon-button"
                aria-label={`Remove ${profile.name}`}
                onClick={() => remove(profile.id)}
              >
                <Trash2 size={15} />
              </button>
            </div>
            <label>
              Name
              <input
                value={profile.name}
                onChange={(event) => replace({ ...profile, name: event.target.value })}
              />
            </label>
            {profile.auth === 'chatgpt' ? (
              <>
                <p className="subtle">
                  {formatLabels.responses} at <code>{profile.base_url}</code>, the only endpoint a
                  ChatGPT sign-in is used with.
                </p>
                <ChatGPTField providerId={profile.id} />
              </>
            ) : (
              <>
                <FormatTable profile={profile} onChange={replace} />
                <KeyField
                  name={profile.credential_ref}
                  status={statuses.data?.find((item) => item.name === profile.credential_ref)}
                  onChanged={() => statuses.refetch()}
                />
              </>
            )}
            {service?.keyUrl && (
              <a className="text-link" href={service.keyUrl} target="_blank" rel="noreferrer">
                Get a key from {service.name} <ExternalLink size={12} />
              </a>
            )}
            {profile.auth !== 'chatgpt' && (
              <details className="advanced">
                <summary>Key variable name</summary>
                <label>
                  Environment variable
                  <input
                    value={profile.credential_ref}
                    spellCheck={false}
                    onChange={(event) =>
                      replace({ ...profile, credential_ref: event.target.value })
                    }
                    placeholder="Leave empty for endpoints without a key"
                  />
                </label>
                <p className="subtle">
                  A key saved here is stored under this name; without one, the variable of this name
                  from the environment or .env is used.
                </p>
              </details>
            )}
          </section>
        );
      })}
      <section className="settings-card" aria-label="Add a provider">
        <h3>Add a provider</h3>
        <div className="service-grid">
          {services.map((service) => (
            <button
              key={service.id}
              className="service-option"
              onClick={() => add(serviceProvider(service, ids))}
            >
              <strong>
                <Plus size={13} /> {service.name}
              </strong>
              <small>{service.description}</small>
            </button>
          ))}
        </div>
        <p className="subtle">Another service or a local server, by the format it speaks:</p>
        <div className="row wrap">
          {(Object.keys(customEndpoints) as Format[]).map((format) => (
            <button key={format} className="button" onClick={() => add(newProvider(format, ids))}>
              <Plus size={14} /> {customEndpoints[format].name}
            </button>
          ))}
        </div>
      </section>
    </>
  );
}
