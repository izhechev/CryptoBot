"use client";
import { useState } from "react";

export function CoinIcon({ url, size = 16 }: { url?: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) {
    return (
      <span
        className="inline-block rounded-full bg-[var(--panel-2)] border border-[var(--border)] shrink-0"
        style={{ width: size, height: size }}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable CoinGecko hosts
    <img
      src={url}
      alt=""
      width={size}
      height={size}
      className="rounded-full shrink-0"
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
    />
  );
}
