import { Check, Circle, LoaderCircle, ListChecks } from 'lucide-react';
import type { Run } from '../../api/client';

/** Render the latest recorded plan as agent progress, without equating completion with verified success. */
export function TaskPlan({ plan }: { plan: NonNullable<Run['plan']> }) {
  const completed = plan.plan.filter((item) => item.status === 'completed').length;
  return (
    <details className="task-plan" open>
      <summary>
        <ListChecks size={16} /> Agent plan{' '}
        <span className="count">
          {completed}/{plan.plan.length}
        </span>
      </summary>
      {plan.explanation && <p className="subtle">{plan.explanation}</p>}
      <ol>
        {plan.plan.map((item, index) => (
          <li key={index} data-status={item.status}>
            {item.status === 'completed' ? (
              <Check size={15} />
            ) : item.status === 'in_progress' ? (
              <LoaderCircle size={15} />
            ) : (
              <Circle size={13} />
            )}
            <span>{item.step}</span>
            <span className="subtle">{item.status.replaceAll('_', ' ')}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
