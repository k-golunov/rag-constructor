import { ComponentFixture, TestBed } from '@angular/core/testing';
import { App } from './app';

describe('App', () => {
  let fixture: ComponentFixture<App>;
  let app: App;

  beforeEach(async () => {
    // Используем более раннюю инициализацию тестовой среды
    await TestBed.configureTestingModule({
      imports: [App],
    })
    .overrideTemplate(App, '<router-outlet />')
    .compileComponents();

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