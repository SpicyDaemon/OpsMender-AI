/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      // The brand palette: deep teal-navy surfaces, cream type, mustard accent.
      colors: {
        om: {
          bg:       '#0A1418',
          brand:    '#0F2C36',
          elevated: '#142730',
          border:   '#1F3640',
          cream:    '#F5EDD8',
          muted:    '#9AAEB5',
          accent:   '#E8B547',
          strong:   '#F5C861',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
      },
      boxShadow: {
        frame: '0 30px 80px -20px rgba(0,0,0,0.65), 0 0 0 1px rgba(245,237,216,0.08)',
        lift:  '0 20px 50px -20px rgba(0,0,0,0.6), 0 0 0 1px rgba(245,237,216,0.06)',
      },
      maxWidth: {
        measure: '38rem',
      },
    },
  },
  plugins: [],
};
