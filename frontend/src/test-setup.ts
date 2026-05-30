// Настройка тестового окружения для Vitest
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Instead of attaching to window/global, we can use Vitest's built-in global APIs
// directly. Vitest automatically provides these in test environment.

// If you need to extend global declarations, you can do it like this:
declare global {
  // This allows us to use vitest's assertions in typescript
  const describe: typeof vi.describe;
  const it: typeof vi.it;
  const beforeEach: typeof vi.beforeEach;
  const afterEach: typeof vi.afterEach;
}

// Экспортируем глобальные переменные для тестов
global.describe = describe;
global.it = it;
global.expect = expect;
global.beforeEach = beforeEach;
global.afterEach = afterEach;