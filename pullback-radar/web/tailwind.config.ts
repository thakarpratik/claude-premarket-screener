import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#0b0e14",
        panel: "#11151e",
        panel2: "#161b26",
        line: "#232a38",
        mute: "#8a94a7",
        ink: "#d8dee9",
        up: "#3fb68b",
        down: "#e5534b",
        warn: "#d4a72c",
        info: "#539bf5",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
