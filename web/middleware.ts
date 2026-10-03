import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { COOKIE, sessionValid } from "@/lib/session";

const PUBLIC_PATHS = new Set([
  "/login",
  "/api/login",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "/favicon.ico",
  "/robots.txt",
]);

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const valid = await sessionValid(request.cookies.get(COOKIE)?.value);
  if (PUBLIC_PATHS.has(pathname)) {
    if (pathname === "/login" && valid) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return NextResponse.next();
  }
  if (valid) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
