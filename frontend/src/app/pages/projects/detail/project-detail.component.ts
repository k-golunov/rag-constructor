import { Component, Input, OnDestroy, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import {
  AbstractControl,
  ReactiveFormsModule,
  FormBuilder,
  FormGroup,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { DatePipe, SlicePipe } from '@angular/common';

import { ProjectService } from '../../../services/project.service';
import { UploadService, UploadSingleResponse, OperationStatus } from '../../../services/upload.service';
import { DataSourceService } from '../../../services/data-source.service';
import { ChatService, ChatMessage, ChatResponse } from '../../../services/chat.service';
import { DataSource } from '../../../models/data-source.model';
import {
  Project,
  ProjectUpdate,
  EMBEDDING_MODELS,
  LLM_MODELS,
  SPLIT_BY_OPTIONS,
  CHUNKING_STRATEGIES,
} from '../../../models/project.model';

export type TabId = 'settings' | 'documents' | 'chat';

/** Валидатор: если поле заполнено — должно начинаться с http:// или https:// */
function optionalUrlValidator(control: AbstractControl): ValidationErrors | null {
  const v = control.value as string;
  if (!v || v.trim() === '') return null;
  return /^https?:\/\/.+/.test(v) ? null : { invalidUrl: true };
}

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [RouterLink, ReactiveFormsModule, DatePipe, SlicePipe],
  templateUrl: './project-detail.component.html',
  styleUrl: './project-detail.component.css',
})
export class ProjectDetailComponent implements OnInit, OnDestroy {
  /** Route param injected via withComponentInputBinding() */
  @Input() id!: string;

  readonly embeddingModels    = EMBEDDING_MODELS;
  readonly llmModels          = LLM_MODELS;
  readonly splitByOptions     = SPLIT_BY_OPTIONS;
  readonly chunkingStrategies = CHUNKING_STRATEGIES;

  readonly tabs: { id: TabId; label: string; icon: string }[] = [
    { id: 'settings',  label: 'Настройки',  icon: 'tune'        },
    { id: 'documents', label: 'Документы',  icon: 'description' },
    { id: 'chat',      label: 'Чат',        icon: 'chat'        },
  ];

  // ── State signals ──────────────────────────────────────────
  project      = signal<Project | null>(null);
  activeTab    = signal<TabId>('settings');
  editMode     = signal<boolean>(false);
  loading      = signal<boolean>(true);
  loadError    = signal<string | null>(null);
  saving       = signal<boolean>(false);
  saveError    = signal<string | null>(null);
  saveSuccess  = signal<boolean>(false);
  showEmbedKey = signal<boolean>(false);
  showLlmKey   = signal<boolean>(false);

  // ── Delete state ────────────────────────────────────────────
  deleting      = signal<boolean>(false);
  deleteError   = signal<string | null>(null);

  // ── DataSources state ────────────────────────────────────────
  dataSources      = signal<DataSource[]>([]);
  dsLoading        = signal<boolean>(false);
  dsError          = signal<string | null>(null);
  deletingDsId     = signal<string | null>(null);

  // ── Chat state ───────────────────────────────────────────────
  chatMessages   = signal<ChatMessage[]>([]);
  chatSessionId  = signal<string | null>(null);
  chatInput      = signal<string>('');
  chatSending    = signal<boolean>(false);
  chatError      = signal<string | null>(null);

  // ── Upload state ────────────────────────────────────────────
  uploading       = signal<boolean>(false);
  uploadResult    = signal<UploadSingleResponse | null>(null);
  uploadError     = signal<string | null>(null);
  dragOver        = signal<boolean>(false);
  archiveStatus   = signal<OperationStatus | null>(null);

  private pollTimer: ReturnType<typeof setTimeout> | null = null;

  form!: FormGroup;

  // ── Computed display labels ────────────────────────────────

  get selectedEmbeddingLabel(): string {
    const val = this.form?.get('embedding_model')?.value as string;
    return this.embeddingModels.find(m => m.value === val)?.label ?? val ?? '—';
  }

  get selectedLlmLabel(): string {
    const val = this.form?.get('llm_model')?.value as string;
    return this.llmModels.find(m => m.value === val)?.label ?? val ?? '—';
  }

  get selectedSplitByLabel(): string {
    const val = this.form?.get('split_by')?.value as string;
    return this.splitByOptions.find(o => o.value === val)?.label ?? val ?? '—';
  }

  get selectedChunkingStrategyLabel(): string {
    const val = this.form?.get('chunking_strategy')?.value as string;
    return this.chunkingStrategies.find(o => o.value === val)?.label ?? val ?? '—';
  }

  private successTimer: ReturnType<typeof setTimeout> | null = null;

