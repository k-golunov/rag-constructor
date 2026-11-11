import { Component, DestroyRef, Input, OnInit, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { environment } from '../../../../environments/environment';
import { apiErrorMessage } from '../../../models/settings-validation';

export interface CardContext {
  intended_use: string;
  users: string;
  domain: string;
  owner: string;
  model_developer: string;
  model_revision: string;
  license: string;
  training_data: string;
  evaluation_data: string;
  factors: string;
  ethical_considerations: string;
  recommendations: string;
}
export interface Measurement {
  name: string;
  value: number | null;
  unit: string;
  sample_size: number | null;
  dataset: string;
  method: string;
  measured_at: string;
  reviewer: string;
}
export interface CardSummary {
  id: string;
  number: number;
  created_at: string;
  source_sha256: string;
  is_stale: boolean;
  changed_fields: string[];
}
export interface ModelCard extends Omit<CardSummary, 'id' | 'number'> {
  id: string | null;
  number: number | null;
  content_sha256: string;
  context: CardContext;
  measurements: Measurement[];
  source_snapshot: {
    project_name: string;
    configuration: {
      llm_model: string;
      embedding_model: string;
      top_k: number;
      max_context_chars: number;
    };
    documents: unknown[];
  };
  sections: { key: string; title: string; english_title: string; paragraphs: string[] }[];
}
const emptyContext = (): CardContext => ({
  intended_use: '',
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
});

@Component({
  selector: 'app-model-card',
  standalone: true,
  imports: [FormsModule, DatePipe, DecimalPipe],
  templateUrl: './model-card.component.html',
  styleUrl: './model-card.component.css',
})
export class ModelCardComponent implements OnInit {
  @Input({ required: true }) projectId!: string;
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  versions = signal<CardSummary[]>([]);
  total = signal(0);
  selected = signal<ModelCard | null>(null);
  draft = signal<ModelCard | null>(null);
  editing = signal(false);
  busy = signal(false);
  downloading = signal(false);
  error = signal('');
  notice = signal('');
  dirty = signal(false);
  context = emptyContext();
  measurements: Measurement[] = [];
  private draftSource = '';
  private editRevision = 0;
  private latestId: string | null = null;

  readonly fields: { key: keyof CardContext; label: string; hint: string; max: number }[] = [
    {
      key: 'intended_use',
      label: 'Для чего нужен ассистент',
      hint: 'Какие задачи он решает и что пользователь получает на выходе',
      max: 6000,
    },
    {
      key: 'users',
      label: 'Кто будет пользоваться',
      hint: 'Например, сотрудники поддержки или студенты',
      max: 3000,
    },
    { key: 'domain', label: 'Предметная область', hint: 'Тематика и язык документов', max: 3000 },
    { key: 'owner', label: 'Ответственный и контакт', hint: 'Кому сообщать об ошибках', max: 500 },
    {
      key: 'model_developer',
      label: 'Разработчик модели',
      hint: 'Укажите по документации модели; провайдер API может быть посредником',
      max: 500,
    },
    {
      key: 'model_revision',
      label: 'Ревизия модели',
      hint: 'Точная версия, если известна',
      max: 500,
    },
    {
      key: 'license',
      label: 'Условия использования',
      hint: 'Лицензия или ссылка на условия провайдера',
      max: 2000,
    },
    {
      key: 'training_data',
      label: 'Данные обучения модели',
      hint: 'Только подтверждённые сведения о базовой модели. Загруженные документы не являются её обучающей выборкой',
      max: 6000,
    },
    {
      key: 'evaluation_data',
      label: 'На чём проверяли качество',
      hint: 'Набор вопросов, его версия, категории, tuning/test и способ проверки эталонов',
      max: 6000,
    },
    {
      key: 'factors',
      label: 'Что ещё влияет на качество',
      hint: 'Терминология, язык, особенности документов',
      max: 6000,
    },
    {
      key: 'ethical_considerations',
      label: 'Риски применения',
      hint: 'Чувствительные данные, последствия ошибок, возможная предвзятость',
      max: 6000,
    },
    {
      key: 'recommendations',
      label: 'Ограничения и рекомендации',
      hint: 'Когда ответ нужно перепроверять и где ассистент неприменим',
      max: 6000,
    },
  ];
  readonly changeLabels: Record<string, string> = {
    project_name: 'название',
    documents: 'документы',
    llm_model: 'языковая модель',
    embedding_model: 'эмбеддер',
    embedding_dimension: 'размерность',
    system_prompt_sha256: 'системный промпт',
    top_k: 'число фрагментов',
    max_context_chars: 'лимит контекста',
    chunk_size: 'размер фрагментов',
    chunk_overlap: 'перекрытие',
    llm_endpoint_sha256: 'подключение LLM',
    embedding_endpoint_sha256: 'подключение эмбеддера',
    template_version: 'шаблон карточки',
  };

  get base(): string {
    return `${environment.apiBaseUrl}/projects/${this.projectId}/model-cards`;
  }
  get visible(): ModelCard | null {
    return this.editing() ? this.draft() : this.selected();
  }
  ngOnInit(): void {
    this.loadVersions(true);
  }

  loadVersions(openLatest = false, more = false): void {
    if (this.busy()) return;
    this.busy.set(true);
    const offset = more ? this.versions().length : 0;
    this.http
      .get<{ total: number; items: CardSummary[] }>(`${this.base}?offset=${offset}&limit=25`)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.total.set(result.total);
          this.versions.set(more ? [...this.versions(), ...result.items] : result.items);
          if (!more) this.latestId = result.items[0]?.id ?? null;
          this.busy.set(false);
          if (openLatest && this.latestId) this.open(this.latestId);
          else if (openLatest) this.edit();
        },
        error: (err) => this.fail(err),
      });
  }
  open(id: string): void {
    if (this.busy() || this.editing()) return;
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    this.http
      .get<ModelCard>(`${this.base}/${id}`)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (card) => {
          this.selected.set(card);
          this.busy.set(false);
        },
        error: (err) => this.fail(err),
      });
  }
  edit(): void {
    if (this.busy()) return;
    const card = this.selected();
    this.context = card ? { ...card.context } : emptyContext();
    this.measurements = card && !card.is_stale ? card.measurements.map((m) => ({ ...m })) : [];
    this.draftSource = card?.source_sha256 ?? '';
    this.editing.set(true);
    this.draft.set(null);
    this.dirty.set(true);
    this.notice.set(
      card?.is_stale
        ? 'Настройки изменились. Описание перенесено, прежние измерения оставлены в старой версии.'
        : '',
    );
    this.prepare();
  }
  cancel(): void {
    if (this.busy()) return;
    this.editRevision++;
    this.editing.set(false);
    this.draft.set(null);
    this.error.set('');
    this.notice.set('');
  }
  changed(): void {
    this.editRevision++;
    this.dirty.set(true);
    this.error.set('');
  }
  addMeasurement(): void {
    this.measurements.push({
      name: '',
      value: null,
      unit: '%',
      sample_size: null,
      dataset: '',
      method: '',
      measured_at: '',
      reviewer: '',
    });
    this.changed();
  }
  removeMeasurement(index: number): void {
    this.measurements.splice(index, 1);
    this.changed();
  }
  validMeasurements(): boolean {
    return this.measurements.every(
      (m) =>
        m.name.trim() &&
        m.value !== null &&
        Number.isFinite(m.value) &&
        m.unit.trim() &&
        Number.isInteger(m.sample_size) &&
        (m.sample_size ?? 0) > 0 &&
        m.dataset.trim() &&
        m.method.trim() &&
        m.measured_at &&
        m.reviewer.trim(),
    );
  }
  prepare(): void {
    if (this.busy()) return;
    if (!this.validMeasurements()) {
      this.error.set(
        'Для каждого измерения заполните значение, выборку, методику, дату и проверяющего.',
      );
      return;
    }
    this.busy.set(true);
    this.error.set('');
    const revision = this.editRevision;
    this.http
      .post<ModelCard>(`${this.base}/preview`, {
        context: this.context,
        measurements: this.measurements,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (card) => {
          this.busy.set(false);
          if (revision !== this.editRevision) return;
          if (
            this.draftSource &&
            card.source_sha256 !== this.draftSource &&
            this.measurements.length
          ) {
            this.measurements = [];
            this.draftSource = card.source_sha256;
            this.notice.set(
              'Конфигурация изменилась. Измерения убраны из нового черновика: проверьте их заново для текущего проекта.',
            );
            this.prepare();
            return;
          }
          this.draftSource = card.source_sha256;
          this.draft.set(card);
          this.dirty.set(false);
        },
        error: (err) => this.fail(err),
      });
  }
  save(): void {
    const card = this.draft();
    if (!card || this.dirty() || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.http
      .post<ModelCard>(this.base, {
        context: card.context,
        measurements: card.measurements,
        source_sha256: card.source_sha256,
        base_version_id: this.latestId,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (saved) => {
          this.selected.set(saved);
          this.editing.set(false);
          this.draft.set(null);
          this.latestId = saved.id;
          this.busy.set(false);
          this.notice.set(
            `Версия ${saved.number} сохранена. Её можно скачать или использовать как основу следующей.`,
          );
          this.loadVersions();
        },
        error: (err) => {
          this.fail(err);
          if (err.status === 409) {
            this.dirty.set(true);
            this.loadVersions();
          }
        },
      });
  }
  export(format: 'md' | 'docx'): void {
    const card = this.selected();
    if (!card?.id || this.downloading()) return;
    this.downloading.set(true);
    this.error.set('');
    this.http
      .get(`${this.base}/${card.id}/export?format=${format}`, { responseType: 'blob' })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (blob) => {
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = `model-card-v${card.number}.${format}`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          this.downloading.set(false);
        },
        error: () => {
          this.error.set('Не удалось скачать карточку. Попробуйте ещё раз.');
          this.downloading.set(false);
        },
      });
  }
  changes(card: ModelCard): string {
    return card.changed_fields.map((key) => this.changeLabels[key] ?? key).join(', ');
  }
  private fail(err: { error?: { detail?: unknown } }): void {
    this.busy.set(false);
    this.error.set(
      apiErrorMessage(
        err.error?.detail,
        'Не удалось загрузить Model Card. Проверьте соединение и повторите попытку.',
      ),
    );
  }
}
