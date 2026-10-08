import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, FlaskConical, Play, Plus, Square, Trash2, X } from 'lucide-react';
import {
  errorMessage,
  request,
  SIMULATED,
  type Bootstrap,
  type Eval,
  type EvalBatch,
  type EvalCase,
  type EvalCheck,
  type EvalVariant,
} from '../../api/client';
import { StatusBadge } from '../../shared/StatusBadge';
import { useDialogFocus } from '../../shared/useDialogFocus';
import { EvalResults } from './EvalResults';
import {
  blank,
  checkKinds,
  fromEval,
  identicalSetups,
  isRenamed,
  namedSetup,
  newCase,
  newSetup,
  problems,
  uniqueName,
  type Draft,
} from './draft';

type Tab = 'tasks' | 'setups' | 'results';

/** Compare ways of running the agent on the same tasks.
 *
 * Tasks are what to ask and how to check the final reply; setups are the presets, models or
 * access to compare. Each task runs once under each setup as an ordinary Run in a hidden
 * conversation, and Results ranks the setups by how many tasks they passed. */
export function Evaluations({
  data,
  projectId,
  onClose,
  onChanged,
}: {
  data: Bootstrap;
  projectId: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>(() => blank(data));
  const [baseline, setBaseline] = useState(() => JSON.stringify(draft));
  const [tab, setTab] = useState<Tab>('tasks');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [batchId, setBatchId] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  // Setups the user named themselves; the others follow their preset, model and access.
  const [renamed, setRenamed] = useState<Set<string>>(() => new Set());
  const dialogRef = useRef<HTMLElement>(null);
  const dirty = JSON.stringify(draft) !== baseline;
  const project = data.projects.find((item) => item.id === projectId);
  const workspace = project?.name || 'the default workspace';

  const close = () => {
    if (busy) return;
    if (dirty && !window.confirm('Discard unsaved evaluation changes?')) return;
    onClose();
  };
  useDialogFocus(dialogRef, true, close);

  const batches = useQuery({
    queryKey: ['eval-batches', draft.id],
    enabled: !!draft.id,
    queryFn: () => request<EvalBatch[]>(`/evals/${draft.id}/batches`),
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.status === 'running') ? 700 : false,
  });
  const batch = batches.data?.find((item) => item.id === batchId) ?? batches.data?.[0];
  const running = batch?.status === 'running';
  // The list shows each evaluation's latest status; refresh it when a comparison finishes.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) void onChanged();
    wasRunning.current = running;
  }, [running, onChanged]);

  const perform = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const load = (value: Draft, view: Tab = 'tasks') => {
    setDraft(value);
    setBaseline(JSON.stringify(value));
    setRenamed(new Set(value.variants.filter((item) => isRenamed(item, data)).map((v) => v.id)));
    setBatchId(null);
    setOpen(null);
    setTab(view);
  };
  const select = (id: string | null) => {
    if (dirty && !window.confirm('Discard unsaved evaluation changes?')) return;
    if (!id) return load(blank(data));
    const ran = !!data.evals.find((item) => item.id === id)?.last_batch_id;
    void perform(async () =>
      load(fromEval(await request<Eval>(`/evals/${id}`)), ran ? 'results' : 'tasks'),
    );
  };
  const save = async (): Promise<string> => {
    const body = { name: draft.name, cases: draft.cases, variants: draft.variants };
    const saved = draft.id
      ? await request<Eval>(`/evals/${draft.id}`, 'PUT', {
          ...body,
          expected_revision: draft.revision,
        })
      : await request<Eval>('/evals', 'POST', body);
    load(fromEval(saved), tab);
    await onChanged();
    return saved.id;
  };
  const run = async () => {
    const missing = problems(draft);
    if (missing.length) {
      setTab('tasks');
      throw new Error(missing[0]);
    }
    const id = dirty || !draft.id ? await save() : draft.id;
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const started = await request<EvalBatch>(`/evals/${id}/batches${query}`, 'POST');
    setBatchId(started.id);
    setOpen(null);
    setTab('results');
    await batches.refetch();
  };

  const updateCase = (index: number, change: Partial<EvalCase>) =>
    setDraft({
      ...draft,
      cases: draft.cases.map((item, i) => (i === index ? { ...item, ...change } : item)),
    });
  const updateCheck = (index: number, change: Partial<EvalCheck>) => {
    const current = draft.cases[index].check ?? { kind: 'contains', value: '', ignore_case: true };
    updateCase(index, { check: { ...current, ...change } });
  };
  const updateSetup = (index: number, change: Partial<EvalVariant>) => {
    const variants = draft.variants.map((item, i) => {
      if (i !== index) return item;
      const next = { ...item, ...change };
      return renamed.has(item.id) || 'name' in change
        ? next
        : namedSetup(next, draft.variants, data);
    });
    setDraft({ ...draft, variants });
  };

  const models = [
    { value: JSON.stringify([SIMULATED, 'simulated']), label: 'Simulated model' },
    ...data.config.providers.flatMap((provider) =>
      provider.models.map((model) => ({
        value: JSON.stringify([provider.id, model]),
        label: `${provider.name} · ${model}`,
      })),
    ),
  ];
  const duplicates = identicalSetups(draft.variants);
  const runs = draft.cases.length * draft.variants.length;

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
        aria-labelledby="evaluations-title"
      >
        <header className="modal-heading">
          <FlaskConical size={18} />
          <h2 id="evaluations-title">Evaluations</h2>
          <button
            className="icon-button"
            disabled={busy}
            onClick={close}
            aria-label="Close evaluations"
          >
            <X size={19} />
          </button>
        </header>
        <div className="settings-body">
          <nav className="settings-nav" aria-label="Saved evaluations">
            <button className="button" disabled={busy} onClick={() => select(null)}>
              <Plus size={14} /> New evaluation
            </button>
            {data.evals.map((item) => (
              <button
                key={item.id}
                className="settings-nav-item eval-nav-item"
                aria-current={draft.id === item.id ? 'page' : undefined}
                disabled={busy}
                onClick={() => select(item.id)}
              >
                <span>{item.name}</span>
                <small>
                  {item.case_count} task{item.case_count === 1 ? '' : 's'} · {item.variant_count}{' '}
                  setup{item.variant_count === 1 ? '' : 's'}
                  {item.last_status ? ` · ${item.last_status}` : ''}
                </small>
              </button>
            ))}
          </nav>
          <div className="settings-content eval-content">
            <div className="eval-title">
              <input
                aria-label="Evaluation name"
                value={draft.name}
                maxLength={80}
                disabled={busy || running}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
              {draft.id && (
                <button
                  className="icon-button"
                  aria-label="Delete evaluation"
                  title="Delete this evaluation and its results"
                  disabled={busy || running}
                  onClick={() => {
                    if (!window.confirm(`Delete ${draft.name} and its results?`)) return;
                    void perform(async () => {
                      await request(`/evals/${draft.id}`, 'DELETE');
                      load(blank(data));
                      await onChanged();
                    });
                  }}
                >
                  <Trash2 size={15} />
                </button>
              )}
            </div>
            <p className="page-intro">
              Run the same tasks with different presets, models or access, and see which setup gets
              more of them right. Every reply is checked by plain text rules, so a result never
              depends on another model&apos;s judgment.
            </p>
            <div className="tabs eval-tabs" role="tablist" aria-label="Evaluation steps">
              <button role="tab" aria-selected={tab === 'tasks'} onClick={() => setTab('tasks')}>
                1 · Tasks <span className="count">{draft.cases.length}</span>
              </button>
              <button role="tab" aria-selected={tab === 'setups'} onClick={() => setTab('setups')}>
                2 · Setups to compare <span className="count">{draft.variants.length}</span>
              </button>
              <button
                role="tab"
                aria-selected={tab === 'results'}
                onClick={() => setTab('results')}
              >
                3 · Results {batch && <StatusBadge status={batch.status} />}
              </button>
            </div>
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}

            {tab === 'tasks' && (
              <fieldset disabled={busy || running} className="eval-step">
                <p className="subtle">
                  Each task starts a new conversation in <strong>{workspace}</strong>. When the run
                  ends, its final reply is checked.
                </p>
                {draft.cases.map((item, index) => (
                  <div
                    className="settings-card eval-card"
                    key={item.id}
                    role="group"
                    aria-label={item.name}
                  >
                    <div className="eval-card-head">
                      <input
                        className="eval-inline-name"
                        aria-label="Task name"
                        value={item.name}
                        maxLength={120}
                        onChange={(event) => updateCase(index, { name: event.target.value })}
                      />
                      <button
                        className="icon-button"
                        aria-label={`Remove ${item.name}`}
                        disabled={draft.cases.length === 1}
                        onClick={() =>
                          setDraft({ ...draft, cases: draft.cases.filter((_, i) => i !== index) })
                        }
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                    <label>
                      Task
                      <textarea
                        rows={2}
                        value={item.input}
                        placeholder="What the agent is asked, for example: Which file starts the server? Answer with its path."
                        onChange={(event) => updateCase(index, { input: event.target.value })}
                      />
                    </label>
                    <div className="eval-check">
                      <label>
                        The reply passes if it
                        <select
                          value={item.check?.kind ?? 'contains'}
                          onChange={(event) =>
                            updateCheck(index, { kind: event.target.value as EvalCheck['kind'] })
                          }
                        >
                          {checkKinds.map((kind) => (
                            <option key={kind.value} value={kind.value}>
                              {kind.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {item.check?.kind !== 'succeeds' && (
                        <label>
                          {item.check?.kind === 'regex' ? 'Pattern' : 'Expected text'}
                          <input
                            value={item.check?.value ?? ''}
                            placeholder={
                              item.check?.kind === 'regex' ? 'server\\.(py|ts)' : 'server.py'
                            }
                            onChange={(event) => updateCheck(index, { value: event.target.value })}
                          />
                        </label>
                      )}
                      {item.check?.kind !== 'succeeds' && (
                        <label className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={item.check?.ignore_case ?? true}
                            onChange={(event) =>
                              updateCheck(index, { ignore_case: event.target.checked })
                            }
                          />
                          Ignore case
                        </label>
                      )}
                    </div>
                  </div>
                ))}
                <button
                  className="text-button"
                  disabled={draft.cases.length >= 50}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      cases: [
                        ...draft.cases,
                        {
                          ...newCase(draft.cases.length + 1),
                          name: uniqueName(
                            `Task ${draft.cases.length + 1}`,
                            draft.cases.map((item) => item.name),
                          ),
                        },
                      ],
                    })
                  }
                >
                  <Plus size={14} /> Add task
                </button>
              </fieldset>
            )}

            {tab === 'setups' && (
              <fieldset disabled={busy || running} className="eval-step">
                <p className="subtle">
                  A setup is one way of running the tasks: a prompt preset, and optionally its own
                  model or workspace access. Without them it uses the default model and sandbox
                  mode. Runs are unattended, so a run that would wait for approval is stopped and
                  fails.
                </p>
                {duplicates.map(([first, second]) => (
                  <p className="notice" key={`${first}-${second}`}>
                    <AlertTriangle size={14} /> {draft.variants[first].name} and{' '}
                    {draft.variants[second].name} are the same; their results can differ only by
                    chance. Change the preset, model or access of one.
                  </p>
                ))}
                <div className="eval-setups">
                  {draft.variants.map((item, index) => (
                    <div
                      className="settings-card eval-card"
                      key={item.id}
                      role="group"
                      aria-label={`Setup ${index + 1}`}
                    >
                      <div className="eval-card-head">
                        <input
                          className="eval-inline-name"
                          aria-label="Setup name"
                          value={item.name}
                          maxLength={120}
                          onChange={(event) => {
                            setRenamed(new Set(renamed).add(item.id));
                            updateSetup(index, { name: event.target.value });
                          }}
                        />
                        <button
                          className="icon-button"
                          aria-label={`Remove ${item.name}`}
                          disabled={draft.variants.length === 1}
                          onClick={() =>
                            setDraft({
                              ...draft,
                              variants: draft.variants.filter((_, i) => i !== index),
                            })
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                      <label>
                        Prompt preset
                        <select
                          value={item.preset_id}
                          onChange={(event) =>
                            updateSetup(index, { preset_id: event.target.value })
                          }
                        >
                          {data.presets.map((preset) => (
                            <option key={preset.id} value={preset.id}>
                              {preset.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Model
                        <select
                          value={
                            item.model
                              ? JSON.stringify([item.model.provider_id, item.model.model])
                              : ''
                          }
                          onChange={(event) => {
                            const value = event.target.value;
                            if (!value) return updateSetup(index, { model: null });
                            const [provider_id, model] = JSON.parse(value) as [string, string];
                            updateSetup(index, {
                              model: { provider_id, model, reasoning_effort: null },
                            });
                          }}
                        >
                          <option value="">Default model</option>
                          {models.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Workspace access
                        <select
                          value={item.access ?? ''}
                          onChange={(event) =>
                            updateSetup(index, {
                              access: (event.target.value || null) as EvalVariant['access'],
                            })
                          }
                        >
                          <option value="">Default</option>
                          <option value="read-only">Read only</option>
                          <option value="workspace-write">Workspace write</option>
                        </select>
                      </label>
                    </div>
                  ))}
                </div>
                <button
                  className="text-button"
                  disabled={draft.variants.length >= 10}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      variants: [
                        ...draft.variants,
                        newSetup(data.presets[0]?.id ?? '', draft.variants, data),
                      ],
                    })
                  }
                >
                  <Plus size={14} /> Add setup
                </button>
              </fieldset>
            )}

            {tab === 'results' &&
              (batch ? (
                <EvalResults
                  batch={batch}
                  batches={batches.data ?? []}
                  open={open}
                  onOpen={setOpen}
                  onBatch={(id) => {
                    setBatchId(id);
                    setOpen(null);
                  }}
                />
              ) : (
                <div className="empty-inspector">
                  No results yet. Add tasks and setups, then run the evaluation to compare them.
                </div>
              ))}
          </div>
        </div>
        <footer className="modal-footer eval-footer">
          <p className="subtle">
            {draft.cases.length} task{draft.cases.length === 1 ? '' : 's'} × {draft.variants.length}{' '}
            setup{draft.variants.length === 1 ? '' : 's'} ={' '}
            <strong>
              {runs} run{runs === 1 ? '' : 's'}
            </strong>{' '}
            in {workspace}, one after another. Real models may cost money.
          </p>
          <div className="row">
            <button
              className="button"
              disabled={busy || running || (!dirty && !!draft.id)}
              onClick={() => void perform(async () => void (await save()))}
            >
              {dirty || !draft.id ? 'Save' : 'Saved'}
            </button>
            {running ? (
              <button
                className="button danger"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await request(`/batches/${batch!.id}/cancel`, 'POST');
                    await batches.refetch();
                  })
                }
              >
                <Square size={13} /> Stop
              </button>
            ) : (
              <button className="button primary" disabled={busy} onClick={() => void perform(run)}>
                <Play size={13} /> Run {runs} run{runs === 1 ? '' : 's'}
              </button>
            )}
          </div>
        </footer>
      </section>
    </div>
  );
}
