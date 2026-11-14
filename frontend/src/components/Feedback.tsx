import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Inbox } from "lucide-react";

export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-lg bg-surface-3 ${className}`} />;
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-3 py-2">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** A card-shaped placeholder grid, for dashboards and lists of cards. */
export function SkeletonCards({ count = 4, className = "h-28" }: { count?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} className={`${className} rounded-card`} />
      ))}
    </div>
  );
}

/** Nothing to show yet: say so plainly, and offer one clear next step. */
export function EmptyState({
  title,
  children,
  icon: Icon = Inbox,
  action,
}: {
  title: string;
  children?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-card border border-dashed border-line-strong px-6 py-12 text-center animate-fade-in">
      <span className="mb-3 grid size-11 place-items-center rounded-2xl bg-surface-2 text-muted" aria-hidden="true">
        <Icon className="size-5" strokeWidth={1.75} />
      </span>
      <p className="font-medium text-ink">{title}</p>
      {children ? <div className="mt-1 max-w-sm text-sm text-muted">{children}</div> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-card border border-danger-line bg-danger-soft p-4 text-sm text-danger">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div>
        <p>{message}</p>
        {onRetry ? (
          <button type="button" className="btn-secondary mt-3" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}
