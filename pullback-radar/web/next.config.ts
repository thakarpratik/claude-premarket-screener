import type { NextConfig } from "next";

// The browser talks only to this Next.js server; /api/* is proxied to the FastAPI backend so the session
// cookie stays first-party and no provider credentials ever reach client code.
const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
