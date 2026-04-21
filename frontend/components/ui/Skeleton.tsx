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

export function SessionDetailSkeleton() {
  return (
    <div className="flex h-full flex-col gap-4">
      <div>
        <Skeleton height={14} width={88} className="mb-3" />
        <div className="rounded-xl border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <Skeleton height={16} width="36%" className="mb-2" />
              <div className="mb-2 flex flex-wrap gap-2">
                <Skeleton height={20} width={84} />
                <Skeleton height={20} width={72} />
                <Skeleton height={20} width={88} />
                <Skeleton height={18} width={56} />
              </div>
              <Skeleton height={12} width={132} />
            </div>
            <Skeleton height={28} width={104} className="rounded-full" />
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="flex min-h-[420px] flex-col rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
          <div className="border-b border-border-subtle px-4 py-3">
            <Skeleton height={12} width={96} />
          </div>
          <div className="flex flex-1 flex-col gap-4 px-4 py-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex gap-3">
                <Skeleton height={14} width={14} className="mt-1 rounded-full" />
                <div className="flex-1">
                  <Skeleton height={12} width={i % 2 === 0 ? "34%" : "28%"} className="mb-2" />
                  <SkeletonText lines={2} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex min-h-[420px] flex-col rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
          <div className="border-b border-border-subtle px-4 py-3">
            <Skeleton height={12} width={80} />
          </div>
          <div className="flex flex-1 flex-col gap-4 px-4 py-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className={`max-w-[85%] ${i % 2 === 0 ? "" : "self-end"}`}>
                <div className="rounded-lg border border-border-subtle bg-bg-elevated px-3 py-3">
                  <SkeletonText lines={i % 2 === 0 ? 3 : 2} />
                </div>
              </div>
            ))}
          </div>
          <div className="border-t border-border-subtle px-4 py-3">
            <Skeleton height={40} className="rounded-md" />
          </div>
        </div>
      </div>
    </div>
  );
}

function ConfigSectionSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
      <div className="border-b border-border-subtle px-6 py-4">
        <Skeleton height={18} width="24%" className="mb-2" />
        <Skeleton height={12} width="52%" />
      </div>
      <div className="space-y-4 px-6 py-5">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i}>
            <Skeleton height={12} width="18%" className="mb-2" />
            <Skeleton height={40} className="rounded-md" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function ConfigPageSkeleton() {
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <Skeleton height={30} width={120} className="mb-2" />
        <Skeleton height={14} width="72%" />
      </div>
      <ConfigSectionSkeleton rows={3} />
      <ConfigSectionSkeleton rows={3} />
      <ConfigSectionSkeleton rows={2} />
      <ConfigSectionSkeleton rows={4} />
      <ConfigSectionSkeleton rows={2} />
    </div>
  );
}
