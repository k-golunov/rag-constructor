import { defineConfig } from 'vitest/config';
import { nxViteTsPaths } from '@nx/vite/plugins/nx-tsconfig-paths.plugin';

export default defineConfig({
  plugins: [nxViteTsPaths()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts'],
    css: true,
    reporters: ['default'],
    coverage: {
      reportsDirectory: '../../coverage/frontend',
      provider: 'v8',
    },
  },
});