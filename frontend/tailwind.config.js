/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        dark: {
          bg: '#0f1117',
          card: '#1a1d23',
          border: '#2a2d35',
          text: '#e1e4e8',
          muted: '#8b949e',
        },
        severity: {
          p0: '#ff4444',
          p1: '#ff8c00',
          p2: '#ffd700',
          p3: '#4169e1',
        },
        status: {
          triggered: '#ff4444',
          triaged: '#ff8c00',
          investigating: '#ffd700',
          mitigated: '#4169e1',
          resolved: '#22c55e',
          postmortem: '#a855f7',
        },
      },
    },
  },
  plugins: [],
}
