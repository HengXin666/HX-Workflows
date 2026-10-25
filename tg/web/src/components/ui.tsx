import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import * as Select from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";

/**
 * HX-Workflows 接入 Agent Notes v2
 * .agents/notes/implemented/process/2026-10-08-repository-agent-notes-v2-adoption.md
 */
export function Card({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`rounded-2xl border border-slate-800/80 bg-slate-950/70 shadow-xl shadow-black/10 ${className}`} {...props} />;
}

export function CardHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-slate-800/70 px-5 py-4">
      <div>
        <h2 className="text-base font-semibold text-white">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-6 text-slate-400">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function UiButton({ className = "", variant = "default", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" }) {
  const variants = {
    default: "border-slate-700 bg-slate-900 text-slate-200 hover:border-sky-500/60 hover:bg-slate-800",
    primary: "border-sky-400/40 bg-sky-500 text-white hover:bg-sky-400",
    danger: "border-rose-500/30 bg-rose-500/10 text-rose-200 hover:bg-rose-500/20",
  };
  return <button className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-45 ${variants[variant]} ${className}`} {...props} />;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" }) {
  const tones = {
    neutral: "border-slate-700 bg-slate-900 text-slate-300",
    good: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    warn: "border-amber-500/30 bg-amber-500/10 text-amber-200",
  };
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

export function UiSelect({ value, placeholder, options, onChange }: { value: string; placeholder: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return <Select.Root value={value || undefined} onValueChange={onChange}><Select.Trigger className="ui-select-trigger" aria-label={placeholder}><Select.Value placeholder={placeholder}/><Select.Icon><ChevronDown/></Select.Icon></Select.Trigger><Select.Portal><Select.Content className="ui-select-content" position="popper" sideOffset={6}><Select.Viewport>{options.map(option=><Select.Item className="ui-select-item" value={option.value} key={option.value}><Select.ItemText>{option.label}</Select.ItemText><Select.ItemIndicator><Check/></Select.ItemIndicator></Select.Item>)}</Select.Viewport></Select.Content></Select.Portal></Select.Root>;
}
