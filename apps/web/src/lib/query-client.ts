import { QueryClient } from "@tanstack/react-query";

/** QueryClient único de la app (compartido por el layout y el gestor de subidas). */
export const queryClient = new QueryClient();
