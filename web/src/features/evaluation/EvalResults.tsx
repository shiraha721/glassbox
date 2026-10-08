import { Check, Circle, ExternalLink, LoaderCircle, X } from 'lucide-react';
import type { EvalBatch } from '../../api/client';
import { StatusBadge } from '../../shared/StatusBadge';

function percent(rate: number | null | undefined) {
  return rate === null || rate === undefined ? '—' : `${Math.round(rate * 100)}%`;
}

function dollars(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `$${value.toFixed(4)}`;
}

function amount(value: number | null | undefined, unit = '') {
  if (value === null || value === undefined) return '—';
  // Seconds under ten keep a decimal; token averages are whole numbers.
  const shown = unit === 's' && value < 10 ? value.toFixed(1) : Math.round(value).toLocaleString();
  return `${shown}${unit}`;
}

type Verdict = 'pass' | 'fail' | 'running' | 'pending';

function verdictOf(passed: boolean | null | undefined, status: string | undefined): Verdict {
  if (passed === true) return 'pass';
  if (passed === false) return 'fail';
  return status && status !== 'pending' ? 'running' : 'pending';
}

const verdictText: Record<Verdict, string> = {
  pass: 'Pass',
  fail: 'Fail',
  running: 'Running',
  pending: 'Waiting',
};

function VerdictIcon({ verdict }: { verdict: Verdict }) {
  if (verdict === 'pass') return <Check size={13} />;
  if (verdict === 'fail') return <X size={13} />;
  if (verdict === 'running') return <LoaderCircle size={13} className="spin" />;
  return <Circle size={11} />;
}

/** One comparison: progress, a ranking of the setups by pass rate, and every task's verdict. */
export function EvalResults({
  batch,
  batches,
  open,
  onOpen,
  onBatch,
}: {
  batch: EvalBatch;
  batches: EvalBatch[];
  open: string | null;
  onOpen: (key: string | null) => void;
  onBatch: (id: string) => void;
}) {
  const running = batch.status === 'running';
  const best = Math.max(...batch.variants.map((item) => item.pass_rate ?? -1));
  // "Best" marks a single leader; a tie has none.
  const leaders = batch.variants.filter((item) => item.pass_rate === best && best >= 0);
  const leader = leaders.length === 1 ? leaders[0].variant_id : null;
  const ranked = [...batch.variants].sort((a, b) => (b.pass_rate ?? -1) - (a.pass_rate ?? -1));
  const cell = (caseId: string, variantId: string) =>
    batch.items.find((item) => item.case_id === caseId && item.variant_id === variantId);
  const opened = open
    ? batch.items.find((item) => `${item.case_id}:${item.variant_id}` === open)
    : undefined;
  const openedCase = batch.cases.find((item) => item.id === opened?.case_id);
  const openedSetup = batch.variants.find((item) => item.variant_id === opened?.variant_id);
  const progress = batch.total ? Math.round((batch.completed / batch.total) * 100) : 0;

  return (
    <section className="eval-results" aria-label="Comparison results">
      <div className="eval-results-head">
        <StatusBadge status={batch.status} testId="batch-status" />
        <span>
          {batch.completed} of {batch.total} runs
        </span>
        {batches.length > 1 ? (
          <select
            aria-label="Comparison"
            value={batch.id}
            onChange={(event) => onBatch(event.target.value)}
          >
            {batches.map((item) => (
              <option key={item.id} value={item.id}>
                {new Date(item.created_at).toLocaleString()} · {item.status}
              </option>
            ))}
          </select>
        ) : (
          <span className="subtle">{new Date(batch.created_at).toLocaleString()}</span>
        )}
      </div>
      {running && (
        <div
          className="usage-bar"
          role="progressbar"
          aria-label="Comparison progress"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
      )}
      {batch.error && (
        <p className="error-text" role="alert">
          {batch.error}
        </p>
      )}

      <h4 className="eval-section-title">Which setup passed most tasks</h4>
      <ol className="eval-ranking" aria-label="Setups by pass rate">
        {ranked.map((item) => (
          <li key={item.variant_id} data-best={item.variant_id === leader || undefined}>
            <div className="eval-ranking-name">
              <strong title={item.name}>{item.name}</strong>
              {item.variant_id === leader && <span className="badge">Best</span>}
            </div>
            <div className="eval-ranking-bar" aria-hidden="true">
              <span style={{ width: `${Math.round((item.pass_rate ?? 0) * 100)}%` }} />
            </div>
            <div className="eval-ranking-rate">
              <strong>{percent(item.pass_rate)}</strong>
              <span className="subtle">
                {item.passed} of {item.graded} passed
              </span>
            </div>
            <p className="subtle eval-ranking-cost">
              Per task: {amount(item.avg_input_tokens)} input · {amount(item.avg_output_tokens)}{' '}
              output tokens · {amount(item.avg_active_seconds, 's')} · {dollars(item.avg_cost)}
            </p>
          </li>
        ))}
      </ol>

      <h4 className="eval-section-title">Each task under each setup</h4>
      <div className="eval-grid-scroll">
        <table className="eval-table eval-matrix">
          <thead>
            <tr>
              <th>Task</th>
              {batch.variants.map((item) => (
                <th key={item.variant_id}>{item.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {batch.cases.map((label) => (
              <tr key={label.id}>
                <th scope="row">{label.name}</th>
                {batch.variants.map((setup) => {
                  const result = cell(label.id, setup.variant_id);
                  const key = `${label.id}:${setup.variant_id}`;
                  const verdict = verdictOf(result?.passed, result?.status);
                  const done = verdict === 'pass' || verdict === 'fail';
                  return (
                    <td key={key}>
                      <button
                        className="eval-cell"
                        data-result={verdict}
                        aria-pressed={open === key}
                        aria-label={`${label.name} under ${setup.name}: ${verdictText[verdict]}`}
                        disabled={!done}
                        onClick={() => onOpen(open === key ? null : key)}
                      >
                        <VerdictIcon verdict={verdict} />
                        {verdictText[verdict]}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {opened ? (
        <div className="settings-card eval-detail" aria-label="Result detail">
          <div className="eval-detail-head">
            <strong>
              {openedCase?.name} under {openedSetup?.name}
            </strong>
            <StatusBadge status={opened.status} />
            {opened.run_id && (
              <a
                className="text-button"
                href={`/api/v1/runs/${opened.run_id}/trace`}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={13} /> Recorded trace
              </a>
            )}
          </div>
          <p>{opened.detail}</p>
          <p className="subtle">
            {amount(opened.input_tokens)} input · {amount(opened.output_tokens)} output tokens ·{' '}
            {amount(opened.active_seconds, 's')} · {dollars(opened.cost)}
          </p>
          <pre className="context-summary">{opened.output || 'No final reply.'}</pre>
        </div>
      ) : (
        <p className="subtle">Select a result to see why it passed or failed, and the reply.</p>
      )}
    </section>
  );
}
