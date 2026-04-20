import type { CSSProperties } from "react";

interface SkeletonProps {
  className?: string;
  width?: string | number;
  height?: string | number;
}

export function Skeleton({ className = "", width, height }: SkeletonProps) {
  const style: CSSProperties = {};
  if (width !== undefined) style.width = typeof width === "number" ? `${width}px` : width;
  if (height !== undefined) style.height = typeof height === "number" ? `${height}px` : height;
  return (
    <div
      className={`animate-pulse rounded-sm bg-bg-hover ${className}`}
      style={style}
      aria-hidden
    />
  );
}

export function SkeletonText({
  lines = 1,
  className = "",
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={12}
          width={i === lines - 1 && lines > 1 ? "60%" : "100%"}
        />
      ))}
    </div>
  );
}

export function TableSkeleton({
  rows = 6,
  columns = 4,
}: {
  rows?: number;
  columns?: number;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border-subtle bg-bg-panel">
      <div className="flex gap-4 border-b border-border-subtle bg-bg-elevated px-4 py-3">
        {Array.from({ length: columns }).map((_, i) => (
          <Skeleton key={i} height={10} className="flex-1" />
        ))}
      </div>
      <div className="divide-y divide-border-subtle">
        {Array.from({ length: rows }).map((_, rowIdx) => (
          <div key={rowIdx} className="flex gap-4 px-4 py-3.5">
            {Array.from({ length: columns }).map((_, colIdx) => (
              <Skeleton
                key={colIdx}
                height={14}
                className="flex-1"
                width={colIdx === columns - 1 ? "40%" : undefined}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-md border border-border-subtle bg-bg-panel p-5">
      <Skeleton height={18} width="40%" className="mb-4" />
      <SkeletonText lines={lines} />
    </div>
  );
}

export function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-md border border-border-subtle bg-bg-panel p-6">
        <Skeleton height={24} width="50%" className="mb-3" />
        <div className="flex gap-2 mb-5">
          <Skeleton height={20} width={72} />
          <Skeleton height={20} width={72} />
          <Skeleton height={20} width={72} />
        </div>
        <SkeletonText lines={4} />
      </div>
      <CardSkeleton lines={2} />
    </div>
  );
}
