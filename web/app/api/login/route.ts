import { NextResponse } from "next/server";
import {
  COOKIE,
  authConfigError,
  cookieOptions,
  passwordMatches,
  requestIsSecure,
  sessionCookieValue,
} from "@/lib/session";

export async function POST(request: Request) {
  const loginUrl = new URL("/login", request.url);
  if (authConfigError()) {
    loginUrl.searchParams.set("error", "config");
    return NextResponse.redirect(loginUrl, 303);
  }
  const form = await request.formData();
  const password = String(form.get("password") ?? "");
  if (!(await passwordMatches(password))) {
    loginUrl.searchParams.set("error", "1");
    return NextResponse.redirect(loginUrl, 303);
  }
  const response = NextResponse.redirect(new URL("/", request.url), 303);
  response.cookies.set(COOKIE, await sessionCookieValue(), cookieOptions(requestIsSecure(request)));
  return response;
}
