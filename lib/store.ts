import { get } from "@vercel/blob";

export const BUNDLE_PATH = "moveradar/data.json";

function readEnv(name: string): string | undefined {
  const value = process.env[name]?.trim().replace(/^["']|["']$/g, "");
  return value || undefined;
}

// The Blob integration may expose the token under a prefixed name; accept any *READ_WRITE_TOKEN.
function blobToken(): string | undefined {
  const names = Object.keys(process.env).filter((key) => key.endsWith("READ_WRITE_TOKEN"));
  for (const name of ["BLOB_READ_WRITE_TOKEN", ...names]) {
    const token = readEnv(name);
    if (token?.startsWith("vercel_blob_rw_")) return token;
  }
  return undefined;
}

/** The latest bundle the PC uploaded (snapshot, track record, stock details), or null. */
export async function readBundle(): Promise<string | null> {
  const token = blobToken();
  const result = await get(BUNDLE_PATH, { access: "private", useCache: false, ...(token ? { token } : {}) });
  if (!result || result.statusCode !== 200 || !result.stream) return null;
  return await new Response(result.stream).text();
}

export const SECURITY_HEADERS = {
  "Cache-Control": "private, no-store",
  "X-Robots-Tag": "noindex, nofollow",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Content-Security-Policy":
    "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; " +
    "script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
};
