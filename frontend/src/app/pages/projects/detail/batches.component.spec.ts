// @vitest-environment jsdom
import '@angular/compiler';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { TestBed, ComponentFixture } from '@angular/core/testing';
import { provideZonelessChangeDetection, ɵresolveComponentResources } from '@angular/core';
import { BrowserTestingModule, platformBrowserTesting } from '@angular/platform-browser/testing';
import { HttpClient } from '@angular/common/http';
import { of, Subject } from 'rxjs';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { BatchesComponent } from './batches.component';

const resourceDir = resolve(process.cwd(), 'src/app/pages/projects/detail');
const template = readFileSync(resolve(resourceDir, 'batches.component.html'), 'utf8');
const preview = {
  preview_sha256: 'a'.repeat(64),
  question_count: 1,
  result_count: 1,
  indexes_to_build: 0,
  differences: [],
  variants: [],
};
const batch = {
  id: 'batch-1',
  name: 'Контроль',
  status: 'completed',
  repeats: 1,
  question_set_version_id: 'v1',
  rerun_of_id: null,
  counts: { pending: 0, running: 0, success: 1, error: 0 },
  variants: [],
};

describe('Analysis collection interface', () => {
  let fixture: ComponentFixture<BatchesComponent>;
  let component: BatchesComponent;
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
        of(
          url.includes('question-sets')
            ? []
            : url.endsWith('/batch-1')
              ? batch
              : { total: 0, items: [] },
        ),
      ),
      post: vi.fn(() => of(batch)),
    };
    await TestBed.configureTestingModule({
      imports: [BatchesComponent],
      providers: [provideZonelessChangeDetection(), { provide: HttpClient, useValue: http }],
    })
      .overrideComponent(BatchesComponent, {
        set: { template, templateUrl: undefined, styleUrl: undefined, styleUrls: [] },
      })
      .compileComponents();
    fixture = TestBed.createComponent(BatchesComponent);
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

  it('clearly states that metric calculation is unavailable', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Анализ метрик');
    expect(text).toContain('Расчёт метрик пока не подключён.');
    expect(text).toContain('Сначала создайте набор');
  });

  it('renders the answer beside exact context and the pinned reference', () => {
    component.selected.set(batch);
    component.results.set([
      {
        id: 'r1',
        variant_id: 'v',
        question_example_id: 'q1',
        repeat_number: 1,
        status: 'success',
        example: {
          question: 'Контрольный вопрос',
          reference_answer: 'Контрольный эталон',
          unanswerable: false,
          evidence: [],
          category: null,
        },
        attempts: [
          {
            number: 1,
            kind: 'initial',
            status: 'success',
            execution_run_id: 'run-1',
            error_code: null,
            response_trace: {
              question: 'Контрольный вопрос',
              answer: 'Ответ модели',
              context: 'Переданный контекст',
              retrieval_status: 'success',
            },
          },
        ],
      },
    ]);
    fixture.detectChanges();
    const columns = fixture.nativeElement.querySelectorAll('.answer-card');
    expect(columns.length).toBe(3);
    expect(columns[0].textContent).toContain('Ответ модели');
    expect(columns[1].textContent).toContain('Переданный контекст');
    expect(columns[2].textContent).toContain('Контрольный эталон');
  });

  it('shows differing settings and required index builds before launch', () => {
    component.preview.set({
      ...preview,
      indexes_to_build: 1,
      differences: [{ parameter: 'top_k', values: [3, 5] }],
      variants: [
        {
          name: 'Новый',
          applied_settings: {},
          index: {
            status: 'not_created',
            requires_build: true,
            shared_with: null,
            document_count: 1,
          },
        },
      ],
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('table').textContent).toContain('Число фрагментов');
    expect(fixture.nativeElement.textContent).toContain('Нужно построить индекс');
    expect(fixture.nativeElement.textContent).toContain('Начать сбор ответов');
  });

  it('clears overrides when selecting a saved configuration and rejects stale preview responses', () => {
    component.forms[0].configuration_version_id = 'saved';
    component.chooseConfiguration(component.forms[0]);
    expect(component.forms[0].settings).toEqual({});
    const pending = new Subject<typeof preview>();
    http.post.mockReturnValue(pending);
    component.prepare();
    component.invalidatePreview();
    pending.next(preview);
    pending.complete();
    expect(component.preview()).toBeNull();
    http.post.mockClear();
    component.create();
    expect(http.post).not.toHaveBeenCalled();
  });

  it('submits checked settings and starts the existing background collector', () => {
    component.preview.set(preview);
    component.versionId = 'v1';
    component.create();
    expect(http.post.mock.calls[0][1].preview_sha256).toBe(preview.preview_sha256);
    expect(http.post.mock.calls[1][0]).toContain('/batch-1/execute');
    expect(component.selected()?.id).toBe('batch-1');
  });

  it('downloads the whole selected run through the export endpoint', () => {
    vi.useFakeTimers();
    const create = vi.fn(() => 'blob:analysis');
    const revoke = vi.fn();
    class DownloadURL extends URL {}
    Object.defineProperties(DownloadURL, {
      createObjectURL: { value: create },
      revokeObjectURL: { value: revoke },
    });
    vi.stubGlobal('URL', DownloadURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    component.selected.set(batch);
    component.resultStatus = 'error';
    http.get.mockReturnValueOnce(of(new Blob(['{"results":[]}'], { type: 'application/json' })));
    component.export('json');
    expect(http.get.mock.calls.at(-1)?.[0]).toContain('/batch-1/export?format=json');
    expect(http.get.mock.calls.at(-1)?.[0]).not.toContain('status=');
    expect(click).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(1000);
    expect(revoke).toHaveBeenCalledWith('blob:analysis');
  });

  it('checks the saved attempt format without starting generation again', () => {
    const result = {
      id: 'r2',
      variant_id: 'v',
      question_example_id: 'q2',
      repeat_number: 1,
      status: 'success',
      example: {
        question: 'Вопрос',
        reference_answer: null,
        unanswerable: false,
        evidence: [],
        category: null,
      },
      attempts: [
        {
          number: 1,
          kind: 'initial',
          status: 'success',
          execution_run_id: 'saved-run',
          error_code: null,
          response_trace: {
            question: 'Вопрос',
            answer: 'Ответ',
            context: '',
            retrieval_status: 'no_context',
          },
        },
      ],
    };
    const check = {
      id: 'check-1',
      execution_run_id: 'saved-run',
      input_sha256: 'a'.repeat(64),
      output: {
        status: 'validated',
        value: null,
        explanation: 'Качество не оценивается.',
        missing_data: [],
        evaluator: { id: 'transport_stub', version: '1.0.0', parameters: {} },
      },
    };
    component.selected.set(batch);
    component.results.set([result]);
    http.post.mockClear();
    http.post.mockReturnValueOnce(of(check));
    component.checkFormat(result);
    const [url, body] = http.post.mock.calls[0];
    expect(url).toContain('/results/r2/evaluation-checks');
    expect(body.execution_run_id).toBe('saved-run');
    expect(body.evaluator_id).toBe('transport_stub');
    expect(http.post).toHaveBeenCalledOnce();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Формат проверен');
    expect(fixture.nativeElement.textContent).toContain('Качество не оценивается.');
    expect(component.results()[0].evaluation_checks?.[0].output.value).toBeNull();
  });
});
