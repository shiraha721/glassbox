import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import type { Config, Tool } from '../../api/client';
import { ToolCatalog } from '../tools/ToolCatalog';

type Server = Config['mcp_servers'][number];

/** Configure stateless MCP servers, discover their tools and set each tool's permission. */
export function McpPage({
  draft,
  tools,
  errors,
  busy,
  onChange,
  onDiscover,
}: {
  draft: Config;
  tools: Tool[];
  errors: Record<string, string>;
  busy: boolean;
  onChange: (config: Config) => void;
  onDiscover: () => void;
}) {
  const change = (id: string, patch: Partial<Server>) =>
    onChange({
      ...draft,
      mcp_servers: draft.mcp_servers.map((server) =>
        server.id === id ? { ...server, ...patch } : server,
      ),
    });
  return (
    <>
      <p className="page-intro">
        MCP servers run outside the command sandbox with this computer&apos;s permissions. Each call
        opens a fresh connection. New tools stay disabled until you allow them.
      </p>
      {draft.mcp_servers.map((server) => {
        const discovered = tools.filter((tool) => tool.server_id === server.id);
        return (
          <section
            className="settings-card"
            key={server.id}
            aria-label={`MCP server ${server.name}`}
          >
            <div className="row">
              <label>
                Server name
                <input
                  value={server.name}
                  onChange={(event) => change(server.id, { name: event.target.value })}
                />
              </label>
              <label>
                Transport
                <select
                  value={server.transport}
                  onChange={(event) =>
                    change(server.id, { transport: event.target.value as Server['transport'] })
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
                  onChange({
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
                    onChange={(event) => change(server.id, { command: event.target.value })}
                    placeholder="Absolute path or executable on PATH"
                  />
                </label>
                <label>
                  Arguments · one per line
                  <textarea
                    rows={3}
                    className="code-input"
                    spellCheck={false}
                    value={server.args.join('\n')}
                    onChange={(event) =>
                      change(server.id, {
                        args: event.target.value ? event.target.value.split('\n') : [],
                      })
                    }
                  />
                </label>
                <label>
                  Working directory
                  <input
                    value={server.cwd || ''}
                    onChange={(event) => change(server.id, { cwd: event.target.value || null })}
                    placeholder="Optional"
                  />
                </label>
                <label>
                  Environment references · NAME=KEY_VARIABLE
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
                      change(server.id, { env: Object.fromEntries(entries) });
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
                    onChange={(event) => change(server.id, { url: event.target.value })}
                    placeholder="http://127.0.0.1:9000/mcp"
                  />
                </label>
                <label>
                  Bearer key variable
                  <input
                    value={server.credential_ref}
                    onChange={(event) => change(server.id, { credential_ref: event.target.value })}
                    placeholder="Optional key name, resolved on the server"
                  />
                </label>
              </>
            )}
            {errors[server.id] && (
              <p className="error-text" role="alert">
                {errors[server.id]}
              </p>
            )}
            {discovered.length > 0 && (
              <ToolCatalog
                tools={discovered}
                config={draft}
                onPermission={(name, value) =>
                  onChange({ ...draft, tools: { ...draft.tools, [name]: value } })
                }
              />
            )}
          </section>
        );
      })}
      <div className="row">
        <button
          className="button"
          onClick={() =>
            onChange({
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
        <button className="button" disabled={busy} onClick={onDiscover}>
          <RefreshCw size={14} />
          Save & discover
        </button>
      </div>
    </>
  );
}
