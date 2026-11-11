// @vitest-environment jsdom
import '@angular/compiler';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { TestBed, ComponentFixture } from '@angular/core/testing';
import { provideZonelessChangeDetection, ɵresolveComponentResources } from '@angular/core';
import { BrowserTestingModule, platformBrowserTesting } from '@angular/platform-browser/testing';
import { HttpClient } from '@angular/common/http';
import { of, Subject, throwError } from 'rxjs';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelCardComponent, ModelCard } from './model-card.component';

const resourceDir = resolve(process.cwd(), 'src/app/pages/projects/detail');
const template = readFileSync(resolve(resourceDir, 'model-card.component.html'), 'utf8');
function card(extra: Partial<ModelCard> = {}): ModelCard {
  return {
    id: 'card-1',
    number: 1,
    created_at: '2026-11-11T10:00:00Z',
    source_sha256: 'a'.repeat(64),
    content_sha256: 'b'.repeat(64),
    context: {
      intended_use: 'Поиск по инструкциям',
      users: '',
      domain: '',
      owner: '',
      model_developer: '',
      model_revision: '',
      license: '',
      training_data: '',
      evaluation_data: '',
      factors: '',
      ethical_considerations: '',
      recommendations: '',
    },
    measurements: [],
    is_stale: false,
    changed_fields: [],
    source_snapshot: {
      project_name: 'Ассистент',
      documents: [],
      configuration: {
        llm_model: 'my-llm',
        embedding_model: 'my-embedder',
        top_k: 5,
        max_context_chars: 2000,
      },
    },
    sections: Array.from({ length: 9 }, (_, i) => ({
      key: `section-${i}`,
      title: `Раздел ${i + 1}`,
      english_title: 'Section',
      paragraphs: ['Описание проекта'],
    })),
    ...extra,
  };
}
const measurement = {
  name: 'Корректность',
  value: 0,
  unit: '%',
  sample_size: 40,
  dataset: 'Набор v1',
  method: 'Сверка с эталоном',
  measured_at: '2026-11-11',
  reviewer: 'Автор проверки',
};

