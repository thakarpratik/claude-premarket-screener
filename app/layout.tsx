import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Move Radar",
  robots: { index: false, follow: false },
  appleWebApp: { capable: true, title: "Move Radar", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#0b0e14",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="gate">{children}</body>
    </html>
  );
}
