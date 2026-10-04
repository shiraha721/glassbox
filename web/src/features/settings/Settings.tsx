import { useRef, useState } from 'react';
import { Plus, RefreshCw, SlidersHorizontal, Trash2, X } from 'lucide-react';
import { request, errorMessage, type Bootstrap, type Config } from '../../api/client';
import { ToolCatalog } from '../tools/ToolCatalog';
import { useDialogFocus } from '../../shared/useDialogFocus';

/** Edit one versioned settings aggregate and explicitly discover configured MCP servers. */
export function Settings({
  data,
  onClose,
  onSaved,
}: {
  data: Bootstrap;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(data.config);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(dialogRef, true, () => {
    if (!busy) onClose();
  });
  const provider = draft.provider;
  const setProvider = (patch: Partial<Config['provider']>) =>
    setDraft({ ...draft, provider: { ...provider, ...patch } });
  const save = async (discover = false) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const saved = await request<Config>('/config', 'PUT', draft);
      setDraft(saved);
      if (discover) await request('/tools/refresh', 'POST');
      await onSaved();
      setNotice(
        discover
          ? 'Settings saved. Discovery complete; review server errors and tool permissions below.'
          : 'Settings saved. New runs use these settings.',
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        <header className="modal-heading">
          <SlidersHorizontal size={18} />
          <h2 id="settings-title">Workspace settings</h2>
          <button
            className="icon-button"
            disabled={busy}
            onClick={onClose}
            aria-label="Close settings"
          >
            <X size={19} />
          </button>
        </header>
        <div className="settings-content">
          <fieldset disabled={busy}>
            <section>
              <h3>Model</h3>
              <label>
                Provider
                <select
                  value={provider.adapter}
                  onChange={(event) =>
                    setProvider({
                      adapter: event.target.value as Config['provider']['adapter'],
                      ...(event.target.value === 'anthropic'
                        ? {
                            base_url: 'https://api.anthropic.com/v1',
                            credential_ref: 'ANTHROPIC_API_KEY',
                            model: '',
                          }
                        : event.target.value === 'openai'
                          ? {
                              base_url: 'https://api.openai.com/v1',
                              credential_ref: 'OPENAI_API_KEY',
                              model: '',
                            }
                          : {}),
                    })
                  }
                >
                  <option value="fake">Simulated model · offline</option>
                  <option value="openai">OpenAI-compatible · Chat Completions</option>
                  <option value="anthropic">Anthropic-compatible · Messages</option>
                </select>
              </label>
              {provider.adapter === 'fake' ? (
                <p className="notice">
                  No key or network needed. Model responses are simulated; local and MCP tools
                  execute normally.
                </p>
              ) : (
                <>
                  <div className="form-grid">
                    <label>
                      Base URL
                      <input
                        value={provider.base_url}
                        onChange={(event) => setProvider({ base_url: event.target.value })}
                        placeholder={
                          provider.adapter === 'anthropic'
                            ? 'https://api.anthropic.com/v1'
                            : 'https://api.openai.com/v1'
                        }
                      />
                    </label>
                    <label>
                      Model
                      <input
                        value={provider.model}
                        onChange={(event) => setProvider({ model: event.target.value })}
                        placeholder="Model ID served by your endpoint"
                      />
                    </label>
                  </div>
                  <label>
                    Credential environment variable
                    <input
                      value={provider.credential_ref}
                      onChange={(event) => setProvider({ credential_ref: event.target.value })}
                      placeholder={
                        provider.adapter === 'anthropic' ? 'ANTHROPIC_API_KEY' : 'OPENAI_API_KEY'
                      }
                    />
                  </label>
                  <p className="subtle">
                    Set{' '}
                    {provider.adapter === 'anthropic'
                      ? 'ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, and ANTHROPIC_API_KEY'
                      : 'OPENAI_BASE_URL, OPENAI_MODEL, and OPENAI_API_KEY'}{' '}
                    in .env. Base URL and model are applied on server startup; changes here apply
                    immediately. Enter only the key variable name above, or leave it blank for a
                    keyless local endpoint.
                  </p>
                </>
              )}
              <div className="form-grid thirds">
                <label>
                  Temperature
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={provider.temperature}
                    onChange={(event) => setProvider({ temperature: Number(event.target.value) })}
                  />
                </label>
                <label>
                  Max output tokens
                  <input
                    type="number"
                    min="1"
                    value={provider.max_output_tokens}
                    onChange={(event) =>
                      setProvider({ max_output_tokens: Number(event.target.value) })
                    }
                  />
                </label>
                <label>
                  Context window
                  <input
                    type="number"
                    min="256"
                    placeholder="Unknown"
                    value={provider.context_window ?? ''}
                    onChange={(event) =>
                      setProvider({
                        context_window: event.target.value ? Number(event.target.value) : null,
                      })
                    }
                  />
                </label>
              </div>
            </section>
            <section>
              <h3>Workspace sandbox</h3>
              <label>
                Workspace access
                <select
                  value={draft.sandbox_mode}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      sandbox_mode: event.target.value as Config['sandbox_mode'],
                    })
                  }
                >
                  <option value="workspace-write">Read and write workspace files</option>
                  <option value="read-only">Read-only workspace</option>
                </select>
              </label>
              <p className="subtle">
                Commands use native Windows AppContainer or Linux bubblewrap. Network access is
                disabled. Check availability in Workspace. MCP servers run outside this sandbox.
              </p>
              <label>
                Trusted runtime directories · one absolute path per line
                <textarea
                  rows={3}
                  value={draft.toolchain_roots.join('\n')}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      toolchain_roots: event.target.value
                        .split('\n')
                        .filter((line) => line.trim())
                        .map((line) => line.trim()),
                    })
                  }
                />
              </label>
              <p className="subtle">
                Select specific Python/Node runtime folders. They are copied into the sandbox with
                read-only access. Original permissions stay unchanged. Python uses .rtagent/packages
                for workspace dependencies and pip stays offline.
              </p>
              <label>
                Allowed HTTPS download hosts · one exact hostname per line
                <textarea
                  rows={3}
                  value={draft.network_hosts.join('\n')}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      network_hosts: event.target.value
                        .split('\n')
                        .filter((line) => line.trim())
                        .map((line) => line.trim()),
                    })
                  }
                />
              </label>
              <p className="subtle">
                fetch_url can download up to 16 MiB to a new workspace file. Only public HTTPS
                addresses are accepted; no redirects or command network access.
              </p>
            </section>
            <section>
              <h3>Tool permissions</h3>
              <p className="subtle">
                Permissions are enforced by the server. New MCP tools remain disabled until you
                select Ask or Allow.
              </p>
              <ToolCatalog
                tools={data.tools}
                config={draft}
                onPermission={(name, value) =>
                  setDraft({ ...draft, tools: { ...draft.tools, [name]: value } })
                }
              />
              {Object.keys(draft.tools)
                .filter((name) => !data.tools.some((tool) => tool.name === name))
                .map((name) => (
                  <div className="tool-setting" key={name}>
                    <p>{name} · unavailable until discovery</p>
                    <button
                      className="text-button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          tools: Object.fromEntries(
                            Object.entries(draft.tools).filter(([key]) => key !== name),
                          ),
                        })
                      }
                    >
                      Remove
                    </button>
                  </div>
                ))}
            </section>
            <section>
              <h3>MCP servers</h3>
              <p className="subtle">
                Each operation opens a fresh connection. This release supports stateless servers. A
                stdio command runs on this computer.
              </p>
              {draft.mcp_servers.map((server, index) => {
                const change = (patch: Partial<typeof server>) =>
                  setDraft({
                    ...draft,
                    mcp_servers: draft.mcp_servers.map((item, i) =>
                      i === index ? { ...item, ...patch } : item,
                    ),
                  });
                return (
                  <div className="server-card" key={server.id}>
                    <div className="row">
                      <label>
                        Server name
                        <input
                          value={server.name}
                          onChange={(event) => change({ name: event.target.value })}
                        />
                      </label>
                      <label>
                        Transport
                        <select
                          value={server.transport}
                          onChange={(event) =>
                            change({ transport: event.target.value as typeof server.transport })
                          }
                        >
                          <option value="stdio">stdio</option>
                          <option value="streamable_http">Streamable HTTP</option>
                        </select>
                      </label>
                      <button
                        className="icon-button"
                        aria-label={`Remove ${server.name}`}
                        onClick={() =>
                          setDraft({
                            ...draft,
                            mcp_servers: draft.mcp_servers.filter((item) => item.id !== server.id),
                          })
                        }
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                    {server.transport === 'stdio' ? (
                      <>
                        <label>
                          Executable
                          <input
                            value={server.command}
                            onChange={(event) => change({ command: event.target.value })}
                            placeholder="Absolute path or executable on PATH"
                          />
                        </label>
                        <label>
                          Arguments · one per line
                          <textarea
                            rows={3}
                            value={server.args.join('\n')}
                            onChange={(event) =>
                              change({
                                args: event.target.value ? event.target.value.split('\n') : [],
                              })
                            }
                            className="code-input"
                            spellCheck={false}
                          />
                        </label>
                        <label>
                          Working directory
                          <input
                            value={server.cwd || ''}
                            onChange={(event) => change({ cwd: event.target.value || null })}
                            placeholder="Optional"
                          />
                        </label>
                        <label>
                          Environment references · NAME=SERVER_VARIABLE
                          <textarea
                            rows={2}
                            spellCheck={false}
                            className="code-input"
                            defaultValue={Object.entries(server.env)
                              .map(([key, value]) => `${key}=${value}`)
                              .join('\n')}
                            onBlur={(event) => {
                              const entries = event.target.value
                                .split('\n')
                                .filter(Boolean)
                                .map((line) => {
                                  const separator = line.indexOf('=');
                                  return separator < 0
                                    ? [line, '']
                                    : [line.slice(0, separator), line.slice(separator + 1)];
                                });
                              change({ env: Object.fromEntries(entries) });
                            }}
                          />
                        </label>
                      </>
                    ) : (
                      <>
                        <label>
                          MCP URL
                          <input
                            value={server.url}
                            onChange={(event) => change({ url: event.target.value })}
                            placeholder="http://127.0.0.1:9000/mcp"
                          />
                        </label>
                        <label>
                          Bearer credential environment variable
                          <input
                            value={server.credential_ref}
                            onChange={(event) => change({ credential_ref: event.target.value })}
                            placeholder="Optional server-side variable name"
                          />
                        </label>
                      </>
                    )}
                    {data.tool_errors[server.id] && (
                      <p className="error-text" role="alert">
                        {data.tool_errors[server.id]}
                      </p>
                    )}
                  </div>
                );
              })}
              <div className="row">
                <button
                  className="button small"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      mcp_servers: [
                        ...draft.mcp_servers,
                        {
                          id: crypto.randomUUID(),
                          name: 'MCP server',
                          transport: 'stdio',
                          command: '',
                          args: [],
                          cwd: null,
                          url: '',
                          env: {},
                          credential_ref: '',
                        },
                      ],
                    })
                  }
                >
                  <Plus size={14} />
                  Add server
                </button>
                <button className="button small" disabled={busy} onClick={() => void save(true)}>
                  <RefreshCw size={14} />
                  Save & discover
                </button>
              </div>
            </section>
            <section>
              <h3>Run limits</h3>
              <div className="form-grid">
                {(
                  [
                    ['max_steps', 'Model attempts'],
                    ['max_tool_calls', 'Tool attempts'],
                    ['tool_timeout', 'Tool timeout (seconds)'],
                    ['run_timeout', 'Active run budget (seconds)'],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      type="number"
                      min="1"
                      value={draft[key]}
                      onChange={(event) =>
                        setDraft({ ...draft, [key]: Number(event.target.value) })
                      }
                    />
                  </label>
                ))}
              </div>
              <p className="subtle">
                Workspace: <code>{data.workspace_root}</code>
              </p>
            </section>
          </fieldset>
        </div>
        <footer className="modal-footer">
          <div aria-live="polite">
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}
            {notice && <p className="success-text">{notice}</p>}
          </div>
          <button className="button primary" disabled={busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save settings'}
          </button>
        </footer>
      </section>
    </div>
  );
}
