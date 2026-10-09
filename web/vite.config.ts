import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // The ChatGPT sign-in returns to /auth/callback on the port the page was loaded from.
  server: { proxy: { '/api': 'http://127.0.0.1:8001', '/auth': 'http://127.0.0.1:8001' } },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
});
