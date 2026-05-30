import { ComponentFixture, TestBed } from '@angular/core/testing';
import { App } from './app';

// Создаем компонент с inline шаблоном для тестирования
const createComponentWithInlineTemplate = () => {
  return TestBed.configureTestingModule({
    imports: [App],
  })
  .overrideTemplate(App, '<router-outlet></router-outlet>')
  .compileComponents();
};

describe('App', () => {
  let fixture: ComponentFixture<App>;
  let app: App;

  beforeEach(async () => {
    await createComponentWithInlineTemplate();
    fixture = TestBed.createComponent(App);
    app = fixture.componentInstance;
  });

  it('should create the app', () => {
    expect(app).toBeTruthy();
  });

  it('should have a title in the component', () => {
    expect(app.title()).toBe('frontend');
  });
});