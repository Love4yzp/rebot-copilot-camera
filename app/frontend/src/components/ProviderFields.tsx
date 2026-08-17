import type { ProviderField } from "../types";

interface Props {
  fields: ProviderField[];
  values: Record<string, unknown>;
  disabled?: boolean;
  onChange: (key: string, value: unknown) => void;
}

/**
 * Draws a provider's controls from its manifest.
 *
 * The host owns the widgets and plugins only describe them, so every provider
 * inherits the ≥44px targets, the focus rings and the reduced-motion behaviour
 * that were settled once. A plugin that shipped its own markup would ship its
 * own colours with it, and here colour is a status channel — four of them, each
 * owning one machine state — not a palette to decorate with.
 *
 * Three kinds, and that is the whole contract. They are the three this sheet
 * already had for the shutter; wanting a fourth is a change to this file, on
 * purpose.
 */
export function ProviderFields({ fields, values, disabled, onChange }: Props) {
  return (
    <>
      {fields.filter((field) => visible(field, values)).map((field) => (
        <div className="sheet__field" key={field.key}>
          <span className="sheet__label" id={`f-${field.key}`}>
            {field.label}
          </span>
          {render(field, values[field.key], disabled ?? false, (v) => onChange(field.key, v))}
        </div>
      ))}
    </>
  );
}

/**
 * A field can hide until another reaches a threshold — a gap between frames
 * means nothing until there are two of them.
 *
 * The condition is a `{key, min}` pair rather than an expression string: a UI
 * that evaluates arbitrary expressions handed to it by an installed package is
 * the start of a bad afternoon.
 */
function visible(field: ProviderField, values: Record<string, unknown>): boolean {
  if (!field.when) return true;
  return Number(values[field.when.key] ?? 0) >= field.when.min;
}

function render(
  field: ProviderField,
  value: unknown,
  disabled: boolean,
  onChange: (value: unknown) => void,
) {
  switch (field.kind) {
    case "switch": {
      const on = Boolean(value);
      return (
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby={`f-${field.key}`}
          disabled={disabled}
          className={`sheet__switch ${on ? "on" : ""}`}
          onClick={() => onChange(!on)}
        >
          {on ? "开" : "关"}
        </button>
      );
    }

    case "stepper": {
      const min = field.min ?? 1;
      const max = field.max ?? 10;
      const n = clamp(Number(value ?? min), min, max);
      return (
        <div className="sheet__stepper">
          <button
            type="button"
            className="sheet__stepper-btn"
            onClick={() => onChange(clamp(n - 1, min, max))}
            disabled={disabled || n <= min}
            aria-label={`减少${field.label}`}
          >
            −
          </button>
          <span className="sheet__stepper-num">{n}</span>
          <button
            type="button"
            className="sheet__stepper-btn"
            onClick={() => onChange(clamp(n + 1, min, max))}
            disabled={disabled || n >= max}
            aria-label={`增加${field.label}`}
          >
            +
          </button>
        </div>
      );
    }

    case "tiers": {
      const values = field.values ?? [];
      // Snap to the nearest tier, so a value typed by an API client or left by
      // an older plugin version still shows as one of the choices rather than
      // as nothing selected.
      const current = nearest(values, Number(value ?? values[0] ?? 0));
      return (
        <div className="sheet__tiers" role="radiogroup" aria-labelledby={`f-${field.key}`}>
          {values.map((tier, i) => (
            <button
              key={tier}
              type="button"
              role="radio"
              aria-checked={current === i}
              disabled={disabled}
              className={`sheet__tier ${current === i ? "selected" : ""}`}
              onClick={() => onChange(tier)}
            >
              {tier}
              {field.unit ?? ""}
            </button>
          ))}
        </div>
      );
    }

    default:
      return null;
  }
}

const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n));

function nearest(values: readonly number[], value: number): number {
  let best = 0;
  for (let i = 1; i < values.length; i++) {
    if (Math.abs(values[i] - value) < Math.abs(values[best] - value)) best = i;
  }
  return best;
}
