"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ReactFlowProvider } from "reactflow";
import { useState, useEffect } from "react";
import { useAuthStore } from "@/store/auth-store";

/** Initialise Amplify and pick up any pending Cognito OAuth redirect. */
function CognitoInit() {
  const { setCognitoUser, isLoggedIn, loginMode } = useAuthStore();

  useEffect(() => {
    async function init() {
      try {
        const { configureCognito, getCognitoUser, getCognitoToken } = await import(
          "@/lib/cognito"
        );
        const configured = configureCognito();
        if (!configured) return;

        // If a Cognito user is already signed in (e.g. page refresh after OAuth),
        // update the store so the UI reflects the logged-in state.
        if (!isLoggedIn || loginMode !== "cognito") {
          const user = await getCognitoUser();
          if (user) {
            const token = await getCognitoToken();
            if (token) {
              try {
                const payload = JSON.parse(atob(token.split(".")[1]));
                setCognitoUser({
                  username: user.username,
                  email: payload.email ?? user.username,
                  name: payload.name ?? payload["cognito:username"] ?? user.username,
                  picture: payload.picture,
                });
              } catch {
                // Malformed JWT — ignore
              }
            }
          }
        }
      } catch {
        // Cognito not configured or user not signed in — no-op
      }
    }

    init();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount

  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={300}>
        <ReactFlowProvider>
          <CognitoInit />
          {children}
        </ReactFlowProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
