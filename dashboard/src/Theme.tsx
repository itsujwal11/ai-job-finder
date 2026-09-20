import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

const KEY = "jd-theme";

/** Read the stored choice. Browser storage can throw (private mode), so it always has a default. */
function stored(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "light";
  } catch {
    return "system";
  }
}

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme);
  }
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* choice just will not persist */
  }
}

const OPTIONS: [Theme, string, string][] = [
  ["light", "☀", "Light"],
  ["dark", "☾", "Dark"],
  ["system", "◐", "Match system"],
];

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(stored);

  useEffect(() => { apply(theme); }, [theme]);

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map(([value, icon, label]) => (
        <button
          key={value}
          role="radio"
          aria-checked={theme === value}
          aria-label={label}
          title={label}
          className={theme === value ? "active" : ""}
          onClick={() => setTheme(value)}
        >
          {icon}
        </button>
      ))}
    </div>
  );
}
