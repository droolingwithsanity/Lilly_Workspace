#!/bin/bash
set -e
MISSION_DIR="/home/labhrasd/Lilly_Workspace/mission-control"

# Login route — force dynamic so env vars are read at request time
cat > "/src/app/api/auth/login/route.ts" << LOGIN_EOF
import { NextRequest, NextResponse } from "next/server";
import { getAuth0LoginUrl } from "@/lib/auth0";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const state = Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
  const response = NextResponse.redirect(getAuth0LoginUrl(state));
  response.cookies.set("auth0_state", state, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    maxAge: 600,
    path: "/",
  });
  return response;
}
LOGIN_EOF

# auth0.ts with whitelist
cat > "/src/lib/auth0.ts" << AUTH0_EOF
const AUTH0_DOMAIN = process.env.AUTH0_DOMAIN;
const AUTH0_CLIENT_ID = process.env.AUTH0_CLIENT_ID;
const AUTH0_SECRET = process.env.AUTH0_SECRET;
const APP_BASE_URL = process.env.APP_BASE_URL || "http://localhost:4000";

function collectAllowedEmails(): Set<string> {
  const rawValues = [process.env.AUTH0_ALLOWED_EMAILS, process.env.OWNER_EMAIL];
  const emails: string[] = [];
  for (const raw of rawValues) {
    if (!raw) continue;
    for (const part of raw.split(",")) {
      const trimmed = part.trim().toLowerCase();
      if (trimmed) emails.push(trimmed);
    }
  }
  return new Set(emails);
}

const ALLOWED_EMAILS = collectAllowedEmails();
const AUTH_ENABLED = !!(AUTH0_DOMAIN && AUTH0_CLIENT_ID && AUTH0_SECRET);
if (!AUTH_ENABLED) {
  console.warn("[Auth0] Missing env vars — authentication disabled (local dev mode)");
}

export function isAllowedUser(email?: string | null): boolean {
  if (!email) return false;
  if (ALLOWED_EMAILS.size === 0) return true;
  return ALLOWED_EMAILS.has(email.toLowerCase());
}

export function getAuth0LoginUrl(state: string): string {
  const params = new URLSearchParams({
    client_id: AUTH0_CLIENT_ID || "",
    redirect_uri: APP_BASE_URL + "/api/auth/callback",
    response_type: "code",
    scope: "openid profile email",
    state,
    connection: "google-oauth2",
  });
  return "https://" + AUTH0_DOMAIN + "/authorize?" + params.toString();
}

export function getAuth0LogoutUrl(): string {
  return "https://" + AUTH0_DOMAIN + "/v2/logout?returnTo=" + encodeURIComponent(APP_BASE_URL);
}

export async function exchangeCodeForToken(code: string): Promise<any> {
  const res = await fetch("https://" + AUTH0_DOMAIN + "/oauth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "authorization_code",
      client_id: AUTH0_CLIENT_ID,
      client_secret: process.env.AUTH0_CLIENT_SECRET,
      code,
      redirect_uri: APP_BASE_URL + "/api/auth/callback",
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error("Auth0 token exchange failed: " + res.status + " " + text);
  }
  return res.json();
}

export async function getUserInfo(accessToken: string): Promise<any> {
  const res = await fetch("https://" + AUTH0_DOMAIN + "/userinfo", {
    headers: { Authorization: "Bearer " + accessToken },
  });
  if (!res.ok) throw new Error("Auth0 userinfo failed: " + res.status);
  return res.json();
}

export async function createSessionToken(user: { sub: string; email?: string; name?: string; picture?: string }): Promise<string> {
  const payload = JSON.stringify({ sub: user.sub, email: user.email, name: user.name, picture: user.picture, iat: Date.now() });
  const encoder = new TextEncoder();
  const keyData = encoder.encode(AUTH0_SECRET || "");
  const cryptoKey = await crypto.subtle.importKey("raw", keyData, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(payload));
  const signatureB64 = bufferToBase64Url(signature);
  return payload + "." + signatureB64;
}

export async function verifySessionToken(token: string): Promise<any | null> {
  try {
    const [payloadB64, signatureB64] = token.split(".");
    if (!payloadB64 || !signatureB64) return null;
    const encoder = new TextEncoder();
    const keyData = encoder.encode(AUTH0_SECRET || "");
    const cryptoKey = await crypto.subtle.importKey("raw", keyData, { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    const signature = base64UrlToBuffer(signatureB64);
    const payload = encoder.encode(payloadB64);
    const valid = await crypto.subtle.verify("HMAC", cryptoKey, signature, payload);
    if (!valid) return null;
    const user = JSON.parse(new TextDecoder().decode(payload));
    if (Date.now() - user.iat > 7 * 24 * 60 * 60 * 1000) return null;
    return user;
  } catch {
    return null;
  }
}

function bufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBuffer(base64Url: string): ArrayBuffer {
  const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(base64 + padding);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < bytes.byteLength; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export { AUTH_ENABLED };
AUTH0_EOF

# Callback route with whitelist gate
cat > "/src/app/api/auth/callback/route.ts" << CALLBACK_EOF
import { NextRequest, NextResponse } from "next/server";
import { exchangeCodeForToken, getUserInfo, createSessionToken, isAllowedUser } from "@/lib/auth0";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const error = request.nextUrl.searchParams.get("error");
  const errorDescription = request.nextUrl.searchParams.get("error_description");

  if (error) {
    return NextResponse.redirect(new URL("/login?error=" + encodeURIComponent(error) + "&desc=" + encodeURIComponent(errorDescription || ""), request.url));
  }

  if (!code || !state) {
    return NextResponse.redirect(new URL("/login?error=missing_code", request.url));
  }

  const savedState = request.cookies.get("auth0_state")?.value;
  if (!savedState || state !== savedState) {
    return NextResponse.redirect(new URL("/login?error=invalid_state", request.url));
  }

  try {
    const tokens = await exchangeCodeForToken(code);
    const userInfo = await getUserInfo(tokens.access_token);
    const email = (userInfo.email || "").toLowerCase();

    if (!isAllowedUser(email)) {
      return NextResponse.redirect(new URL("/login?error=not_allowed", request.url));
    }

    const sessionToken = await createSessionToken({
      sub: userInfo.sub,
      email: userInfo.email,
      name: userInfo.name,
      picture: userInfo.picture,
    });

    const response = NextResponse.redirect(new URL("/", request.url));
    response.cookies.set("auth0_session", sessionToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      maxAge: 7 * 24 * 60 * 60,
      path: "/",
    });
    response.cookies.delete("auth0_state");
    return response;
  } catch (e) {
    console.error("Auth0 callback error:", e);
    return NextResponse.redirect(new URL("/login?error=auth_failed", request.url));
  }
}
CALLBACK_EOF

echo "Patches applied."
