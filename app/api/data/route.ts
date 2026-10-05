import { readBundle, SECURITY_HEADERS } from "@/lib/store";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  let body: string | null = null;
  try {
    body = await readBundle();
  } catch {
    body = null;
  }
  if (!body) {
    return Response.json({ error: "No data published yet. Start Move Radar on your PC (run.bat)." },
      { status: 503, headers: SECURITY_HEADERS });
  }
  return new Response(body, { headers: { "Content-Type": "application/json", ...SECURITY_HEADERS } });
}
