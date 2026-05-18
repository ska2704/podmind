/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Semantic palette — kept narrow on purpose. The institutional
      // look is "absence of color = healthy"; only anomalies wear red.
      colors: {
        // Soft page background. NOT pure white — pure white is too cold
        // under camera and the rounded card edges disappear.
        'page': '#FAFAFA',
        'card': '#FFFFFF',
        'border-subtle': '#EFEFEF',
        // Anomaly accent. Same red used for warn and critical; the
        // distinction is whether we layer a glow under it (critical).
        'red-anomaly': '#E5484D',
        // Neutral text scale (greys we actually use). Avoid relying on
        // Tailwind's default `gray-*` so the palette is opinionated.
        'ink-900': '#111111',
        'ink-700': '#3A3A3A',
        'ink-500': '#7A7A7A',
        'ink-400': '#A3A3A3',
        'ink-300': '#D4D4D4',
      },
      fontFamily: {
        // UI text. Loaded from Google Fonts in index.html.
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        // All numbers/timestamps/metrics. Monospaced is the
        // institutional tell — line up at the digit, not the glyph.
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        // Custom-tuned card shadow. Tailwind's `shadow` is slightly
        // too soft; `shadow-md` is too pronounced under camera.
        card: '0 1px 2px 0 rgba(17, 17, 17, 0.04), 0 1px 1px 0 rgba(17, 17, 17, 0.03)',
        // Anomaly glow. Used under critical pods on the dependency
        // graph and on critical badges.
        'glow-red': '0 0 0 4px rgba(229, 72, 77, 0.18)',
      },
    },
  },
  plugins: [],
};
