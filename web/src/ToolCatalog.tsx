import { useState } from 'react';
import { Search, Wrench, X } from 'lucide-react';
import type { Config, Tool } from './api/client';

const groups = {
  'Files and search': [
    'list_files',
    'search_files',
    'search_text',
    'read_text',
    'read_instructions',
  ],
  'File changes': ['write_text', 'edit_text', 'apply_patch'],
  Commands: [
    'powershell',
    'bash',
    'start_process',
    'poll_process',
    'send_process_input',
    'stop_process',
  ],
  Downloads: ['fetch_url'],
  'Memory and Skills': ['read_memory', 'save_memory', 'load_skill', 'save_skill'],
  'Task progress': ['update_plan', 'echo'],
};
const writes = new Set([
  'write_text',
  'edit_text',
  'apply_patch',
  'save_memory',
  'save_skill',
  'fetch_url',
]);

/** Identify the concrete capability group without treating MCP annotations as authorization. */
function toolGroup(tool: Tool): string {
  if (tool.server_id) return 'MCP connections';
  return (
    Object.entries(groups).find(([, names]) => names.includes(tool.name))?.[0] || 'Other tools'
  );
}

/** Inspect effective tool exposure, arguments, and optional versioned permission edits. */
export function ToolCatalog({
  tools,
  config,
  onPermission,
}: {
  tools: Tool[];
  config: Config;
  onPermission?: (name: string, value: 'allow' | 'ask' | 'deny') => void;
}) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'exposed' | 'disabled'>('all');
  const exposed = (tool: Tool) =>
    (config.tools[tool.name] || 'deny') !== 'deny' &&
    !(config.sandbox_mode === 'read-only' && (writes.has(tool.name) || tool.writes_workspace));
  const visible = tools.filter((tool) => {
    const text = `${tool.name} ${tool.original_name || ''} ${tool.description}`.toLowerCase();
    return (
      text.includes(search.toLowerCase()) &&
      (filter === 'all' || exposed(tool) === (filter === 'exposed'))
    );
  });
  const names = [...Object.keys(groups), 'MCP connections', 'Other tools'];
  return (
    <div className="tool-catalog">
      <div className="tool-filter">
        <label className="search-box">
          <Search size={15} />
          <input
            aria-label="Search tools"
            placeholder="Search tools"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <select
          aria-label="Tool visibility"
          value={filter}
          onChange={(event) => setFilter(event.target.value as typeof filter)}
        >
          <option value="all">All tools ({tools.length})</option>
          <option value="exposed">Exposed ({tools.filter(exposed).length})</option>
          <option value="disabled">Disabled</option>
        </select>
      </div>
      {names.map((group) => {
        const members = visible.filter((tool) => toolGroup(tool) === group);
        if (!members.length) return null;
        return (
          <section className="tool-group" key={group}>
            <h3>
              {group} <span className="count">{members.length}</span>
            </h3>
            {members.map((tool) => {
              const permission = config.tools[tool.name] || 'deny';
              const blocked =
                config.sandbox_mode === 'read-only' &&
                (writes.has(tool.name) || tool.writes_workspace);
              return (
                <div className="tool-setting" key={tool.name} data-tool={tool.name}>
                  <div>
                    <strong title={tool.name}>{tool.original_name || tool.name}</strong>
                    <span className={`badge tool-access ${exposed(tool) ? 'tool-exposed' : ''}`}>
                      {blocked
                        ? 'Blocked by read-only'
                        : permission === 'deny'
                          ? 'Disabled'
                          : permission === 'ask'
                            ? 'Approval required'
                            : 'Allowed'}
                    </span>
                    <p>{tool.description}</p>
                    {tool.server_id && (
                      <p className="subtle">External MCP connection · outside command sandbox</p>
                    )}
                    <details className="tool-schema">
                      <summary>Arguments</summary>
                      <pre className="json">{JSON.stringify(tool.input_schema, null, 2)}</pre>
                    </details>
                  </div>
                  {onPermission && (
                    <select
                      aria-label={`${tool.name} permission`}
                      value={permission}
                      onChange={(event) =>
                        onPermission(tool.name, event.target.value as 'allow' | 'ask' | 'deny')
                      }
                    >
                      <option value="deny">Disabled</option>
                      <option value="ask">Ask each time</option>
                      <option value="allow">Allow</option>
                    </select>
                  )}
                </div>
              );
            })}
          </section>
        );
      })}
      {!visible.length && <p className="subtle">No matching tools.</p>}
    </div>
  );
}

/** Show the current workspace's tool inventory without offering an unrecorded execution route. */
export function ToolsPanel({
  tools,
  config,
  onClose,
  onSettings,
}: {
  tools: Tool[];
  config: Config;
  onClose: () => void;
  onSettings: () => void;
}) {
  return (
    <aside className="panel tools-panel" aria-label="Tool catalog">
      <div className="panel-heading">
        <Wrench size={16} />
        <h2>Tools</h2>
        <button className="icon-button" aria-label="Close tools" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="tools-content">
        <ToolCatalog tools={tools} config={config} />
      </div>
      <div className="panel-footer">
        <span className="subtle">Run freezes tools and permissions.</span>
        <button className="button" onClick={onSettings}>
          Edit permissions
        </button>
      </div>
    </aside>
  );
}
