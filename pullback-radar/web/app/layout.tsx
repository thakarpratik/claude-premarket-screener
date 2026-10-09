import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pullback Radar",
  description: "Market Pullback Radar — research and decision support for U.S. stock pullback setups.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
