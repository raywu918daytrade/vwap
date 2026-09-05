import daisyui from "daisyui";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["SFMono-Regular", "Consolas", "Menlo", "monospace"],
      },
    },
  },
  plugins: [daisyui],
  daisyui: {
    themes: [
      {
        vwap: {
          primary: "#58a6ff",
          secondary: "#d2a8ff",
          accent: "#d29922",
          neutral: "#161b22",
          "base-100": "#0d1117",
          "base-200": "#161b22",
          "base-300": "#21262d",
          "base-content": "#e6edf3",
          info: "#58a6ff",
          success: "#3fb950",
          warning: "#d29922",
          error: "#f85149",
        },
      },
    ],
  },
};
