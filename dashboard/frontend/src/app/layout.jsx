import "./global.css";
import Providers from "../components/Providers";

// Load Inter font via Google Fonts
const interFontStyle = `
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; font-family: 'Inter', -apple-system, sans-serif; }
  a { text-decoration: none; color: inherit; }
`;
// background/color used to be hardcoded (#F9FAFB / #111827) right here. This
// tag is unlayered CSS injected straight into <head>, and unlayered rules
// always win over @layer base regardless of where they sit in the cascade --
// so those two properties silently shadowed global.css's themed `body` rule
// (background-color: var(--background) + the grid/glow canvas texture) on
// every page. Dropped them so the themed rule is reachable; margin/padding/
// font-family stay here since nothing in global.css claims those.

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        {/* No page previously set a title at all, so every tab/history entry
            read blank. This is the app-wide fallback; pages that identify a
            specific record (e.g. a SOW document) override it via
            document.title in their own effect and restore this on unmount. */}
        <title>AEP — QA Platform</title>
        <style dangerouslySetInnerHTML={{ __html: interFontStyle }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
