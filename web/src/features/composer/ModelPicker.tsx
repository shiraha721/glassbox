import { useState } from 'react';
import { Check, ChevronDown, Cpu, RefreshCw, Settings2 } from 'lucide-react';
import {
  errorMessage,
  request,
  SIMULATED,
  type Bootstrap,
  type Config,
  type ModelChoice,
} from '../../api/client';
import { Popover } from '../../shared/Popover';
import { addModels, formatOf } from '../settings/providers';
import { effortLabels, modelLabel } from './choices';

/** Refresh every saved provider's model list from its /models endpoint and save the result. */
async function refreshModels(config: Config): Promise<string[]> {
  const failures: string[] = [];
  const providers = await Promise.all(
    config.providers.map(async (profile) => {
      try {
        const { models } = await request<{ models: string[] }>(
          '/providers/models',
          'POST',
          profile,
        );
        return addModels(profile, models);
      } catch (caught) {
        failures.push(`${profile.name}: ${errorMessage(caught)}`);
        return profile;
      }
    }),
  );
  await request('/config', 'PUT', { ...config, providers });
  return failures;
}

/** Choose the model and reasoning effort for the next Run, beside the message box. */
export function ModelPicker({
  data,
  choice,
  disabled,
  onChange,
  onManage,
  onRefreshed,
}: {
  data: Bootstrap;
  choice: ModelChoice;
  disabled?: boolean;
  onChange: (choice: ModelChoice) => void;
  onManage: () => void;
  onRefreshed: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const provider = data.config.providers.find((item) => item.id === choice.provider_id);
  const levels = provider ? data.effort_levels[formatOf(provider, choice.model)] || [] : [];
  const select = (providerId: string, model: string) => {
    const profile = data.config.providers.find((item) => item.id === providerId);
    const adapter = profile && formatOf(profile, model);
    const keep = adapter && data.effort_levels[adapter]?.includes(choice.reasoning_effort || '');
    onChange({
      provider_id: providerId,
      model,
      reasoning_effort: keep ? choice.reasoning_effort : null,
    });
  };
  const refresh = async () => {
    setBusy(true);
    setNotice('');
    try {
      const failures = await refreshModels(data.config);
      await onRefreshed();
      setNotice(failures.length ? failures.join(' ') : 'Model lists updated.');
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Popover
      label="Model"
      title="Model and reasoning effort for the next run"
      className="model-picker"
      disabled={disabled}
      trigger={
        <>
          <Cpu size={14} />
          <span className="trigger-text">{modelLabel(choice)}</span>
          <ChevronDown size={13} />
        </>
      }
    >
      {(close) => (
        <div className="picker">
          <div className="picker-scroll">
            {data.config.providers.map((profile) => {
              const models =
                profile.id === choice.provider_id &&
                choice.model &&
                !profile.models.includes(choice.model)
                  ? [choice.model, ...profile.models]
                  : profile.models;
              return (
                <div className="picker-group" key={profile.id}>
                  <div className="picker-group-label">{profile.name}</div>
                  {models.map((model) => {
                    const selected = choice.provider_id === profile.id && choice.model === model;
                    return (
                      <button
                        key={model}
                        className="picker-option"
                        aria-pressed={selected}
                        onClick={() => {
                          select(profile.id, model);
                          close();
                        }}
                      >
                        <span>{model}</span>
                        {selected && <Check size={14} />}
                      </button>
                    );
                  })}
                  {!models.length && (
                    <p className="picker-empty">No models yet. Refresh the list or add one.</p>
                  )}
                </div>
              );
            })}
            <div className="picker-group">
              <div className="picker-group-label">Offline</div>
              <button
                className="picker-option"
                aria-pressed={choice.provider_id === SIMULATED}
                onClick={() => {
                  onChange({ provider_id: SIMULATED, model: 'simulated', reasoning_effort: null });
                  close();
                }}
              >
                <span>Simulated model</span>
                {choice.provider_id === SIMULATED && <Check size={14} />}
              </button>
            </div>
          </div>
          <div className="picker-section">
            <div className="picker-group-label">Reasoning effort</div>
            <div className="segmented" role="group" aria-label="Reasoning effort">
              {[null, ...levels].map((level) => (
                <button
                  key={level || 'default'}
                  aria-pressed={(choice.reasoning_effort || null) === level}
                  disabled={!provider}
                  onClick={() => onChange({ ...choice, reasoning_effort: level as never })}
                >
                  {level ? effortLabels[level] : 'Default'}
                </button>
              ))}
            </div>
            <p className="picker-note">
              {provider
                ? 'Default leaves effort to the model. Higher effort thinks longer and costs more.'
                : 'The simulated model has no reasoning effort.'}
            </p>
          </div>
          <div className="picker-actions">
            <button className="picker-action" disabled={busy} onClick={() => void refresh()}>
              <RefreshCw size={14} className={busy ? 'spin' : undefined} />
              {busy ? 'Refreshing…' : 'Refresh model list'}
            </button>
            <button
              className="picker-action"
              onClick={() => {
                close();
                onManage();
              }}
            >
              <Settings2 size={14} />
              Manage models…
            </button>
            {notice && (
              <p className="picker-note" role="status">
                {notice}
              </p>
            )}
          </div>
        </div>
      )}
    </Popover>
  );
}
