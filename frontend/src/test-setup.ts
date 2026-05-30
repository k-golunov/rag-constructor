import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock для describe, it, beforeEach и других jasmine-функций
if (typeof window !== 'undefined') {
  (window as any).describe = vi.describe;
  (window as any).it = vi.it;
  (window as any).beforeEach = vi.beforeEach;
  (window as any).afterEach = vi.afterEach;
}

// Для глобального окружения
if (typeof global !== 'undefined') {
  (global as any).describe = vi.describe;
  (global as any).it = vi.it;
  (global as any).beforeEach = vi.beforeEach;
  (global as any).afterEach = vi.afterEach;
}