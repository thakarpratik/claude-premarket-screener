import { NextResponse } from "next/server";
import { COOKIE, cookieOptions, requestIsSecure } from "@/lib/session";

export async function GET(request: Request) {
  const response = NextResponse.redirect(new URL("/login", request.url), 303);
  response.cookies.set(COOKIE, "", { ...cookieOptions(requestIsSecure(request)), maxAge: 0 });
  return response;
}
