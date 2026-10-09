"use client";
import { useEffect, useState } from "react";
import { api } from "./api";
import { useApp } from "@/components/Shell";

/** Load an endpoint that depends on the latest scan, reloading whenever a new scan finishes. */
export function useScanData<T = any>(path: string) {
  const { scanVersion, setLastScan } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    api<T>(path)
      .then((d: any) => {
        if (!live) return;
        setData(d);
        setError(d && d.ok === false ? (d.messages || [d.error]).join(" ") : null);
        if (d?.generated_at) setLastScan(d.generated_at);
      })
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [path, scanVersion, setLastScan]);
  return { data, error, loading };
}
