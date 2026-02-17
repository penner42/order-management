/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['DM Sans', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        brand: {
          50: '#fdf8f6',
          100: '#f2e8e5',
          200: '#eaddd7',
          300: '#e0cec7',
          400: '#c4a77d',
          500: '#b08968',
          600: '#8b6914',
          700: '#6f4e37',
          800: '#5c4033',
          900: '#3d2c29',
        },
        ink: { DEFAULT: '#1a1a1a', muted: '#6b6b6b' },
      },
    },
  },
  plugins: [],
}
