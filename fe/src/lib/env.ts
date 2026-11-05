// Env Vite (di-inline saat build). Nilai default untuk dev lokal.
export const AI_URL =
  import.meta.env.VITE_AI_URL || "http://localhost:3003/api/v1";
export const BE_URL =
  import.meta.env.VITE_BE_URL || "http://localhost:3001";
