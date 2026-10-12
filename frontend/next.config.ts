import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pages are rendered per visitor and fall back to payload files kept outside this folder,
  // so those files must be traced into the server bundle from the repository root.
  outputFileTracingRoot: path.join(__dirname, ".."),
  outputFileTracingIncludes: {
    "/*": ["../data/mock/sample_full_frontend_data.json", "../data/evaluation/demo_recommendation.json"],
  },
};

export default nextConfig;
