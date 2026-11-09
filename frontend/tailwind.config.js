/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#171717", soft: "#737373" },
        line: "#E5E5E5",
        paper: { DEFAULT: "#FFFFFF", soft: "#FAFAFA" },
        gold: { DEFAULT: "#C6A04A", dark: "#A98232", light: "#F7F1E3" },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
