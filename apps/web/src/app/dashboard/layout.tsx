"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Menu } from "lucide-react";
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
  const [sidebarOpen, setSidebarOpen] = useState(false);

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
      <div className="flex h-dvh bg-background">
        <AppSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        {!sidebarOpen && (
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="fixed left-3 top-3 z-40 rounded-md border bg-background p-2 shadow-sm md:hidden"
            aria-label="Abrir menú"
          >
            <Menu className="h-5 w-5" />
          </button>
        )}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Header />
          <main className="flex-1 overflow-x-hidden overflow-y-auto p-4 md:p-6">{children}</main>
        </div>
        <ChatWidget />
        <Toaster />
      </div>
    </QueryClientProvider>
  );
}