  ngOnDestroy(): void {
    if (this.successTimer) clearTimeout(this.successTimer);
    if (this.pollTimer) clearTimeout(this.pollTimer);
  }

  constructor(
    private readonly projectService: ProjectService,
    private readonly uploadService: UploadService,
    private readonly dataSourceService: DataSourceService,
    private readonly chatService: ChatService,
    private readonly fb: FormBuilder,
    private readonly router: Router,
  ) {}

  ngOnInit(): void {
    this.buildForm();
    this.loadProject();
  }

  // ── Form ───────────────────────────────────────────────────

  private buildForm(): void {
    this.form = this.fb.group({
      // ── Основное ──────────────────────────────────────────
      name: ['', [Validators.required, Validators.maxLength(255)]],

      // ── Чанкинг ───────────────────────────────────────────
      chunk_size:        [800,          [Validators.required, Validators.min(100), Validators.max(8000)]],
      chunk_overlap:     [100,          [Validators.required, Validators.min(0),   Validators.max(2000)]],
      split_by:          ['paragraphs', Validators.required],
      chunking_strategy: ['recursive',  Validators.required],
      extract_tables:    [false],

      // ── Эмбеддинги ────────────────────────────────────────
      embedding_model:     ['', Validators.required],
      embedding_dimension: [1536, [Validators.required, Validators.min(1)]],
      embedding_api_key:   [null],
      embedding_api_url:   [null, optionalUrlValidator],

      // ── LLM ───────────────────────────────────────────────
      llm_model:   ['', Validators.required],
      llm_api_key: [null],
      llm_api_url: [null, optionalUrlValidator],

      // ── Промпт ────────────────────────────────────────────
      system_prompt: ['', Validators.required],
    });
    // Starts in readonly mode
    this.form.disable();
  }

  private patchForm(p: Project): void {
    this.form.patchValue({
      name:                p.name,
      chunk_size:          p.chunk_size,
      chunk_overlap:       p.chunk_overlap,
      split_by:            p.split_by,
      chunking_strategy:   p.chunking_strategy,
      extract_tables:      p.extract_tables,
      embedding_model:     p.embedding_model,
      embedding_dimension: p.embedding_dimension,
      embedding_api_key:   p.embedding_api_key,
      embedding_api_url:   p.embedding_api_url,
      llm_model:           p.llm_model,
      llm_api_key:         p.llm_api_key,
      llm_api_url:         p.llm_api_url,
      system_prompt:       p.system_prompt,
    });
    this.form.markAsPristine();
  }

  get f() { return this.form.controls; }

  // ── Data loading ───────────────────────────────────────────

  loadProject(): void {
    this.loading.set(true);
    this.loadError.set(null);

    this.projectService.getProject(this.id).subscribe({
      next: (p) => {
        this.project.set(p);
        this.patchForm(p);
        this.loading.set(false);
        this.form.disable();
        this.editMode.set(false);
      },
      error: () => {
        this.loadError.set('Не удалось загрузить проект. Проверьте соединение с сервером.');
        this.loading.set(false);
      },
    });
  }

  // ── Edit mode ──────────────────────────────────────────────

  onEdit(): void {
    this.saveError.set(null);
    this.saveSuccess.set(false);
    this.editMode.set(true);
    this.form.enable();
  }

  onCancel(): void {
    const p = this.project();
    if (p) this.patchForm(p);
    this.form.disable();
    this.editMode.set(false);
    this.saveError.set(null);
    this.showEmbedKey.set(false);
    this.showLlmKey.set(false);
  }

  // ── Save ───────────────────────────────────────────────────

  onSave(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.saving.set(true);
    this.saveError.set(null);
    this.saveSuccess.set(false);

    // Send only changed fields
    const raw     = this.form.getRawValue();
    const current = this.project()!;
    const update: ProjectUpdate = {};

    (Object.keys(raw) as (keyof ProjectUpdate)[]).forEach((key) => {
      if (raw[key] !== (current as unknown as Record<string, unknown>)[key]) {
        (update as Record<string, unknown>)[key] = raw[key];
      }
    });

    this.projectService.updateProject(this.id, update).subscribe({
      next: (updated) => {
        this.project.set(updated);
        this.patchForm(updated);
        this.form.disable();
        this.editMode.set(false);
        this.saving.set(false);
        this.saveSuccess.set(true);
        this.showEmbedKey.set(false);
        this.showLlmKey.set(false);
        if (this.successTimer) clearTimeout(this.successTimer);
        this.successTimer = setTimeout(() => this.saveSuccess.set(false), 3000);
      },
      error: (err) => {
        const detail: string =
          err?.error?.detail ?? 'Не удалось сохранить изменения. Попробуйте позже.';
        this.saveError.set(detail);
        this.saving.set(false);
      },
    });
  }

  // ── Tabs ───────────────────────────────────────────────────

