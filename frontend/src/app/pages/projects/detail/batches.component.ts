import { Component, Input, OnInit, OnDestroy, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { JsonPipe } from '@angular/common';
import { Observable, Subject, finalize, takeUntil } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { apiErrorMessage } from '../../../models/settings-validation';

interface VariantSettings {
  top_k?: number;
  max_context_chars?: number;
  score_threshold?: number | null;
  chunk_size?: number;
  chunk_overlap?: number;
  embedding_model?: string;
  embedding_dimension?: number;
  llm_model?: string;
  system_prompt?: string;
  max_attempts?: number;
}
interface VariantForm {
  name: string;
  configuration_version_id?: string;
  settings: VariantSettings;
}
interface Difference {
  parameter: string;
  values: unknown[];
}
interface IndexPlan {
  status: string;
  requires_build: boolean;
  shared_with: string | null;
  document_count: number;
}
interface Preview {
  preview_sha256: string;
  question_count: number;
  result_count: number;
  indexes_to_build: number;
  differences: Difference[];
  variants: { name: string; applied_settings: Record<string, unknown>; index: IndexPlan }[];
}
interface SavedConfiguration {
  id: string;
  name: string;
  sha256: string;
  applied_settings: Record<string, unknown>;
}
interface Batch {
  id: string;
  name: string;
  status: string;
  rerun_of_id: string | null;
  question_set_version_id: string;
  repeats: number;
  differences?: Difference[];
  question_set?: { name: string; number: number; sha256: string };
  counts: { pending: number; running: number; success: number; error: number };
  job?: { id: string; status: string; policy: unknown; error_code: string | null } | null;
  variants?: {
    id: string;
    name: string;
    configuration_version_id: string;
    index_version_id: string;
    index_status: string;
    applied_settings: Record<string, unknown>;
  }[];
}
interface Evidence {
  file_name: string;
  quote: string;
  document_version_id: string;
  start_char: number;
  end_char: number;
}
interface Trace {
  question: string;
  answer: string | null;
  context: string | null;
  retrieval_status: string;
  retrieved_chunks?: { text: string; score: number | null; metadata: { source?: string } }[];
  errors?: { stage: string; message: string; type: string }[];
}
interface Attempt {
  number: number;
  kind: string;
  execution_run_id: string;
  error_code: string | null;
  status: string;
  response_trace: Trace | null;
}
interface EvaluationCheck {
  id: string;
  input_sha256: string;
  execution_run_id: string | null;
  output: {
    status: string;
    value: number | null;
    explanation: string;
    missing_data: string[];
    evaluator: { id: string; version: string; parameters: Record<string, unknown> };
  };
}
interface Result {
  id: string;
  variant_id: string;
  question_example_id: string;
  repeat_number: number;
  status: string;
  example: {
    question: string;
    reference_answer: string | null;
    unanswerable: boolean;
    evidence: Evidence[];
    category: string | null;
  };
  jobs?: unknown[];
  metric_input?: unknown;
  evaluation_checks?: EvaluationCheck[];
  attempts: Attempt[];
}
interface Page<T> {
  total: number;
  items: T[];
}
interface SetSummary {
  id: string;
  name: string;
}
interface Version {
  id: string;
  number: number;
  review_status: string;
}

@Component({
  selector: 'app-batches',
  standalone: true,
  imports: [FormsModule, JsonPipe],
  templateUrl: './batches.component.html',
  styleUrl: './batches.component.css',
})
export class BatchesComponent implements OnInit, OnDestroy {
  @Input({ required: true }) projectId!: string;
  name = 'Сбор ответов';
  setId = '';
  versionId = '';
  split = 'tuning';
  repeats = 1;
  resultStatus = '';
  resultVariant = '';
  forms: VariantForm[] = [
    { name: 'Вариант 1', settings: { top_k: 3 } },
    { name: 'Вариант 2', settings: { top_k: 5 } },
  ];
  sets = signal<SetSummary[]>([]);
  versions = signal<Version[]>([]);
  configurations = signal<SavedConfiguration[]>([]);
  configurationsTotal = signal(0);
  preview = signal<Preview | null>(null);
  batches = signal<Batch[]>([]);
  selected = signal<Batch | null>(null);
  results = signal<Result[]>([]);
  resultsTotal = signal(0);
  batchTotal = signal(0);
  error = signal<string | null>(null);
  busy = signal(false);
  private requests = 0;
  private destroyed = new Subject<void>();
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private selectionRevision = 0;
  private readonly http = inject(HttpClient);
  get project(): string {
    return `${environment.apiBaseUrl}/projects/${this.projectId}`;
  }
  get base(): string {
    return `${this.project}/batches`;
  }
  ngOnInit(): void {
    this.load();
    this.pollTimer = setInterval(() => {
      if (
        !this.busy() &&
        (this.isActive(this.selected()?.status ?? '') ||
          this.batches().some((batch) => this.isActive(batch.status)))
      )
        this.refresh();
    }, 3000);
  }
  ngOnDestroy(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.destroyed.next();
    this.destroyed.complete();
  }
  isActive(status: string): boolean {
    return ['queued', 'running', 'cancelling'].includes(status);
  }
  request<T>(call: Observable<T>, done: (value: T) => void): void {
    this.requests++;
    this.busy.set(true);
    this.error.set(null);
    call
      .pipe(
        takeUntil(this.destroyed),
        finalize(() => {
          this.requests--;
          this.busy.set(this.requests > 0);
        }),
      )
      .subscribe({
        next: done,
        error: (err) =>
          this.error.set(
            apiErrorMessage(
              err?.error?.detail,
              'Не удалось выполнить действие. Обновите состояние запуска перед повтором.',
            ),
          ),
      });
  }
  load(): void {
    this.request(this.http.get<SetSummary[]>(`${this.project}/question-sets?limit=1000`), (sets) =>
      this.sets.set(sets),
    );
    this.loadConfigurations();
    this.refresh();
  }
  loadConfigurations(append = false): void {
    const offset = append ? this.configurations().length : 0;
    this.request(
      this.http.get<Page<SavedConfiguration>>(
        `${this.base}/configurations?offset=${offset}&limit=100`,
      ),
      (page) => {
        this.configurations.set(append ? [...this.configurations(), ...page.items] : page.items);
        this.configurationsTotal.set(page.total);
      },
    );
  }
  refresh(): void {
    this.request(this.http.get<Page<Batch>>(this.base), (page) => {
      this.batches.set(page.items);
      this.batchTotal.set(page.total);
    });
    const current = this.selected();
    if (current) this.select(current.id, true);
  }
  moreBatches(): void {
    this.request(
      this.http.get<Page<Batch>>(`${this.base}?offset=${this.batches().length}`),
      (page) => {
        this.batches.update((items) => [...items, ...page.items]);
        this.batchTotal.set(page.total);
      },
    );
  }
  invalidatePreview(): void {
    this.selectionRevision++;
    this.preview.set(null);
  }
  selectSet(): void {
    this.invalidatePreview();
    this.versionId = '';
    this.versions.set([]);
    if (!this.setId) return;
    this.request(
      this.http.get<Version[]>(`${this.project}/question-sets/${this.setId}/versions?limit=1000`),
      (versions) => {
        this.versions.set(versions);
        this.versionId = versions[0]?.id ?? '';
      },
    );
  }
  chooseConfiguration(form: VariantForm): void {
    form.settings = {};
    this.invalidatePreview();
  }
  addVariant(): void {
    this.forms.push({ name: `Вариант ${this.forms.length + 1}`, settings: {} });
    this.invalidatePreview();
  }
  removeVariant(index: number): void {
    this.forms.splice(index, 1);
    this.invalidatePreview();
  }
  payload(): object {
    return {
      name: this.name,
      question_set_version_id: this.versionId,
      split: this.split,
      repeats: this.repeats,
      variants: this.forms.map((form) => ({
        name: form.name,
        configuration_version_id: form.configuration_version_id || null,
        settings: Object.fromEntries(
          Object.entries(form.settings).filter(
            ([, value]) => value !== null && value !== undefined && value !== '',
          ),
        ),
      })),
    };
  }
  prepare(): void {
    const revision = this.selectionRevision;
    this.preview.set(null);
    this.request(this.http.post<Preview>(`${this.base}/preview`, this.payload()), (plan) => {
      if (revision === this.selectionRevision) this.preview.set(plan);
    });
  }
  create(start = true): void {
    const plan = this.preview();
    if (!plan) return;
    this.request(
      this.http.post<Batch>(this.base, { ...this.payload(), preview_sha256: plan.preview_sha256 }),
      (batch) => {
        this.selected.set(batch);
        this.results.set([]);
        this.resultStatus = '';
        this.resultVariant = '';
        this.preview.set(null);
        this.loadConfigurations();
        if (start)
          this.request(this.http.post<Batch>(`${this.base}/${batch.id}/execute`, {}), () =>
            this.refresh(),
          );
        else this.refresh();
      },
    );
  }
  select(id: string, preserve = false): void {
    if (!preserve || this.selected()?.id !== id) {
      this.resultStatus = '';
      this.resultVariant = '';
      this.results.set([]);
    }
    this.request(this.http.get<Batch>(`${this.base}/${id}`), (batch) => {
      this.selected.set(batch);
      this.loadResults(false);
    });
  }
  loadResults(append: boolean): void {
    const id = this.selected()?.id;
    if (!id) return;
    const offset = append ? this.results().length : 0;
    let params = new HttpParams().set('offset', offset);
    if (this.resultStatus) params = params.set('status', this.resultStatus);
    if (this.resultVariant) params = params.set('variant_id', this.resultVariant);
    this.request(this.http.get<Page<Result>>(`${this.base}/${id}/results`, { params }), (page) => {
      if (this.selected()?.id !== id) return;
      this.results.set(append ? [...this.results(), ...page.items] : page.items);
      this.resultsTotal.set(page.total);
    });
  }
  execute(): void {
    const batch = this.selected();
    if (batch)
      this.request(this.http.post<Batch>(`${this.base}/${batch.id}/execute`, {}), () =>
        this.refresh(),
      );
  }
  retry(result: Result): void {
    const batch = this.selected();
    if (batch)
      this.request(
        this.http.post<Batch>(`${this.base}/${batch.id}/results/${result.id}/retry`, {}),
        () => this.refresh(),
      );
  }
  cancel(): void {
    const batch = this.selected();
    if (batch)
      this.request(this.http.post<Batch>(`${this.base}/${batch.id}/cancel`, {}), () =>
        this.refresh(),
      );
  }
  rerun(): void {
    const batch = this.selected();
    if (!batch) return;
    this.request(this.http.post<Batch>(`${this.base}/${batch.id}/rerun`, {}), (next) => {
      this.selected.set(next);
      this.resultStatus = '';
      this.resultVariant = '';
      this.refresh();
    });
  }
  export(format: 'json' | 'csv'): void {
    const batch = this.selected();
    if (!batch) return;
    this.request(
      this.http.get(`${this.base}/${batch.id}/export?format=${format}`, { responseType: 'blob' }),
      (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `rag-analysis-${batch.id}.${format}`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
    );
  }
  checkFormat(result: Result): void {
    const batch = this.selected();
    if (!batch) return;
    this.request(
      this.http.post<EvaluationCheck>(
        `${this.base}/${batch.id}/results/${result.id}/evaluation-checks`,
        {
          evaluator_id: 'transport_stub',
          evaluator_version: '1.0.0',
          parameters: {},
          execution_run_id: this.latest(result)?.execution_run_id ?? null,
        },
      ),
      (check) =>
        this.results.update((rows) =>
          rows.map((row) =>
            row.id === result.id
              ? { ...row, evaluation_checks: [...(row.evaluation_checks ?? []), check] }
              : row,
          ),
        ),
    );
  }
  missingData(fields: string[]): string {
    const labels: Record<string, string> = {
      question: 'вопрос',
      answer: 'сохранённый ответ',
      context: 'фактический контекст',
      reference_answer: 'эталон',
      evidence: 'подтверждающие выдержки',
      retrieved_chunks: 'найденные фрагменты',
      'provenance.execution_run_id': 'сохранённая попытка',
      'provenance.configuration_version_id': 'версия конфигурации',
      'provenance.index_version_id': 'версия индекса',
      'provenance.source_trace_sha256': 'полная трасса ответа',
      'provenance.question_isolated': 'подтверждение независимого выполнения вопроса',
      'provenance.source_trace_schema_version': 'поддерживаемая версия трассы',
    };
    return fields.map((field) => labels[field] ?? field).join(', ');
  }
  checkStatus(status: string): string {
    return (
      (
        {
          validated: 'Формат проверен',
          skipped: 'Проверка пропущена',
          error: 'Ошибка проверки',
        } as Record<string, string>
      )[status] ?? status
    );
  }
  latest(result: Result): Attempt | undefined {
    return result.attempts.at(-1);
  }
  variantName(id: string): string {
    return this.selected()?.variants?.find((item) => item.id === id)?.name ?? id;
  }
  total(batch: Batch): number {
    return Object.values(batch.counts).reduce((sum, value) => sum + value, 0);
  }
  done(batch: Batch): number {
    return batch.counts.success + batch.counts.error;
  }
  parameter(key: string): string {
    return (
      (
        {
          top_k: 'Число фрагментов',
          max_context_chars: 'Лимит контекста, символов',
          score_threshold: 'Порог поиска',
          chunk_size: 'Размер фрагмента',
          chunk_overlap: 'Перекрытие',
          embedding_model: 'Модель эмбеддингов',
          embedding_dimension: 'Размерность',
          embedding_api_url: 'Провайдер эмбеддингов',
          llm_model: 'Модель ответа',
          llm_api_url: 'Провайдер ответа',
          system_prompt: 'Системный промпт',
          max_attempts: 'Попыток на этап',
        } as Record<string, string>
      )[key] ?? key
    );
  }
  value(value: unknown): string {
    return value === null || value === undefined
      ? 'Не задано'
      : typeof value === 'object'
        ? JSON.stringify(value)
        : String(value);
  }
  errorLabel(code: string | null | undefined): string {
    return (
      (
        {
          retrieval_error: 'Ошибка поиска по документам',
          generation_error: 'Ошибка генерации ответа',
          execution_timeout: 'Превышено время выполнения',
          execution_interrupted: 'Выполнение прервано',
          question_failed: 'Фоновая задача завершилась ошибкой',
        } as Record<string, string>
      )[code ?? ''] ?? 'Ответ пока не получен'
    );
  }
  status(value: string): string {
    return (
      (
        {
          queued: 'В очереди',
          cancelling: 'Отменяется',
          cancelled: 'Отменён',
          pending: 'Ожидает выполнения',
          running: 'Выполняется',
          success: 'Ответ сохранён',
          error: 'Ошибка',
          completed: 'Сбор завершён',
          completed_with_errors: 'Сбор завершён с ошибками',
          interrupted: 'Прерван',
        } as Record<string, string>
      )[value] ?? value
    );
  }
}
