import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 32,
          height: 32,
          background: "#09090b",
          borderRadius: 8,
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "center",
          padding: "4px 3px",
          gap: 3,
        }}
      >
        {/* Bar 1 — short, muted */}
        <div
          style={{
            background: "#059669",
            width: 7,
            height: 8,
            borderRadius: "2px 2px 1px 1px",
          }}
        />
        {/* Bar 2 — medium */}
        <div
          style={{
            background: "#10b981",
            width: 7,
            height: 15,
            borderRadius: "2px 2px 1px 1px",
          }}
        />
        {/* Bar 3 — tall, bright */}
        <div
          style={{
            background: "#34d399",
            width: 7,
            height: 22,
            borderRadius: "2px 2px 1px 1px",
          }}
        />
      </div>
    ),
    { ...size }
  );
}
