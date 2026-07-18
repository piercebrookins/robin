import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Robin Presentation",
  description: "Robin live presentation surface",
};

export default function PresentationLayout({ children }: { children: React.ReactNode }) {
  return children;
}
