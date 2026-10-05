import { readFile } from "node:fs/promises";
import path from "node:path";
import { SECURITY_HEADERS } from "@/lib/store";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const HEAD = `
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Move Radar">
<meta name="theme-color" content="#0b0e14">
<script>window.REMOTE = true;</script>`;

// Serves the same page as the local app, switched to read-only mode (data comes from /api/data).
export async function GET() {
  const html = await readFile(path.join(process.cwd(), "static", "index.html"), "utf8");
  return new Response(html.replace("</head>", `${HEAD}</head>`), {
    headers: { "Content-Type": "text/html; charset=utf-8", ...SECURITY_HEADERS },
  });
}
