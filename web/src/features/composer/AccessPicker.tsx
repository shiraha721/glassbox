import { Check, ChevronDown } from 'lucide-react';
import type { WorkspaceAccess } from '../../api/client';
import { Popover } from '../../shared/Popover';
import { accessOptions } from './choices';

/** Choose what the next Run may do to the workspace, beside the model choice. */
export function AccessPicker({
  access,
  disabled,
  onChange,
}: {
  access: WorkspaceAccess;
  disabled?: boolean;
  onChange: (access: WorkspaceAccess) => void;
}) {
  const current = accessOptions.find((option) => option.value === access) || accessOptions[2];
  const Icon = current.icon;
  return (
    <Popover
      label="Workspace access"
      title="What the next run may do to the workspace"
      className="access-picker"
      disabled={disabled}
      trigger={
        <>
          <Icon size={14} />
          <span className="trigger-text">{current.label}</span>
          <ChevronDown size={13} />
        </>
      }
    >
      {(close) => (
        <div className="picker">
          <div className="picker-group-label">Workspace access</div>
          {accessOptions.map((option) => {
            const OptionIcon = option.icon;
            return (
              <button
                key={option.value}
                className="picker-option rich"
                aria-pressed={option.value === access}
                onClick={() => {
                  onChange(option.value);
                  close();
                }}
              >
                <OptionIcon size={15} />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
                {option.value === access && <Check size={14} />}
              </button>
            );
          })}
          <p className="picker-note">Commands always run in the sandbox without network access.</p>
        </div>
      )}
    </Popover>
  );
}
