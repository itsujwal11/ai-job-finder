import { useCallback, useEffect, useState } from "react";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/ui${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let message = res.statusText;
    try {
      message = (await res.json()).detail ?? message;
    } catch {
      /* not JSON */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export function useData<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    api<T>(path)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload, setData };
}

export function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}