  setTab(tab: TabId): void {
    this.activeTab.set(tab);
    if (tab === 'documents') this.loadDataSources();
  }

  // ── DataSources ────────────────────────────────────────────

  loadDataSources(): void {
    this.dsLoading.set(true);
    this.dsError.set(null);
    this.dataSourceService.getDataSources(this.id).subscribe({
      next: (res) => {
        this.dataSources.set(res.items);
        this.dsLoading.set(false);
      },
      error: () => {
        this.dsError.set('Не удалось загрузить список документов.');
        this.dsLoading.set(false);
      },
    });
  }

  deleteDataSource(dsId: string): void {
    if (!confirm('Удалить этот источник данных?')) return;
    this.deletingDsId.set(dsId);
    this.dataSourceService.deleteDataSource(dsId).subscribe({
      next: () => {
        this.dataSources.update(list => list.filter(d => d.id !== dsId));
        this.deletingDsId.set(null);
      },
      error: () => {
        this.deletingDsId.set(null);
      },
    });
  }

  // ── Chat ───────────────────────────────────────────────────

  setChatInput(event: Event): void {
    this.chatInput.set((event.target as HTMLTextAreaElement).value);
  }

  onChatKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  sendMessage(): void {
    const question = this.chatInput().trim();
    if (!question || this.chatSending()) return;

    this.chatMessages.update(msgs => [...msgs, { role: 'user', content: question }]);
    this.chatInput.set('');
    this.chatSending.set(true);
    this.chatError.set(null);

    this.chatService.sendMessage(this.id, question, this.chatSessionId()).subscribe({
      next: (res: ChatResponse) => {
        this.chatSessionId.set(res.session_id);
        this.chatMessages.update(msgs => [...msgs, { role: 'assistant', content: res.answer }]);
        this.chatSending.set(false);
      },
      error: (err) => {
        this.chatMessages.update(msgs => msgs.slice(0, -1));
        this.chatError.set(err?.error?.detail ?? 'Ошибка при отправке сообщения.');
        this.chatSending.set(false);
      },
    });
  }

  newChatSession(): void {
    this.chatSessionId.set(null);
    this.chatMessages.set([]);
    this.chatError.set(null);
  }

  // ── Slider sync ────────────────────────────────────────────

  syncSlider(controlName: string, event: Event): void {
    const value = +(event.target as HTMLInputElement).value;
    this.form.get(controlName)?.setValue(value);
    this.form.get(controlName)?.markAsDirty();
  }

  // ── Delete ─────────────────────────────────────────────────

  onDelete(): void {
    if (!confirm(`Удалить проект «${this.project()!.name}»? Это действие необратимо.`)) return;
    this.deleting.set(true);
    this.deleteError.set(null);
    this.projectService.deleteProject(this.id).subscribe({
      next: () => this.router.navigate(['/projects']),
      error: () => {
        this.deleteError.set('Не удалось удалить проект. Попробуйте ещё раз.');
        this.deleting.set(false);
      },
    });
  }

  // ── Upload ─────────────────────────────────────────────────

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(true);
  }

  onDragLeave(): void {
    this.dragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) this.uploadFile(file);
  }

  onFileSelected(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (file) this.uploadFile(file);
  }

  private uploadFile(file: File): void {
    this.uploading.set(true);
    this.uploadResult.set(null);
    this.uploadError.set(null);
    this.archiveStatus.set(null);
    if (this.pollTimer) clearTimeout(this.pollTimer);

    const isZip = file.name.toLowerCase().endsWith('.zip');

    if (isZip) {
      this.uploadService.uploadArchive(this.id, file).subscribe({
        next: ({ operation_id }) => this.pollArchiveStatus(operation_id),
        error: (err) => {
          this.uploadError.set(err?.error?.detail ?? 'Ошибка загрузки архива.');
          this.uploading.set(false);
        },
      });
    } else {
      this.uploadService.uploadFile(this.id, file).subscribe({
        next: (result) => {
          this.uploadResult.set(result);
          this.uploading.set(false);
          this.loadDataSources();
        },
        error: (err) => {
          this.uploadError.set(err?.error?.detail ?? 'Ошибка загрузки файла. Попробуйте ещё раз.');
          this.uploading.set(false);
        },
      });
    }
  }

  private pollArchiveStatus(operationId: string): void {
    this.uploadService.getOperationStatus(operationId).subscribe({
      next: (status) => {
        this.archiveStatus.set(status);
        if (status.status === 'processing') {
          this.pollTimer = setTimeout(() => this.pollArchiveStatus(operationId), 2000);
        } else {
          this.uploading.set(false);
        }
      },
      error: () => {
        this.uploadError.set('Не удалось получить статус операции.');
        this.uploading.set(false);
      },
    });
  }
}
