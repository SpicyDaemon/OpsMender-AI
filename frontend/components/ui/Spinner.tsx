export function Spinner({ size = 6 }: { size?: number }) {
  return (
    <svg
      className={`h-${size} w-${size} animate-spin text-indigo-600`}
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
