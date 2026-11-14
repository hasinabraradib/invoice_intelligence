"use client";
// The one select in the app: Radix UI Select, styled like our inputs. Keyboard support
// (arrows, type-ahead, Enter, Escape) and ARIA come from Radix.
import * as RS from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

// Radix reserves "" for "no value", so an empty option is mapped to this sentinel.
const EMPTY = "__empty__";

export type SelectOption = { value: string; label: string; detail?: ReactNode; textValue?: string };

type Props = {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  /** Shown when no option matches the value. */
  placeholder?: string;
  "aria-label"?: string;
  className?: string;
  size?: "md" | "sm";
  disabled?: boolean;
};

export function Select({ id, value, onChange, options, placeholder, className = "", size = "md", disabled, ...rest }: Props) {
  const current = options.some((o) => o.value === value) ? value || EMPTY : undefined;
  return (
    <RS.Root value={current} onValueChange={(v) => onChange(v === EMPTY ? "" : v)} disabled={disabled}>
      <RS.Trigger
        id={id}
        aria-label={rest["aria-label"]}
        className={`input flex items-center justify-between gap-2 text-left data-[placeholder]:text-muted data-[state=open]:border-ink ${
          size === "sm" ? "h-8 rounded-lg px-2.5 text-xs" : ""
        } ${className}`}
      >
        <span className="min-w-0 truncate">
          <RS.Value placeholder={placeholder} />
        </span>
        <RS.Icon asChild>
          <ChevronDown className="size-4 shrink-0 text-muted" aria-hidden="true" />
        </RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content
          position="popper"
          sideOffset={6}
          className="menu max-h-[min(22rem,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] animate-fade-in"
        >
          <RS.Viewport>
            {options.map((o) => (
              <RS.Item key={o.value || EMPTY} value={o.value || EMPTY} textValue={o.textValue ?? o.label} className="menu-item pr-8 relative">
                <span className="min-w-0">
                  <RS.ItemText>{o.label}</RS.ItemText>
                  {o.detail ? <span className="block text-xs text-muted">{o.detail}</span> : null}
                </span>
                <RS.ItemIndicator className="absolute right-2.5">
                  <Check className="size-4" aria-hidden="true" />
                </RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  );
}
