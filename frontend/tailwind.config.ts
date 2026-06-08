import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          hover: "hsl(var(--primary-hover))",
          foreground: "#fff",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "#fff",
        },
        destructive: "hsl(var(--destructive))",
        warning: "hsl(var(--warning))",
        muted: "hsl(var(--muted))",
        ring: "hsl(var(--ring))",
      },
      borderRadius: {
        lg: "var(--radius)",
      },
      keyframes: {
        "grid-drift": {
          "0%": { backgroundPosition: "0 0" },
          "100%": { backgroundPosition: "64px 64px" },
        },
      },
      animation: {
        "grid-drift": "grid-drift 20s linear infinite",
      },
    },
  },
  plugins: [],
};
export default config;
