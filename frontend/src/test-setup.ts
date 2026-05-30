// Импортируем необходимые модули для тестирования
import '@testing-library/jest-dom';

// Гарантируем доступность глобальных функций Vitest
if (typeof global !== 'undefined') {
  global.describe = global.describe || require('vitest').describe;
  global.it = global.it || require('vitest').it;
  global.test = global.test || require('vitest').test;
  global.expect = global.expect || require('vitest').expect;
  global.beforeEach = global.beforeEach || require('vitest').beforeEach;
  global.afterEach = global.afterEach || require('vitest').afterEach;
}