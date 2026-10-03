import { readFile } from "node:fs/promises";
import path from "node:path";
import { get } from "@vercel/blob";

const HEAD = `
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Premarket">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#f6f4ef" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#24211c" media="(prefers-color-scheme: dark)">
`;

export function decorate(html: string): string {
  let page = html;
  if (!page.includes("manifest.webmanifest")) {
    page = page.replace("</head>", `${HEAD}</head>`);
  }
  if (!page.includes("/logout")) {
    page = page.replace(
      "<button type=button class=theme id=theme>Theme</button>",
      "<button type=button class=theme id=theme>Theme</button><a class=theme href=\"/logout\">Log out</a>",
    );
  }
  return page;
}

export function htmlResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "private, no-store",
      "X-Robots-Tag": "noindex, nofollow",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "same-origin",
      "Content-Security-Policy":
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    },
  });
}

export function waitingPage(message: string): string {
  return `<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CLaude Premarket Screener</title>${HEAD}
<style>
:root{color-scheme:light dark;--bg:#f6f4ef;--ink:#2c2924;--mute:#6d675e;--card:#fffcf7;--line:#e4dfd4}
@media (prefers-color-scheme:dark){:root{--bg:#24211c;--ink:#f3efe6;--mute:#b7b0a4;--card:#2e2a24;--line:#453f36}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
main{width:min(28rem,calc(100% - 32px));background:var(--card);border:1px solid var(--line);border-radius:16px;padding:28px 24px}
p{margin:0 0 8px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
h1{margin:0 0 12px;font-size:1.35rem;letter-spacing:-0.02em}
a{color:inherit}
</style></head><body><main>
<p>Private</p>
<h1>CLaude Premarket Screener</h1>
<p style="letter-spacing:0;text-transform:none;font-size:15px;color:var(--ink)">${message}</p>
<p style="margin-top:18px"><a href="/logout">Log out</a></p>
</main></body></html>`;
}

async function localDashboard(): Promise<string | null> {
  if (process.env.VERCEL) return null;
  const file = path.join(process.cwd(), "reports", "latest.html");
  try {
    return await readFile(file, "utf8");
  } catch {
    return null;
  }
}

function blobToken(): string | undefined {
  return (
    process.env.BLOB_READ_WRITE_TOKEN ||
    process.env.BLOB_READ_WRITE_TOKEN_READ_WRITE_TOKEN ||
    undefined
  );
}

export async function loadDashboard(): Promise<string> {
  const local = await localDashboard();
  if (local) return decorate(local);
  try {
    const token = blobToken();
    const result = await get("latest.html", {
      access: "private",
      useCache: false,
      ...(token ? { token } : {}),
    });
    if (!result || result.statusCode !== 200 || !result.stream) {
      return waitingPage("The latest screen has not been published yet. It appears here after the weekday run on your PC finishes.");
    }
    return decorate(await new Response(result.stream).text());
  } catch {
    return waitingPage("The private store is not connected yet. Add the Blob token on Vercel, then run the screener once on your PC.");
  }
}
