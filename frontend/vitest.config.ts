import { defineConfig } from 'vitest/config';
import { angularVitestPlugins } from '@analogjs/vite-plugin-angular';

export default defineConfig({
  plugins: [...angularVitestPlugins()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts'],
    css: true,
    reporters: ['default'],
    coverage: {
      reportsDirectory: '../coverage/frontend',
      provider: 'v8',
    },
  },
  define: {
    'process.env': {},
  },
});