import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // The page, script and styles are read from static/ at request time.
  outputFileTracingIncludes: {
    "/": ["./static/**"],
    "/static/[file]": ["./static/**"],
  },
};

export default nextConfig;
