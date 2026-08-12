import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    // Pin the workspace root to this app. Without it Turbopack walks upward,
    // finds a stray package-lock.json in the home directory, and infers a
    // root outside the KAVACH repo entirely.
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
