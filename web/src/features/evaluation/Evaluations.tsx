import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FlaskConical, Play, Plus, Square, Trash2, X } from 'lucide-react';
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

type Draft = {
  id: string | null;
  revision: number;
  name: string;
  cases: EvalCase[];
  variants: EvalVariant[];
};

const checkKinds: { value: EvalCheck['kind']; label: string }[] = [
  { value: 'contains', label: 'Output contains' },
  { value: 'not_contains', label: 'Output does not contain' },
  { value: 'equals', label: 'Output equals' },
  { value: 'regex', label: 'Output matches regex' },
  { value: 'succeeds', label: 'Run succeeds' },
];

function newCase(index: number): EvalCase {
  return {
    id: crypto.randomUUID(),
    name: `Case ${index}`,
    input: '',
    variables: {},
    check: { kind: 'contains', value: '', ignore_case: true },
  };
}

function newVariant(index: number, presetId: string): EvalVariant {
  return {
    id: crypto.randomUUID(),
    name: `Variant ${String.fromCharCode(64 + index)}`,
    preset_id: presetId,
    model: null,
    access: null,
  };
}

function blank(presetId: string): Draft {
  return {
    id: null,
    revision: 0,
    name: '',
    cases: [newCase(1)],
    variants: [newVariant(1, presetId), newVariant(2, presetId)],
  };
}

function fromEval(value: Eval): Draft {
  return {
    id: value.id,
    revision: value.revision,
    name: value.name,
    cases: value.cases,
    variants: value.variants,
  };
}

function percent(rate: number | null | undefined) {
  return rate === null || rate === undefined ? '—' : `${Math.round(rate * 100)}%`;
}

function dollars(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `$${value.toFixed(4)}`;
}

function amount(value: number | null | undefined, unit = '') {
  return value === null || value === undefined ? '—' : `${value.toLocaleString()}${unit}`;
}

