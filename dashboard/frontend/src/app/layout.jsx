import "./global.css";
import Providers from "../components/Providers";

// Load Inter font via Google Fonts
const interFontStyle = `
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; font-family: 'Inter', -apple-system, sans-serif; background: #F9FAFB; color: #111827; }
  a { text-decoration: none; color: inherit; }
`;

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <style dangerouslySetInnerHTML={{ __html: interFontStyle }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
