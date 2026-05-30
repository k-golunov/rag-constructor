import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    css: true,
    reporters: ['default'],
    coverage: {
      reportsDirectory: '../coverage/frontend',
      provider: 'v8',
    },
  },
});