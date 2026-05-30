const { defineConfig } = require('vitest/config');

module.exports = defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    environmentOptions: {
      jsdom: {
        resources: 'usable'
      }
    },
    setupFiles: ['./src/test-setup.ts'],
    reporters: ['default', 'junit'],
    outputFile: {
      junit: './test-results/results.xml'
    },
    coverage: {
      provider: 'istanbul',
      reporter: ['text', 'json', 'html', 'junit'],
      reportsDirectory: './coverage',
      include: ['src/**/*'],
      exclude: ['src/**/*.d.ts', 'src/**/*.test.ts']
    },
    typecheck: {
      enabled: true
    }
  }
});