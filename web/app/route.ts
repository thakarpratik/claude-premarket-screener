import { htmlResponse, loadDashboard } from "@/lib/dashboard";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  return htmlResponse(await loadDashboard());
}
