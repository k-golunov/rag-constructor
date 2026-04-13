import { Component, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { AbstractControl, ReactiveFormsModule, FormBuilder, FormGroup, ValidationErrors, Validators } from '@angular/forms';

import { ProjectService } from '../../../services/project.service';
import {
  CHUNKING_STRATEGIES,
  EMBEDDING_MODELS,
  LLM_MODELS,
  ProjectCreate,
  SPLIT_BY_OPTIONS,
} from '../../../models/project.model';

/** Валидатор: если поле заполнено — должно начинаться с http:// или https:// */
function optionalUrlValidator(control: AbstractControl): ValidationErrors | null {
  const v = control.value as string;
  if (!v || v.trim() === '') return null;
  return /^https?:\/\/.+/.test(v) ? null : { invalidUrl: true };
}

@Component({
  selector: 'app-project-create',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './project-create.component.html',
  styleUrl: './project-create.component.css',
})
export class ProjectCreateComponent {
  readonly embeddingModels    = EMBEDDING_MODELS;
  readonly llmModels          = LLM_MODELS;
  readonly splitByOptions     = SPLIT_BY_OPTIONS;
  readonly chunkingStrategies = CHUNKING_STRATEGIES;

  readonly DEFAULT_PROMPT =
    'Вы полезный ассистент, отвечающий на вопросы по документам.';

  submitting   = signal<boolean>(false);
  serverError  = signal<string | null>(null);
  showEmbedKey = signal<boolean>(false);
  showLlmKey   = signal<boolean>(false);

  form: FormGroup;

  constructor(
    private readonly fb: FormBuilder,
    private readonly projectService: ProjectService,
    private readonly router: Router,
  ) {
    this.form = this.fb.group({
      // ── Основное ────────────────────────────────────────────
      name: ['', [Validators.required, Validators.maxLength(255)]],

      // ── Чанкинг ─────────────────────────────────────────────
      chunk_size:        [800,          [Validators.required, Validators.min(100), Validators.max(8000)]],
      chunk_overlap:     [100,          [Validators.required, Validators.min(0),   Validators.max(2000)]],
      split_by:          ['paragraphs', Validators.required],
      chunking_strategy: ['recursive',  Validators.required],
      extract_tables:    [false],

      // ── Эмбеддинги ──────────────────────────────────────────
      embedding_model:     ['text-embedding-3-small', Validators.required],
      embedding_dimension: [1536, [Validators.required, Validators.min(1)]],
      embedding_api_key:   [null],
      embedding_api_url:   [null, optionalUrlValidator],

      // ── LLM ─────────────────────────────────────────────────
      llm_model:   ['gpt-4o-mini', Validators.required],
      llm_api_key: [null],
      llm_api_url: [null, optionalUrlValidator],

      // ── Промпт ──────────────────────────────────────────────
      system_prompt: [this.DEFAULT_PROMPT, Validators.required],
    });
  }

  get f() { return this.form.controls; }

  onSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.serverError.set(null);

    const payload: ProjectCreate = this.form.getRawValue() as ProjectCreate;

    this.projectService.createProject(payload).subscribe({
      next: (created) => {
        this.router.navigate(['/projects', created.id]);
      },
      error: (err) => {
        const detail: string =
          err?.error?.detail ?? 'Не удалось создать проект. Попробуйте позже.';
        this.serverError.set(detail);
        this.submitting.set(false);
      },
    });
  }

  onCancel(): void {
    this.router.navigate(['/projects']);
  }
}
