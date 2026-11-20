"use client";

import { useEffect } from "react";

// Last-resort boundary: catches an error in the ROOT layout itself, where no other error.tsx applies.
// It replaces the whole document, so it renders its own <html>/<body> and uses inline styles only —
// the app's stylesheet may be exactly what failed to load.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("Global error boundary:", error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
          background: "#f8fafc",
          color: "#0f172a",
          padding: "24px",
        }}
      >
        <div style={{ maxWidth: "28rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "1.125rem", fontWeight: 600, margin: "0 0 0.5rem" }}>Something went wrong</h1>
          <p style={{ fontSize: "0.875rem", color: "#64748b", margin: "0 0 1.25rem" }}>
            The app hit an unexpected error. Your saved work is not affected.
          </p>
          <button
            onClick={() => reset()}
            style={{
              border: "none",
              borderRadius: "0.5rem",
              background: "#4f46e5",
              color: "#fff",
              padding: "0.5rem 1rem",
              fontSize: "0.875rem",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
          {error.digest && (
            <p style={{ fontSize: "0.75rem", color: "#94a3b8", marginTop: "1rem" }}>
              Reference: <code>{error.digest}</code>
            </p>
          )}
        </div>
      </body>
    </html>
  );
}
