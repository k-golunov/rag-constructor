import { Component, Input, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import {
  AbstractControl,
  ReactiveFormsModule,
  FormBuilder,
  FormGroup,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { DatePipe } from '@angular/common';

import { ProjectService } from '../../../services/project.service';
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
  imports: [RouterLink, ReactiveFormsModule, DatePipe],
  templateUrl: './project-detail.component.html',
  styleUrl: './project-detail.component.css',
})
export class ProjectDetailComponent implements OnInit {
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

  constructor(
    private readonly projectService: ProjectService,
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
  }

  // ── Slider sync ────────────────────────────────────────────

  syncSlider(controlName: string, event: Event): void {
    const value = +(event.target as HTMLInputElement).value;
    this.form.get(controlName)?.setValue(value);
    this.form.get(controlName)?.markAsDirty();
  }
}
