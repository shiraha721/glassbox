import { useRef, useState } from 'react';
import {
  BookOpen,
  Cpu,
  Gauge,
  Keyboard,
  Webhook,
  KeyRound,
  Layers,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
  X,
} from 'lucide-react';
import { request, errorMessage, type Bootstrap, type Config } from '../../api/client';
import { ToolCatalog } from '../tools/ToolCatalog';
import { useDialogFocus } from '../../shared/useDialogFocus';
import { ProvidersPage } from './ProvidersPage';
import { ModelsPage } from './ModelsPage';
import { McpPage } from './McpPage';
import { SkillsPage } from './SkillsPage';
import { CommandRules } from './CommandRules';
import { SearchSettings } from './SearchSettings';
import { ContextPage } from './ContextPage';
import { HooksPage } from './HooksPage';
import { ComposerPage } from './ComposerPage';
import type { SettingsPage } from './pages';
import { withDefaultConnection } from './providers';

const pages = [
  { id: 'providers', label: 'Providers', icon: KeyRound },
  { id: 'models', label: 'Models', icon: Cpu },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'mcp', label: 'MCP servers', icon: Server },
  { id: 'skills', label: 'Skills', icon: BookOpen },
  { id: 'sandbox', label: 'Sandbox', icon: ShieldCheck },
  { id: 'context', label: 'Context', icon: Layers },
  { id: 'hooks', label: 'Hooks', icon: Webhook },
  { id: 'limits', label: 'Run limits', icon: Gauge },
  { id: 'composer', label: 'Composer', icon: Keyboard },
] as const;

/** Split a one-entry-per-line text area into trimmed nonempty values. */
function lines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Global settings: providers and keys, models, tools, MCP, skills, sandbox and limits.
 *
 * Configuration pages edit one versioned draft saved together; keys and global skills are
 * saved immediately by their own pages because they are not part of the settings record. */
