import { type ReactNode, type ThHTMLAttributes, type TdHTMLAttributes } from "react";

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`overflow-x-auto rounded-lg border border-border-subtle ${className}`}>
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="bg-bg-panel border-b border-border-subtle">
      <tr>{children}</tr>
    </thead>
  );
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-border-subtle">{children}</tbody>;
}

interface ThProps extends ThHTMLAttributes<HTMLTableCellElement> {
  children: ReactNode;
}

export function Th({ children, className = "", ...rest }: ThProps) {
  return (
    <th
      {...rest}
      className={`px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-fg-muted ${className}`}
    >
      {children}
    </th>
  );
}

interface TrProps {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}

export function Tr({ children, className = "", onClick }: TrProps) {
  const interactive = onClick ? "cursor-pointer hover:bg-bg-hover transition-colors" : "";
  return (
    <tr onClick={onClick} className={`${interactive} ${className}`}>
      {children}
    </tr>
  );
}

interface TdProps extends TdHTMLAttributes<HTMLTableCellElement> {
  children: ReactNode;
  mono?: boolean;
}

export function Td({ children, className = "", mono = false, ...rest }: TdProps) {
  const monoCls = mono ? "font-mono text-xs tabular-nums text-fg-secondary" : "text-fg-primary";
  return (
    <td {...rest} className={`px-4 py-3 ${monoCls} ${className}`}>
      {children}
    </td>
  );
}
