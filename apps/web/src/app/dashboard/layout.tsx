"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AppSidebar } from "@/components/app-sidebar";
import { Header } from "@/components/header";
import { ChatWidget } from "@/components/chat-widget";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { Toaster } from "sonner";

const queryClient = new QueryClient();

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { isAuthenticated, accessToken, hasHydrated } = useAuth();

  // Solo redirige cuando Zustand ya restauró la sesión desde localStorage.
  useEffect(() => {
    if (hasHydrated && (!isAuthenticated || !accessToken)) {
      router.push("/login");
    }
  }, [hasHydrated, isAuthenticated, accessToken, router]);

  if (!hasHydrated) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated || !accessToken) {
    return null;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <div className="flex h-screen bg-background">
        <AppSidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Header />
          <main className="flex-1 overflow-y-auto p-6">{children}</main>
        </div>
        <ChatWidget />
        <Toaster />
      </div>
    </QueryClientProvider>
  );
}
