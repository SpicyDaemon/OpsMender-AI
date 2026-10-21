/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        ops: {
          bg:      '#07090f',
          surface: '#0d1221',
          card:    '#111827',
          border:  '#1a2540',
          blue:    '#3b82f6',
          indigo:  '#6366f1',
          cyan:    '#06b6d4',
          muted:   '#64748b',
          subtle:  '#94a3b8',
        },
      },
      fontFamily: {
        sans: ['"Schibsted Grotesk"', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
      },
      borderRadius: {
        panel: '28px',
      },
      boxShadow: {
        frame: '0 40px 100px -30px rgba(59,130,246,0.25), 0 30px 60px -30px rgba(0,0,0,0.8)',
        card:  '0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.04)',
      },
      keyframes: {
        marquee: { from: { transform: 'translateX(0)' }, to: { transform: 'translateX(-50%)' } },
      },
      animation: {
        marquee: 'marquee 48s linear infinite',
      },
    },
  },
  plugins: [],
};
