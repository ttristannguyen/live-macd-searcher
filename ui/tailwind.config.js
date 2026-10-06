/** @type {import('tailwindcss').Config} */

// Every colour is a CSS variable (see src/index.css), so light and dark are one palette
// defined in one place rather than `dark:` variants scattered through the components.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: token('paper'),
        card: token('card'),
        ink: token('ink'),
        muted: token('muted'),
        line: token('line'),
        bull: token('bull'),
        bear: token('bear'),
        accent: token('accent'),
      },
      fontFamily: {
        serif: ['"Iowan Old Style"', '"Palatino Linotype"', 'Palatino', 'Georgia', 'serif'],
        mono: ['ui-monospace', '"Cascadia Mono"', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
