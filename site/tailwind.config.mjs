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
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
      },
      backgroundImage: {
        'hero-gradient': 'radial-gradient(ellipse 80% 60% at 50% -10%, rgba(59,130,246,0.18) 0%, transparent 70%), radial-gradient(ellipse 50% 40% at 80% 60%, rgba(99,102,241,0.10) 0%, transparent 60%)',
        'card-shine': 'linear-gradient(135deg, rgba(255,255,255,0.04) 0%, transparent 50%)',
      },
      boxShadow: {
        'glow-blue': '0 0 40px rgba(59,130,246,0.12)',
        'glow-sm':   '0 0 20px rgba(59,130,246,0.08)',
        'card':      '0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.04)',
      },
      animation: {
        'fade-up': 'fadeUp 0.6s ease forwards',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};
