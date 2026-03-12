import { createHash } from "node:crypto";

// Exported auth class.
export class AuthClient {
  login(token: string): boolean {
    return token.startsWith("prod_");
  }
}

export function validate(token: string): boolean {
  return token.length > 3;
}
