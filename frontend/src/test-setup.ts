import '@testing-library/jest-dom';
import { vi, type MockedFunction } from 'vitest';
import { TestBed } from '@angular/core/testing';

// Mock для describe, it, beforeEach и других jasmine-функций
if (typeof describe === 'undefined') {
  (global as any).describe = vi.describe;
}
if (typeof it === 'undefined') {
  (global as any).it = vi.it;
}
if (typeof beforeEach === 'undefined') {
  (global as any).beforeEach = vi.beforeEach;
}
if (typeof afterEach === 'undefined') {
  (global as any).afterEach = vi.afterEach;
}

// Убедиться, что тесты выполняются в async окружении
TestBed.initTestEnvironment(
  [],
  undefined,
  { teardown: { destroyAfterEach: true } }
);