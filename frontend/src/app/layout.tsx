// Root layout for every page: the HTML shell, global styles, page metadata and the theme set before first paint.
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Procurement Intelligence",
  description: "Evidence-backed procurement decision support over World Bank open data and project documents.",
};

// Applies the saved (or system) theme before first paint, so dark mode never flashes light.
const themeScript = `try{var t=localStorage.getItem("theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

/** Wraps every page. suppressHydrationWarning is needed because the theme script changes <html> before React loads. */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
