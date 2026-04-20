const SIZE_MAP: Record<number, string> = {
  4: "h-4 w-4",
  5: "h-5 w-5",
  6: "h-6 w-6",
  7: "h-7 w-7",
  8: "h-8 w-8",
  10: "h-10 w-10",
};

export function Spinner({ size = 6 }: { size?: number }) {
  const cls = SIZE_MAP[size] ?? "h-6 w-6";
  return (
    <svg
      className={`${cls} animate-spin text-accent`}
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v8H4z"
      />
    </svg>
  );
}

export function PageSpinner() {
  return (
    <div className="flex h-full min-h-[300px] items-center justify-center">
      <Spinner size={8} />
    </div>
  );
}
