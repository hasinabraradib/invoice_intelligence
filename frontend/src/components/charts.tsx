"use client";
// Charts styled to the design system: colours come from CSS variables, so they follow
// dark mode; minimal gridlines, rounded bars, quiet tooltips. Every chart has a text
// alternative for screen readers.
import { Area, AreaChart, Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useReducedMotion } from "@/components/ui";

const tooltip = {
  contentStyle: {
    background: "var(--surface)",
    border: "1px solid var(--line)",
    borderRadius: 12,
    boxShadow: "var(--shadow-lift)",
    fontSize: 12,
    color: "var(--ink)",
    padding: "6px 10px",
  },
  labelStyle: { color: "var(--muted)", marginBottom: 2 },
  itemStyle: { color: "var(--ink)", padding: 0 },
  cursor: { fill: "var(--surface-2)" },
};

/** A tiny trend line for a KPI card. Values may contain nulls (no data that day). */
export function Sparkline({ data, color = "var(--accent)", label }: { data: (number | null)[]; color?: string; label: string }) {
  const reduced = useReducedMotion();
  const id = `spark-${label.replace(/\W+/g, "-")}`;
  const points = data.map((value, i) => ({ i, value }));
  return (
    <div className="h-10 w-full" role="img" aria-label={`${label}, last ${data.length} days`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.22} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={1.75}
            fill={`url(#${id})`}
            connectNulls
            dot={false}
            isAnimationActive={!reduced}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export type DayPoint = { date: string; value: number };

function shortDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Documents per day: rounded bars, a faint baseline, no vertical gridlines. */
export function DailyBars({ data, label }: { data: DayPoint[]; label: string }) {
  const reduced = useReducedMotion();
  const total = data.reduce((sum, d) => sum + d.value, 0);
  return (
    <figure className="m-0">
      <div className="h-56 w-full" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -24 }}>
            <XAxis
              dataKey="date"
              tickFormatter={shortDay}
              tickLine={false}
              axisLine={{ stroke: "var(--chart-grid)" }}
              tick={{ fill: "var(--faint)", fontSize: 11 }}
              interval="preserveStartEnd"
              minTickGap={18}
            />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--faint)", fontSize: 11 }} width={44} />
            <Tooltip {...tooltip} labelFormatter={(v) => shortDay(String(v))} formatter={(v) => [v, "documents"]} />
            <Bar dataKey="value" fill="var(--accent)" radius={[6, 6, 2, 2]} maxBarSize={28} isAnimationActive={!reduced} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="sr-only">
        {label}: {total} in total. {data.map((d) => `${shortDay(d.date)}: ${d.value}`).join(", ")}.
      </figcaption>
    </figure>
  );
}

export type Slice = { key: string; label: string; value: number; color: string };

/** A status donut. The legend next to it carries the same numbers as text. */
export function Donut({ data, centerLabel, centerValue }: { data: Slice[]; centerLabel: string; centerValue: string }) {
  const reduced = useReducedMotion();
  const shown = data.filter((d) => d.value > 0);
  return (
    <div className="relative mx-auto size-40 shrink-0" aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={shown.length ? shown : [{ key: "none", label: "None", value: 1, color: "var(--surface-3)" }]}
            dataKey="value"
            nameKey="label"
            innerRadius="72%"
            outerRadius="100%"
            paddingAngle={shown.length > 1 ? 2 : 0}
            cornerRadius={4}
            stroke="none"
            isAnimationActive={!reduced}
          >
            {(shown.length ? shown : [{ key: "none", color: "var(--surface-3)" }]).map((d) => (
              <Cell key={d.key} fill={d.color} />
            ))}
          </Pie>
          {shown.length ? <Tooltip {...tooltip} /> : null}
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 grid place-items-center text-center">
        <div>
          <div className="num text-2xl font-semibold text-ink">{centerValue}</div>
          <div className="text-xs text-muted">{centerLabel}</div>
        </div>
      </div>
    </div>
  );
}
