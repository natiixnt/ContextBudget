/**
 * Minimal HTTP client utilities (Node.js built-ins only — no dependencies).
 */

import * as https from "https";
import * as http from "http";
import { URL } from "url";

export class HttpError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly body: string,
    message?: string
  ) {
    super(message ?? `HTTP ${statusCode}: ${body}`);
    this.name = "HttpError";
  }
}

export interface RequestOptions {
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  url: string;
  headers?: Record<string, string>;
  body?: unknown;
  timeoutMs?: number;
}

export async function request<T>(opts: RequestOptions): Promise<T> {
  const url = new URL(opts.url);
  const isHttps = url.protocol === "https:";
  const transport = isHttps ? https : http;
  const bodyStr = opts.body !== undefined ? JSON.stringify(opts.body) : undefined;

  const reqOptions: http.RequestOptions = {
    method: opts.method,
    hostname: url.hostname,
    port: url.port || (isHttps ? 443 : 80),
    path: url.pathname + url.search,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...opts.headers,
      ...(bodyStr ? { "Content-Length": Buffer.byteLength(bodyStr).toString() } : {}),
    },
    timeout: opts.timeoutMs ?? 30_000,
  };

  return new Promise((resolve, reject) => {
    const req = transport.request(reqOptions, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf-8");
        const statusCode = res.statusCode ?? 0;

        if (statusCode >= 200 && statusCode < 300) {
          try {
            resolve(JSON.parse(raw) as T);
          } catch {
            resolve(raw as unknown as T);
          }
        } else {
          reject(new HttpError(statusCode, raw));
        }
      });
    });

    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error(`Request timed out after ${opts.timeoutMs ?? 30_000}ms`));
    });

    if (bodyStr) {
      req.write(bodyStr);
    }
    req.end();
  });
}
