/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // ── Brand palette ──────────────────────────────────────────────────────
        // Change these three values to retheme the entire dashboard.
        accent:        "#d40012",   // primary red - buttons, active states, highlights
        "accent-dark": "#8c000e",   // darker red - hover states
        "accent-light":"#ff4455",   // lighter red - text on dark backgrounds

        // ── Surfaces ───────────────────────────────────────────────────────────
        "page-bg":     "#0b0016",   // main page background
        card:          "#150020",   // card / panel background

        // ── Sidebar ────────────────────────────────────────────────────────────
        sidebar:         "#0b0016",
        "sidebar-hover": "#1a0025",
        "sidebar-active":"#d40012", // keep in sync with accent

        // ── Semantic ───────────────────────────────────────────────────────────
        success: "#10b981",
        warning: "#f59e0b",
        danger:  "#ef4444",
      },
    },
  },
  plugins: [],
};
