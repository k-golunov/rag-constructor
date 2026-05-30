import { createComponentFixture } from '@angular/core/testing';
import { App } from './app';

describe('App', () => {
  let fixture: any;
  let app: App;

  beforeEach(async () => {
    fixture = await createComponentFixture(App);
    app = fixture.componentInstance;
  });

  it('should create the app', () => {
    expect(app).toBeDefined();
  });

  it('should render title', () => {
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const titleElement = compiled.querySelector('h1');
    const textContent = titleElement?.textContent ?? '';
    expect(textContent).toContain('Hello, frontend');
  });
});