describe('Project model card', () => {
  let fixture: ComponentFixture<ModelCardComponent>;
  let component: ModelCardComponent;
  let http: { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };
  beforeAll(async () => {
    TestBed.initTestEnvironment(BrowserTestingModule, platformBrowserTesting());
    await ɵresolveComponentResources((url) =>
      Promise.resolve(readFileSync(resolve(resourceDir, url), 'utf8')),
    );
  });
  beforeEach(async () => {
    http = {
      get: vi.fn((url: string) =>
        of(url.endsWith('/card-1') ? card() : { total: 1, items: [card()] }),
      ),
      post: vi.fn(() => of(card({ id: null, number: null }))),
    };
    await TestBed.configureTestingModule({
      imports: [ModelCardComponent],
      providers: [provideZonelessChangeDetection(), { provide: HttpClient, useValue: http }],
    })
      .overrideComponent(ModelCardComponent, {
        set: { template, templateUrl: undefined, styleUrl: undefined, styleUrls: [] },
      })
      .compileComponents();
    fixture = TestBed.createComponent(ModelCardComponent);
    fixture.componentRef.setInput('projectId', 'project-1');
    component = fixture.componentInstance;
    fixture.detectChanges();
  });
  afterEach(() => {
    fixture?.destroy();
    TestBed.resetTestingModule();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('opens the latest version, renders nine sections and explains missing measurements', () => {
    expect(component.selected()?.id).toBe('card-1');
    expect(fixture.nativeElement.querySelectorAll('.card-section')).toHaveLength(9);
    expect(fixture.nativeElement.textContent).toContain('Качество не оценивалось');
    expect(fixture.nativeElement.textContent).toContain('my-llm');
    expect(http.post).not.toHaveBeenCalled();
  });
  it('prepares a new project without requiring manual fields', () => {
    component.selected.set(null);
    component.edit();
    expect(http.post.mock.calls[0][0]).toContain('/model-cards/preview');
    expect(http.post.mock.calls[0][1].context.intended_use).toBe('');
    expect(component.draft()?.sections).toHaveLength(9);
    expect(component.dirty()).toBe(false);
  });
  it('requires fresh preview after edits and discards late preview responses', () => {
    component.edit();
    component.context.owner = 'Другой автор';
    component.changed();
    http.post.mockClear();
    component.save();
    expect(http.post).not.toHaveBeenCalled();
    const pending = new Subject<ModelCard>();
    http.post.mockReturnValueOnce(pending);
    component.prepare();
    component.context.domain = 'Новая область';
    component.changed();
    pending.next(card({ context: { ...card().context, domain: 'Старое значение' } }));
    pending.complete();
    expect(component.dirty()).toBe(true);
    expect(component.draft()?.context.domain).not.toBe('Старое значение');
  });
  it('does not carry measurements from an outdated configuration', () => {
    component.selected.set(
      card({ is_stale: true, changed_fields: ['documents'], measurements: [measurement] }),
    );
    component.edit();
    expect(http.post.mock.calls[0][1].measurements).toEqual([]);
    expect(component.notice()).toContain('прежние измерения');
  });
  it('also detects project changes that occurred after the card was opened', () => {
    component.selected.set(card({ measurements: [measurement] }));
    http.post.mockReturnValueOnce(of(card({ source_sha256: 'c'.repeat(64) })));
    component.edit();
    expect(component.measurements).toEqual([]);
    expect(http.post).toHaveBeenCalledTimes(2);
    expect(component.notice()).toContain('Конфигурация изменилась');
  });
  it('accepts a genuine zero and blocks incomplete measurements', () => {
    component.edit();
    component.addMeasurement();
    http.post.mockClear();
    component.prepare();
    expect(http.post).not.toHaveBeenCalled();
    component.measurements = [{ ...measurement }];
    expect(component.validMeasurements()).toBe(true);
    component.prepare();
    expect(http.post).toHaveBeenCalledOnce();
  });
  it('saves a frozen preview using the latest version as a concurrency token', () => {
    component.edit();
    http.post.mockClear();
    http.post.mockReturnValueOnce(of(card({ id: 'card-2', number: 2 })));
    component.save();
    expect(http.post.mock.calls[0][1].base_version_id).toBe('card-1');
    expect(http.post.mock.calls[0][1].source_sha256).toBe('a'.repeat(64));
    expect(component.selected()?.number).toBe(2);
    expect(component.editing()).toBe(false);
  });
  it('keeps the editor after conflict and blocks another save until preview', () => {
    component.edit();
    http.post.mockReturnValueOnce(
      throwError(() => ({ status: 409, error: { detail: 'Настройки изменились' } })),
    );
    component.save();
    expect(component.editing()).toBe(true);
    expect(component.dirty()).toBe(true);
    expect(component.error()).toContain('Настройки изменились');
  });
  it('renders user text as text and shows changed project fields', () => {
    component.selected.set(
      card({
        is_stale: true,
        changed_fields: ['system_prompt_sha256'],
        sections: [
          {
            key: 'details',
            title: 'Описание',
            english_title: 'Details',
            paragraphs: ['<img src=x onerror=alert(1)>'],
          },
        ],
      }),
    );
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('article img')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(fixture.nativeElement.textContent).toContain('системный промпт');
  });
  it('downloads the selected immutable version and releases the blob URL', () => {
    vi.useFakeTimers();
    const create = vi.fn(() => 'blob:model-card');
    const revoke = vi.fn();
    class DownloadURL extends URL {}
    Object.defineProperties(DownloadURL, {
      createObjectURL: { value: create },
      revokeObjectURL: { value: revoke },
    });
    vi.stubGlobal('URL', DownloadURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    http.get.mockReturnValueOnce(of(new Blob(['docx'])));
    component.export('docx');
    expect(http.get.mock.calls.at(-1)?.[0]).toContain('/card-1/export?format=docx');
    expect(click).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(1000);
    expect(revoke).toHaveBeenCalledWith('blob:model-card');
  });
});
