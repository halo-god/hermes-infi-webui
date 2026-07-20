import { ref, watch } from "vue";
import { resetMermaidTheme } from "@/utils/markdown";

type Theme = "light" | "dark";

const THEME_KEY = "hermes.theme";

function getSystemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function loadTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return getSystemTheme();
}

const theme = ref<Theme>(loadTheme());

function applyTheme(t: Theme) {
  document.body.classList.toggle("dark", t === "dark");
  document.documentElement.dataset.theme = t;
  // Reset mermaid so it re-renders with correct theme
  resetMermaidTheme();
}

// Apply on load
applyTheme(theme.value);

// Listen for system theme changes. Save handler reference so it can be
// removed if ever needed (e.g. SSR or testing). The composable is a module
// singleton so this runs once.
const _systemThemeHandler = (e: MediaQueryListEvent) => {
  if (!localStorage.getItem(THEME_KEY)) {
    theme.value = e.matches ? "dark" : "light";
  }
};
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", _systemThemeHandler);

watch(theme, (t) => {
  applyTheme(t);
  localStorage.setItem(THEME_KEY, t);
});

export function useTheme() {
  function toggleTheme() {
    theme.value = theme.value === "dark" ? "light" : "dark";
  }

  function setTheme(t: Theme) {
    theme.value = t;
  }

  return {
    theme,
    toggleTheme,
    setTheme,
  };
}
