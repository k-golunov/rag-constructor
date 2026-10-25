import { Component, Input, OnInit, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { Observable, finalize } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { apiErrorMessage } from '../../../models/settings-validation';

interface Evidence {
  document_version_id: string;
  quote: string;
  start_char?: number | null;
  end_char?: number | null;
  file_name?: string;
}
interface Example {
  id: string;
  question: string;
  reference_answer: string | null;
  evidence: Evidence[];
  category: string | null;
  unanswerable: boolean;
  split: 'tuning' | 'test';
  review: { status: string; reviewer: string | null; reviewed_at: string | null };
}
interface VersionSummary {
  id: string;
  number: number;
  review_status: string;
}
interface SetVersion extends VersionSummary {
  examples: Example[];
  documents: { document_version_id: string; file_name: string; sha256: string }[];
  sha256: string;
}
interface SetSummary {
  id: string;
  name: string;
  latest_version: VersionSummary;
}

@Component({
  selector: 'app-question-sets',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './question-sets.component.html',
  styleUrl: './question-sets.component.css',
})
export class QuestionSetsComponent implements OnInit {
  @Input({ required: true }) projectId!: string;
  name = '';
  description = '';
  setId = '';
  reviewer = '';
  filter = 'all';
  importFile: File | null = null;
  sets = signal<SetSummary[]>([]);
  versions = signal<VersionSummary[]>([]);
  moreSets = signal(false);
  moreVersions = signal(false);
  version = signal<SetVersion | null>(null);
  error = signal<string | null>(null);
  busy = signal(false);
  private pending = 0;
  answer = signal<{ answer: string; run_id: string; version: number } | null>(null);
  editingId: string | null = null;
  question = '';
  reference = '';
  category = '';
  split: 'tuning' | 'test' = 'tuning';
  unanswerable = false;
  evidence: Evidence[] = [];
  evidenceDocument = '';
  evidenceQuote = '';
  evidenceStart: number | null = null;

  constructor(private http: HttpClient) {}
  ngOnInit(): void {
    this.load();
  }
  get base(): string {
    return `${environment.apiBaseUrl}/projects/${this.projectId}/question-sets`;
  }
  get current(): string {
    return `${this.base}/${this.setId}`;
  }
  get latest(): boolean {
    return this.version()?.id === this.versions()[0]?.id;
  }

  request<T>(call: Observable<T>, done: (value: T) => void): void {
    this.pending++;
    this.busy.set(true);
    this.error.set(null);
    call
      .pipe(
        finalize(() => {
          this.pending--;
          this.busy.set(this.pending > 0);
        }),
      )
      .subscribe({
        next: done,
        error: (err) =>
          this.error.set(apiErrorMessage(err?.error?.detail, 'Не удалось выполнить действие.')),
      });
  }
  load(): void {
    this.request(this.http.get<SetSummary[]>(this.base), (sets) => {
      this.sets.set(sets);
      this.moreSets.set(sets.length === 100);
      if (sets.length && !sets.some((set) => set.id === this.setId)) this.selectSet(sets[0].id);
    });
  }
  loadMoreSets(): void {
    this.request(this.http.get<SetSummary[]>(`${this.base}?skip=${this.sets().length}`), (sets) => {
      this.sets.update((previous) => [...previous, ...sets]);
      this.moreSets.set(sets.length === 100);
    });
  }
  loadMoreVersions(): void {
    this.request(
      this.http.get<VersionSummary[]>(`${this.current}/versions?skip=${this.versions().length}`),
      (versions) => {
        this.versions.update((previous) => [...previous, ...versions]);
        this.moreVersions.set(versions.length === 100);
      },
    );
  }
  selectSet(id: string): void {
    this.setId = id;
    this.version.set(null);
    this.answer.set(null);
    this.request(this.http.get<VersionSummary[]>(`${this.current}/versions`), (versions) => {
      this.versions.set(versions);
      this.moreVersions.set(versions.length === 100);
      if (versions.length) this.selectVersion(versions[0].id);
    });
  }
  selectVersion(id: string): void {
    this.request(this.http.get<SetVersion>(`${this.current}/versions/${id}`), (version) => {
      this.version.set(version);
      this.clearEditor();
      this.answer.set(null);
    });
  }
  adopt(version: SetVersion): void {
    this.version.set(version);
    this.clearEditor();
    this.answer.set(null);
    this.request(this.http.get<VersionSummary[]>(`${this.current}/versions`), (versions) => {
      this.versions.set(versions);
      this.moreVersions.set(versions.length === 100);
    });
  }
  createSet(): void {
    if (!this.name.trim()) {
      this.error.set('Укажите название набора.');
      return;
    }
    this.request(
      this.http.post<{ id: string; version: SetVersion }>(this.base, {
        name: this.name,
        description: this.description,
      }),
      (created) => {
        this.setId = created.id;
        this.name = '';
        this.description = '';
        this.adopt(created.version);
        this.load();
      },
    );
  }
  clearEditor(): void {
    this.editingId = null;
    this.question = '';
    this.reference = '';
    this.category = '';
    this.split = 'tuning';
    this.unanswerable = false;
    this.evidence = [];
    this.evidenceDocument = this.version()?.documents[0]?.document_version_id ?? '';
    this.evidenceQuote = '';
    this.evidenceStart = null;
  }
  edit(example: Example): void {
    this.editingId = example.id;
    this.question = example.question;
    this.reference = example.reference_answer ?? '';
    this.category = example.category ?? '';
    this.split = example.split;
    this.unanswerable = example.unanswerable;
    this.evidence = example.evidence.map((proof) => this.proofInput(proof));
  }
  proofInput(proof: Evidence): Evidence {
    return {
      document_version_id: proof.document_version_id,
      quote: proof.quote,
      start_char: proof.start_char ?? null,
      end_char: proof.end_char ?? null,
    };
  }
  exampleInput(example: Example) {
    return {
      id: example.id,
      question: example.question,
      reference_answer: example.reference_answer,
      category: example.category,
      split: example.split,
      unanswerable: example.unanswerable,
      evidence: example.evidence.map((proof) => this.proofInput(proof)),
    };
  }
  addEvidence(): void {
    if (!this.evidenceDocument || !this.evidenceQuote.trim()) {
      this.error.set('Выберите документ и вставьте точную выдержку.');
      return;
    }
    this.evidence = [
      ...this.evidence,
      {
        document_version_id: this.evidenceDocument,
        quote: this.evidenceQuote,
        start_char: this.evidenceStart,
      },
    ];
    this.evidenceQuote = '';
    this.evidenceStart = null;
  }
  removeEvidence(index: number): void {
    this.evidence = this.evidence.filter((_, position) => position !== index);
  }
  saveExample(): void {
    if (!this.question.trim()) {
      this.error.set('Введите вопрос.');
      return;
    }
    const version = this.version();
    if (!version || !this.latest) return;
    const example = {
      question: this.question,
      reference_answer: this.reference || null,
      category: this.category || null,
      split: this.split,
      unanswerable: this.unanswerable,
      evidence: this.evidence,
    };
    const call = this.editingId
      ? this.http.patch<SetVersion>(`${this.current}/examples/${this.editingId}`, {
          base_version_id: version.id,
          ...example,
        })
      : this.http.post<SetVersion>(`${this.current}/examples`, {
          base_version_id: version.id,
          example,
        });
    this.request(call, (updated) => this.adopt(updated));
  }
  review(example: Example, status: string): void {
    const version = this.version();
    if (!version || !this.latest) return;
    if (!this.reviewer.trim()) {
      this.error.set('Укажите имя проверяющего.');
      return;
    }
    this.request(
      this.http.post<SetVersion>(`${this.current}/examples/${example.id}/review`, {
        base_version_id: version.id,
        status,
        reviewer: this.reviewer,
      }),
      (updated) => this.adopt(updated),
    );
  }
  remove(example: Example): void {
    const version = this.version();
    if (!version || !this.latest) return;
    this.request(
      this.http.delete<SetVersion>(
        `${this.current}/examples/${example.id}?base_version_id=${version.id}`,
      ),
      (updated) => this.adopt(updated),
    );
  }
  chooseFile(event: Event): void {
    this.importFile = (event.target as HTMLInputElement).files?.[0] ?? null;
  }
  importQuestions(): void {
    const version = this.version();
    if (!version || !this.importFile || !this.latest) return;
    const body = new FormData();
    body.append('file', this.importFile);
    this.request(
      this.http.post<SetVersion>(`${this.current}/import?base_version_id=${version.id}`, body),
      (updated) => this.adopt(updated),
    );
  }
  exportQuestions(): void {
    const version = this.version();
    if (!version) return;
    this.request(this.http.get(`${this.current}/versions/${version.id}/export`), (data) => {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = `questions-v${version.number}.json`;
      link.click();
      URL.revokeObjectURL(url);
    });
  }
  refreshDocuments(): void {
    const version = this.version();
    if (!version || !this.latest) return;
    this.request(
      this.http.post<SetVersion>(`${this.current}/versions`, {
        base_version_id: version.id,
        refresh_documents: true,
        examples: version.examples.map((example) => ({
          ...this.exampleInput(example),
          evidence: [],
        })),
      }),
      (updated) => this.adopt(updated),
    );
  }
  run(example: Example): void {
    const version = this.version();
    if (!version) return;
    this.request(
      this.http.post<{ answer: string; run_id: string }>(
        `${environment.apiBaseUrl}/projects/${this.projectId}/chat`,
        {
          question: example.question,
          question_set_version_id: version.id,
          question_example_id: example.id,
        },
      ),
      (result) => this.answer.set({ ...result, version: version.number }),
    );
  }
  visibleExamples(): Example[] {
    return (
      this.version()?.examples.filter(
        (example) => this.filter === 'all' || example.split === this.filter,
      ) ?? []
    );
  }
  documentName(id: string): string {
    return (
      this.version()?.documents.find((document) => document.document_version_id === id)
        ?.file_name ?? 'Документ'
    );
  }
  reviewLabel(status: string): string {
    return status === 'approved'
      ? 'Проверен человеком'
      : status === 'rejected'
        ? 'Нужно исправить'
        : 'Не проверен';
  }
}
