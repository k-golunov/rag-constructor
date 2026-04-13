import { Component, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';

import { ProjectService } from '../../../services/project.service';
import { EMBEDDING_MODELS, LLM_MODELS, ProjectCreate } from '../../../models/project.model';

@Component({
  selector: 'app-project-create',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './project-create.component.html',
  styleUrl: './project-create.component.css',
})
export class ProjectCreateComponent {
  readonly embeddingModels = EMBEDDING_MODELS;
  readonly llmModels        = LLM_MODELS;

  readonly DEFAULT_PROMPT =
    'Вы полезный ассистент, отвечающий на вопросы по документам.';

  submitting = signal<boolean>(false);
  serverError = signal<string | null>(null);

  form: FormGroup;

  constructor(
    private readonly fb: FormBuilder,
    private readonly projectService: ProjectService,
    private readonly router: Router,
  ) {
    this.form = this.fb.group({
      name:            ['', [Validators.required, Validators.maxLength(255)]],
      chunk_size:      [800,  [Validators.required, Validators.min(100), Validators.max(8000)]],
      chunk_overlap:   [100,  [Validators.required, Validators.min(0),   Validators.max(2000)]],
      embedding_model: ['text-embedding-3-small', Validators.required],
      llm_model:       ['gpt-4o-mini',             Validators.required],
      system_prompt:   [this.DEFAULT_PROMPT,       Validators.required],
    });
  }

  /** Удобный доступ к контролям для шаблона. */
  get f() {
    return this.form.controls;
  }

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
