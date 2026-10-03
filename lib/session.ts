export const COOKIE = "pm_session";
export const MAX_AGE = 60 * 60 * 24 * 30;

function env(name: string): string {
  return process.env[name]?.trim() ?? "";
}

export function authConfigError(): string | null {
  if (!env("SITE_PASSWORD")) return "SITE_PASSWORD is not set on the server.";
  if (env("AUTH_SECRET").length < 16) return "AUTH_SECRET must be at least 16 characters.";
  return null;
}

export function requestIsSecure(request: Request): boolean {
  const forwarded = request.headers.get("x-forwarded-proto");
  if (forwarded) return forwarded.split(",")[0].trim() === "https";
  return new URL(request.url).protocol === "https:";
}

export function cookieOptions(secure: boolean) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    maxAge: MAX_AGE,
  };
}

function bytesToB64url(bytes: ArrayBuffer): string {
  const bin = Array.from(new Uint8Array(bytes), (b) => String.fromCharCode(b)).join("");
  return btoa(bin).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function hmac(message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env("AUTH_SECRET")),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return bytesToB64url(sig);
}

function fixedEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export async function passwordMatches(given: string): Promise<boolean> {
  const expected = env("SITE_PASSWORD");
  if (!expected || !given) return false;
  const left = await hmac(`password:${given}`);
  const right = await hmac(`password:${expected}`);
  return fixedEqual(left, right);
}

export async function sessionCookieValue(): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE;
  const sig = await hmac(`session:${exp}|${env("SITE_PASSWORD")}`);
  return `${exp}.${sig}`;
}

export async function sessionValid(value: string | undefined): Promise<boolean> {
  if (!value || authConfigError()) return false;
  const dot = value.indexOf(".");
  if (dot <= 0) return false;
  const expText = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  if (!/^\d+$/.test(expText)) return false;
  const exp = Number(expText);
  if (exp < Math.floor(Date.now() / 1000)) return false;
  const expected = await hmac(`session:${exp}|${env("SITE_PASSWORD")}`);
  return fixedEqual(sig, expected);
}