/** Compare prompts on the same tasks: each case runs under each variant as an ordinary Run,
 * is graded from its final text, and every variant gets a pass rate and average cost. */
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
  const defaultPreset = data.presets[0]?.id ?? '';
  const [draft, setDraft] = useState<Draft>(() => blank(defaultPreset));
  const [baseline, setBaseline] = useState(() => JSON.stringify(blank(defaultPreset)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [batchId, setBatchId] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const dirty = JSON.stringify(draft) !== baseline;
  const project = data.projects.find((item) => item.id === projectId);

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
  const load = (value: Draft) => {
    setDraft(value);
    setBaseline(JSON.stringify(value));
    setBatchId(null);
    setOpen(null);
  };
  const select = (id: string | null) => {
    if (dirty && !window.confirm('Discard unsaved evaluation changes?')) return;
    if (!id) {
      load(blank(defaultPreset));
      return;
    }
    void perform(async () => load(fromEval(await request<Eval>(`/evals/${id}`))));
  };
  const save = async (): Promise<string> => {
    const body = { name: draft.name, cases: draft.cases, variants: draft.variants };
    const saved = draft.id
      ? await request<Eval>(`/evals/${draft.id}`, 'PUT', {
          ...body,
          expected_revision: draft.revision,
        })
      : await request<Eval>('/evals', 'POST', body);
    load(fromEval(saved));
    await onChanged();
    return saved.id;
  };
  const run = async () => {
    const id = dirty || !draft.id ? await save() : draft.id;
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const started = await request<EvalBatch>(`/evals/${id}/batches${query}`, 'POST');
    setBatchId(started.id);
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
  const updateVariant = (index: number, change: Partial<EvalVariant>) =>
    setDraft({
      ...draft,
      variants: draft.variants.map((item, i) => (i === index ? { ...item, ...change } : item)),
    });

  const models = [
    { value: JSON.stringify([SIMULATED, 'simulated']), label: 'Simulated model' },
    ...data.config.providers.flatMap((provider) =>
      provider.models.map((model) => ({
        value: JSON.stringify([provider.id, model]),
        label: `${provider.name} · ${model}`,
      })),
    ),
  ];
  const result = (caseId: string, variantId: string) =>
    batch?.items.find((item) => item.case_id === caseId && item.variant_id === variantId);
  const best = Math.max(...(batch?.variants.map((item) => item.pass_rate ?? -1) ?? [-1]));
  const opened = open
    ? batch?.items.find((item) => `${item.case_id}:${item.variant_id}` === open)
    : undefined;

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
                  {item.case_count} cases · {item.variant_count} variants
                  {item.last_status ? ` · ${item.last_status}` : ''}
                </small>
              </button>
            ))}
          </nav>
          <div className="settings-content">
            <h3 className="settings-page-title">{draft.id ? draft.name : 'New evaluation'}</h3>
            <p className="page-intro">
              Each case runs once under each variant as an ordinary Run in{' '}
              <strong>{project?.name || 'the default workspace'}</strong>, then is graded from its
              final reply. Runs are unattended: one that waits for approval is stopped and counts as
              failed. Results use your configured models and may cost money.
            </p>
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}
            <fieldset disabled={busy || running}>
              <label>
                Evaluation name
                <input
                  value={draft.name}
                  maxLength={80}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                />
              </label>
              <h4 className="eval-section-title">Cases</h4>
              {draft.cases.map((item, index) => (
                <div className="settings-card eval-card" key={item.id}>
                  <div className="row">
                    <label>
                      Case name
                      <input
                        value={item.name}
                        maxLength={120}
                        onChange={(event) => updateCase(index, { name: event.target.value })}
                      />
                    </label>
                    <button
                      className="icon-button"
                      aria-label={`Remove case ${item.name}`}
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
                      onChange={(event) => updateCase(index, { input: event.target.value })}
                    />
                  </label>
                  <div className="row">
                    <label>
                      Passes when
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
                        {item.check?.kind === 'regex' ? 'Pattern' : 'Text'}
                        <input
                          value={item.check?.value ?? ''}
                          onChange={(event) => updateCheck(index, { value: event.target.value })}
                        />
                      </label>
                    )}
                  </div>
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
              ))}
              <button
                className="text-button"
                onClick={() =>
                  setDraft({ ...draft, cases: [...draft.cases, newCase(draft.cases.length + 1)] })
                }
              >
                <Plus size={14} /> Add case
              </button>
              <h4 className="eval-section-title">Variants</h4>
              {draft.variants.map((item, index) => (
                <div className="settings-card eval-card" key={item.id}>
                  <div className="row">
                    <label>
                      Variant name
                      <input
                        value={item.name}
                        maxLength={120}
                        onChange={(event) => updateVariant(index, { name: event.target.value })}
                      />
                    </label>
                    <button
                      className="icon-button"
                      aria-label={`Remove variant ${item.name}`}
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
                  <div className="row">
                    <label>
                      Prompt preset
                      <select
                        value={item.preset_id}
                        onChange={(event) =>
                          updateVariant(index, { preset_id: event.target.value })
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
                          if (!value) return updateVariant(index, { model: null });
                          const [provider_id, model] = JSON.parse(value) as [string, string];
                          updateVariant(index, {
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
                          updateVariant(index, {
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
                </div>
              ))}
              <button
                className="text-button"
                disabled={draft.variants.length >= 10}
                onClick={() =>
                  setDraft({
                    ...draft,
                    variants: [
                      ...draft.variants,
                      newVariant(draft.variants.length + 1, defaultPreset),
                    ],
                  })
                }
              >
                <Plus size={14} /> Add variant
              </button>
            </fieldset>
            <div className="dialog-actions eval-actions">
              {draft.id && (
                <button
                  className="button danger"
                  disabled={busy || running}
                  onClick={() => {
                    if (!window.confirm(`Delete ${draft.name} and its results?`)) return;
                    void perform(async () => {
                      await request(`/evals/${draft.id}`, 'DELETE');
                      load(blank(defaultPreset));
                      await onChanged();
                    });
                  }}
                >
                  <Trash2 size={14} /> Delete
                </button>
              )}
              <button
                className="button"
                disabled={busy || running || (!dirty && !!draft.id)}
                onClick={() => void perform(async () => void (await save()))}
              >
                Save
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
                  <Square size={13} /> Stop comparison
                </button>
              ) : (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => void perform(run)}
                >
                  <Play size={13} /> Run comparison
                </button>
              )}
            </div>
            {batch && (
              <section className="eval-results" aria-label="Comparison results">
                <div className="row">
                  <h4 className="eval-section-title">Results</h4>
                  <StatusBadge status={batch.status} testId="batch-status" />
                  <span className="subtle">
                    {batch.completed} of {batch.total} runs ·{' '}
                    {new Date(batch.created_at).toLocaleString()}
                  </span>
                  {(batches.data?.length ?? 0) > 1 && (
                    <select
                      aria-label="Comparison"
                      value={batch.id}
                      onChange={(event) => {
                        setBatchId(event.target.value);
                        setOpen(null);
                      }}
                    >
                      {batches.data!.map((item) => (
                        <option key={item.id} value={item.id}>
                          {new Date(item.created_at).toLocaleString()} · {item.status}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {batch.error && (
                  <p className="error-text" role="alert">
                    {batch.error}
                  </p>
                )}
                <table className="eval-table">
                  <thead>
                    <tr>
                      <th>Variant</th>
                      <th>Pass rate</th>
                      <th>Avg input tokens</th>
                      <th>Avg output tokens</th>
                      <th>Avg time</th>
                      <th>Avg cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batch.variants.map((item) => (
                      <tr
                        key={item.variant_id}
                        data-best={(item.pass_rate === best && best >= 0) || undefined}
                      >
                        <th scope="row">{item.name}</th>
                        <td>
                          {percent(item.pass_rate)}{' '}
                          <span className="subtle">
                            ({item.passed}/{item.graded})
                          </span>
                        </td>
                        <td>{amount(item.avg_input_tokens)}</td>
                        <td>{amount(item.avg_output_tokens)}</td>
                        <td>{amount(item.avg_active_seconds, 's')}</td>
                        <td>{dollars(item.avg_cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <table className="eval-table eval-matrix">
                  <thead>
                    <tr>
                      <th>Case</th>
                      {batch.variants.map((item) => (
                        <th key={item.variant_id}>{item.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {batch.cases.map((label) => (
                      <tr key={label.id}>
                        <th scope="row">{label.name}</th>
                        {batch.variants.map((variant) => {
                          const cell = result(label.id, variant.variant_id);
                          const key = `${label.id}:${variant.variant_id}`;
                          const verdict =
                            cell?.passed === true
                              ? 'pass'
                              : cell?.passed === false
                                ? 'fail'
                                : 'pending';
                          return (
                            <td key={key}>
                              <button
                                className="eval-cell"
                                data-result={verdict}
                                aria-pressed={open === key}
                                aria-label={`${label.name} under ${variant.name}: ${verdict}`}
                                disabled={!cell || verdict === 'pending'}
                                onClick={() => setOpen(open === key ? null : key)}
                              >
                                {verdict === 'pass'
                                  ? 'Pass'
                                  : verdict === 'fail'
                                    ? 'Fail'
                                    : (cell?.status ?? 'pending')}
                              </button>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {opened && (
                  <div className="settings-card eval-detail" aria-label="Result detail">
                    <p>
                      <StatusBadge status={opened.status} /> {opened.detail}
                    </p>
                    <p className="subtle">
                      {amount(opened.input_tokens)} input · {amount(opened.output_tokens)} output ·{' '}
                      {amount(opened.active_seconds, 's')} · {dollars(opened.cost)} · Run{' '}
                      {opened.run_id?.slice(0, 8) ?? '—'}
                    </p>
                    <pre className="context-summary">{opened.output || 'No final reply.'}</pre>
                  </div>
                )}
              </section>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
