import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Robin",
  description: "Robin local operator dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
