import { readFile } from "node:fs/promises";
import path from "node:path";
import { SECURITY_HEADERS } from "@/lib/store";

export const runtime = "nodejs";

const TYPES: Record<string, string> = {
  "app.js": "text/javascript; charset=utf-8",
  "styles.css": "text/css; charset=utf-8",
};

export async function GET(_request: Request, { params }: { params: Promise<{ file: string }> }) {
  const { file } = await params;
  const type = TYPES[file];
  if (!type) return new Response("Not found", { status: 404 });
  const body = await readFile(path.join(process.cwd(), "static", file), "utf8");
  return new Response(body, { headers: { "Content-Type": type, ...SECURITY_HEADERS } });
}
