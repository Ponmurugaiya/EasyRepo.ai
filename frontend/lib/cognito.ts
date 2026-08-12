// ─────────────────────────────────────────────────────────────────────────────
// AWS Cognito configuration helpers
// ─────────────────────────────────────────────────────────────────────────────

import { Amplify } from "aws-amplify";
import {
  signInWithRedirect,
  signOut,
  getCurrentUser,
  fetchAuthSession,
} from "aws-amplify/auth";

// Configure Amplify once — call this before any auth operations.
// Safe to call multiple times (Amplify deduplicates).
export function configureCognito() {
  const region    = process.env.NEXT_PUBLIC_COGNITO_REGION    ?? "";
  const userPoolId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID ?? "";
  const clientId   = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID    ?? "";
  const domain     = process.env.NEXT_PUBLIC_COGNITO_DOMAIN        ?? "";
  const redirectUri = process.env.NEXT_PUBLIC_COGNITO_REDIRECT_URI ?? "";

  if (!userPoolId || !clientId) {
    // Cognito not configured — skip (dev environments may not have it set up)
    return false;
  }

  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId,
        userPoolClientId: clientId,
        loginWith: {
          oauth: {
            domain: domain.replace(/^https?:\/\//, ""),
            scopes: ["openid", "email", "profile"],
            redirectSignIn:  [redirectUri],
            redirectSignOut: [redirectUri],
            responseType: "code",
          },
        },
      },
    },
  });

  return true;
}

/** Trigger Google sign-in via Cognito Hosted UI. */
export async function signInWithGoogle() {
  await signInWithRedirect({ provider: "Google" });
}

/** Sign the user out and redirect back to the app. */
export async function signOutCognito() {
  await signOut({ global: false });
}

/** Returns the current Cognito user, or null if not signed in. */
export async function getCognitoUser() {
  try {
    return await getCurrentUser();
  } catch {
    return null;
  }
}

/** Returns the current JWT id token string, or null. */
export async function getCognitoToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.toString() ?? null;
  } catch {
    return null;
  }
}
