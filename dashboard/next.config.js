/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    CONTEXTBUDGET_API_URL: process.env.CONTEXTBUDGET_API_URL || "http://localhost:7842",
  },
};

module.exports = nextConfig;
