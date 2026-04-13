import { Component, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { DatePipe, SlicePipe } from '@angular/common';

import { ProjectService } from '../../../services/project.service';
import { Project } from '../../../models/project.model';

@Component({
  selector: 'app-project-list',
  standalone: true,
  imports: [RouterLink, DatePipe, SlicePipe],
  templateUrl: './project-list.component.html',
  styleUrl: './project-list.component.css',
})
export class ProjectListComponent implements OnInit {
  projects = signal<Project[]>([]);
  total    = signal<number>(0);
  loading  = signal<boolean>(true);
  error    = signal<string | null>(null);

  constructor(
    private readonly projectService: ProjectService,
    private readonly router: Router,
  ) {}

  ngOnInit(): void {
    this.loadProjects();
  }

  loadProjects(): void {
    this.loading.set(true);
    this.error.set(null);

    this.projectService.getProjects().subscribe({
      next: (response) => {
        this.projects.set(response.items);
        this.total.set(response.total);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Не удалось загрузить проекты. Проверьте соединение с сервером.');
        this.loading.set(false);
      },
    });
  }

  navigateToProject(id: string): void {
    this.router.navigate(['/projects', id]);
  }

  /** Кол-во слов в системном промпте как грубая оценка сложности. */
  promptPreview(prompt: string): string {
    return prompt.length > 80 ? prompt.slice(0, 80) + '…' : prompt;
  }
}