export function Settings({
  data,
  page: initialPage,
  onClose,
  onSaved,
}: {
  data: Bootstrap;
  page: SettingsPage;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [page, setPage] = useState<SettingsPage>(initialPage);
  const [draft, setDraft] = useState<Config>(data.config);
  const [baseline, setBaseline] = useState<Config>(data.config);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const dialogRef = useRef<HTMLElement>(null);
  const close = () => {
    if (busy) return;
    if (dirty && !window.confirm('Discard unsaved settings changes?')) return;
    onClose();
  };
  useDialogFocus(dialogRef, true, close);
  const change = (next: Config) => {
    setDraft(withDefaultConnection(next));
    setNotice('');
  };
  const save = async (discover = false) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const saved = await request<Config>('/config', 'PUT', draft);
      setDraft(saved);
      setBaseline(saved);
      if (discover) await request('/tools/refresh', 'POST');
      await onSaved();
      setNotice(
        discover
          ? 'Settings saved. Discovery complete; set permissions for the tools found.'
          : 'Settings saved. New runs use these settings.',
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const title = pages.find((item) => item.id === page)?.label;
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
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
          <h2 id="settings-title">Settings</h2>
          <button
            className="icon-button"
            disabled={busy}
            onClick={close}
            aria-label="Close settings"
          >
            <X size={19} />
          </button>
        </header>
        <div className="settings-body">
          <nav className="settings-nav" aria-label="Settings pages">
            {pages.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                className="settings-nav-item"
                aria-current={page === id ? 'page' : undefined}
                onClick={() => setPage(id)}
              >
                <Icon size={15} />
                {label}
              </button>
            ))}
          </nav>
          <div className="settings-content">
            <h3 className="settings-page-title">{title}</h3>
            <fieldset disabled={busy}>
              {page === 'providers' && <ProvidersPage draft={draft} onChange={change} />}
              {page === 'models' && (
                <ModelsPage draft={draft} effortLevels={data.effort_levels} onChange={change} />
              )}
              {page === 'tools' && (
                <>
                  <p className="page-intro">
                    The default permission of each tool. The workspace access chosen below the
                    message box can tighten these for a run: read only removes file changes, and ask
                    before changes requires approval for edits, commands and MCP tools.
                  </p>
                  <CommandRules draft={draft} onChange={change} />
                  <SearchSettings draft={draft} onChange={change} />
                  <ToolCatalog
                    tools={data.tools}
                    config={draft}
                    onPermission={(name, value) =>
                      change({ ...draft, tools: { ...draft.tools, [name]: value } })
                    }
                  />
                  {Object.keys(draft.tools)
                    .filter((name) => !data.tools.some((tool) => tool.name === name))
                    .map((name) => (
                      <div className="tool-setting" key={name}>
                        <p>{name} · unavailable until its MCP server is discovered</p>
                        <button
                          className="text-button"
                          onClick={() =>
                            change({
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
                </>
              )}
              {page === 'mcp' && (
                <McpPage
                  draft={draft}
                  tools={data.tools}
                  errors={data.tool_errors}
                  busy={busy}
                  onChange={change}
                  onDiscover={() => void save(true)}
                />
              )}
              {page === 'sandbox' && (
                <>
                  <p className="page-intro">
                    Commands run in a Linux bubblewrap or Windows AppContainer sandbox without
                    network access. Choose read-only or write access for each run below the message
                    box; check that the sandbox works in the Workspace panel.
                  </p>
                  <label>
                    Trusted runtime directories · one absolute path per line
                    <textarea
                      rows={3}
                      className="code-input"
                      spellCheck={false}
                      value={draft.toolchain_roots.join('\n')}
                      onChange={(event) =>
                        change({ ...draft, toolchain_roots: lines(event.target.value) })
                      }
                    />
                  </label>
                  <p className="subtle">
                    Specific Python or Node installation folders, exposed read-only to commands
                    (mounted on Linux, copied on Windows; original permissions never change). For a
                    virtual environment, add the interpreter folder named in its pyvenv.cfg. Python
                    also finds packages in .glassbox/packages; pip stays offline.
                  </p>
                  <label>
                    Allowed HTTPS download hosts · one exact hostname per line
                    <textarea
                      rows={6}
                      className="code-input"
                      spellCheck={false}
                      value={draft.network_hosts.join('\n')}
                      onChange={(event) =>
                        change({ ...draft, network_hosts: lines(event.target.value) })
                      }
                    />
                  </label>
                  <div className="row">
                    <button
                      className="button"
                      disabled={data.common_download_hosts.every((host) =>
                        draft.network_hosts.includes(host),
                      )}
                      onClick={() =>
                        change({
                          ...draft,
                          network_hosts: [
                            ...new Set([...draft.network_hosts, ...data.common_download_hosts]),
                          ],
                        })
                      }
                    >
                      Add common hosts
                    </button>
                    <span className="subtle">
                      GitHub, npm, PyPI, Hugging Face, Google downloads, crates.io, Go modules and
                      Maven Central, with the hosts they redirect downloads to.
                    </span>
                  </div>
                  <p className="subtle">
                    fetch_url can download up to 16 MiB from these hosts to a new workspace file,
                    and asks before each download unless you allow it in Tools. Only public HTTPS
                    addresses are accepted; redirects are followed only to hosts in this list.
                    Commands still have no network access.
                  </p>
                </>
              )}
              {page === 'context' && <ContextPage draft={draft} onChange={change} />}
              {page === 'hooks' && <HooksPage draft={draft} onChange={change} />}
              {page === 'limits' && (
                <>
                  <p className="page-intro">
                    Bounds for every run. A run that reaches one stops with the reason shown.
                  </p>
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
                            change({ ...draft, [key]: Number(event.target.value) })
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <p className="subtle">
                    Default workspace: <code>{data.workspace_root}</code>
                  </p>
                </>
              )}
            </fieldset>
            {page === 'skills' && <SkillsPage />}
            {page === 'composer' && <ComposerPage />}
          </div>
        </div>
        <footer className="modal-footer">
          <div aria-live="polite">
            {error ? (
              <p className="error-text" role="alert">
                {error}
              </p>
            ) : notice ? (
              <p className="success-text">{notice}</p>
            ) : dirty ? (
              <p className="subtle">Unsaved changes</p>
            ) : null}
          </div>
          <button className="button primary" disabled={busy || !dirty} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save settings'}
          </button>
        </footer>
      </section>
    </div>
  );
}
