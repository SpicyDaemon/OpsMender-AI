type StatusDotTone = "green" | "amber" | "red";

const STATUS_DOT_STYLES: Record<StatusDotTone, string> = {
  green: "bg-status-low ring-status-low-border/70",
  amber: "bg-status-medium ring-status-medium-border/70",
  red: "bg-status-critical ring-status-critical-border/70",
};

export function StatusDot({
  tone,
  label,
  title,
  className = "",
}: {
  tone: StatusDotTone;
  label?: string;
  title?: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-2 ${className}`}
      title={title}
    >
      <span
        aria-hidden
        className={`h-2.5 w-2.5 rounded-full ring-2 ring-offset-1 ring-offset-bg-panel ${STATUS_DOT_STYLES[tone]}`}
      />
      {!label && title && <span className="sr-only">{title}</span>}
      {label && <span className="text-xs text-fg-secondary">{label}</span>}
    </span>
  );
}
